import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from config import Settings
from models import AIPlayerDossier, AIPlayerDossierRun, AppSetting, PlayerData, PlayerSession
from services_ai_dossier import (
    build_kobold_request,
    dossier_evidence_link,
    dossier_run_public_dict,
    dossier_response_with_run,
    get_dossier_config,
    get_dossier_prompt,
    validate_dossier_ca_pem,
    normalize_dossier_result,
    should_queue_dossier,
    select_active_player_ids,
    dossier_input_hash,
    sanitize_dossier_result,
)
import services_ai_dossier


class AIDossierContractTests(unittest.TestCase):
    def test_normalize_result_requires_schema_and_does_not_invent_fields(self):
        result = normalize_dossier_result({
            "summary": "Опытный игрок; подтверждённых нарушений нет.",
            "facts": [{"text": "Сыграл 12 раундов", "source": "stats"}],
            "signals": [],
            "caveats": ["Логи доступны не полностью"],
            "confidence": "medium",
            "evidence": [{"kind": "stats", "id": "player:7"}],
            "unexpected": "drop me",
        })

        self.assertEqual(result["confidence"], "medium")
        self.assertNotIn("unexpected", result)
        self.assertEqual(result["facts"][0]["text"], "Сыграл 12 раундов")

    def test_normalize_result_rejects_missing_or_invalid_confidence(self):
        with self.assertRaises(ValueError):
            normalize_dossier_result({"summary": "x"})
        with self.assertRaises(ValueError):
            normalize_dossier_result({
                "summary": "x", "facts": [], "signals": [], "caveats": [],
                "confidence": "certain", "evidence": [],
            })

    def test_kobold_request_has_bearer_auth_and_json_only_contract(self):
        request = build_kobold_request(
            {"player_id": 7, "missing": ["logs"]},
            api_key="secret",
            model="qwen2.5-3b-instruct",
        )

        self.assertEqual(request["headers"]["Authorization"], "Bearer secret")
        self.assertFalse(request["json"]["stream"])
        self.assertEqual(request["json"]["max_tokens"], 650)
        self.assertIn("player_id", request["json"]["messages"][1]["content"])

    def test_should_queue_only_when_changed_or_stale_unless_forced(self):
        now = datetime(2026, 9, 4, 12, 0)
        successful = AIPlayerDossier(
            status="succeeded",
            input_hash="same",
            generated_at=now - timedelta(days=2),
        )
        self.assertFalse(should_queue_dossier(successful, "same", False, now, 7))
        self.assertTrue(should_queue_dossier(successful, "new", False, now, 7))
        self.assertTrue(should_queue_dossier(successful, "same", False, now, 1))
        self.assertTrue(should_queue_dossier(successful, "same", True, now, 365))

    def test_input_hash_ignores_only_the_moving_window_end(self):
        first = {"profile": {"player_id": 7}, "window": {"from": "2026-08-01", "to": "2026-09-04T12:00:00"}}
        second = {"profile": {"player_id": 7}, "window": {"from": "2026-08-01", "to": "2026-09-04T13:00:00"}}
        self.assertEqual(dossier_input_hash(first), dossier_input_hash(second))

    def test_sanitize_hides_identifiers_and_drops_unknown_evidence(self):
        payload = {"profile": {"player_id": 7, "steamid": "7656119", "discord_id": "123456789", "last_ip": "10.0.0.1", "known_ips": []}, "logs": [{"id": 42}]}
        result = sanitize_dossier_result({
            "summary": "IP 10.0.0.1 найден",
            "facts": [{"text": "Steam 7656119", "source": "profile"}],
            "signals": [], "caveats": [], "confidence": "low",
            "evidence": [{"kind": "log", "id": 42}, {"kind": "log", "id": 999}],
        }, payload)
        self.assertNotIn("10.0.0.1", result["summary"])
        self.assertEqual(result["evidence"], [{"kind": "log", "id": 42}])

    def test_active_player_selection_is_recent_login_desc_then_id(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[PlayerData.__table__, PlayerSession.__table__])
        db = Session(engine)
        try:
            db.add_all([
                PlayerData(player_id=2, player_steamid="2", player_last_time_name="two", last_login_time=datetime(2026, 9, 3)),
                PlayerData(player_id=1, player_steamid="1", player_last_time_name="one", last_login_time=datetime(2026, 9, 3)),
                PlayerData(player_id=3, player_steamid="3", player_last_time_name="old", last_login_time=datetime(2026, 7, 1)),
            ])
            db.commit()
            self.assertEqual(select_active_player_ids(db, datetime(2026, 9, 4), 10), [1, 2])
        finally:
            db.close()
            engine.dispose()

    def test_enqueue_is_idempotent_for_running_player(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AIPlayerDossier.__table__, AIPlayerDossierRun.__table__])
        db = Session(engine)
        try:
            first, created = services_ai_dossier.enqueue_dossier(db, 7, 11, force=True)
            second, created_again = services_ai_dossier.enqueue_dossier(db, 7, 12, force=True)
            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertEqual(first.id, second.id)
            self.assertEqual(second.requested_by_admin_id, 11)
            self.assertEqual(db.query(AIPlayerDossierRun).count(), 1)
            self.assertEqual(db.query(AIPlayerDossierRun).one().source, "manual")
        finally:
            db.close()
            engine.dispose()

    def test_generation_failure_keeps_previous_successful_result(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AIPlayerDossier.__table__, AIPlayerDossierRun.__table__])
        db = Session(engine)
        try:
            report = AIPlayerDossier(
                player_id=7,
                status="queued",
                result_json={"summary": "старый", "facts": [], "signals": [], "caveats": [], "confidence": "high", "evidence": []},
                summary_text="старый",
                input_hash="old",
                generated_at=datetime(2026, 9, 1),
            )
            db.add(report)
            db.commit()

            fake_settings = SimpleNamespace(
                ai_dossier_url="https://qwen.test/v1/chat/completions",
                ai_dossier_api_key="secret",
                ai_dossier_model="qwen",
                ai_dossier_tls_ca_file="",
                ai_dossier_timeout_seconds=1,
                ai_dossier_active_days=30,
                ai_dossier_max_log_events=10,
                ai_dossier_max_input_chars=30000,
            )
            with (
                patch("database.SessionLocal", return_value=db),
                patch.object(services_ai_dossier, "build_player_dossier_payload", return_value={"player_id": 7}),
                patch.object(services_ai_dossier, "get_settings", return_value=fake_settings),
                patch("httpx.Client.post", side_effect=RuntimeError("qwen offline")),
            ):
                result = services_ai_dossier.process_one_dossier()

            self.assertEqual(result["status"], "failed")
            db.expire_all()
            stored = db.query(AIPlayerDossier).one()
            self.assertEqual(stored.status, "failed")
            self.assertEqual(stored.summary_text, "старый")
            self.assertEqual(stored.result_json["summary"], "старый")
            self.assertIn("qwen offline", stored.error_message)
        finally:
            db.close()
            engine.dispose()

    def test_evidence_links_are_derived_from_whitelisted_source_ids(self):
        self.assertEqual(dossier_evidence_link("log", 42, 7), "/logs?event_id=42")
        self.assertEqual(dossier_evidence_link("note", 3, 7), "/players/7?tab=notes&source_id=3")
        self.assertIsNone(dossier_evidence_link("note", 999, None))

    def test_run_public_dict_contains_metadata_but_no_response_body(self):
        run = AIPlayerDossierRun(
            id=4, player_id=7, source="manual", status="succeeded", model="qwen",
            latency_ms=1200, prompt_tokens=100, completion_tokens=20,
        )
        public = dossier_run_public_dict(run, player_name="Alice")
        self.assertEqual(public["player_name"], "Alice")
        self.assertEqual(public["latency_ms"], 1200)
        self.assertNotIn("result", public)
        self.assertNotIn("raw_response", public)

    def test_empty_dossier_response_does_not_require_a_report_row(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AIPlayerDossierRun.__table__])
        db = Session(engine)
        try:
            response = dossier_response_with_run(db, None)
            self.assertEqual(response, {"status": "none", "result": None, "run": None})
        finally:
            db.close()
            engine.dispose()

    def test_runtime_config_reads_safe_operational_overrides(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AppSetting.__table__])
        db = Session(engine)
        try:
            db.add_all([
                AppSetting(key="ai_dossier_schedule_hour", value="6"),
                AppSetting(key="ai_dossier_active_days", value="14"),
                AppSetting(key="ai_dossier_max_log_events", value="40"),
            ])
            db.commit()
            with patch.object(services_ai_dossier, "get_settings", return_value=SimpleNamespace(
                ai_dossier_url="", ai_dossier_api_key="", ai_dossier_model="qwen",
                ai_dossier_daily_limit=10, ai_dossier_schedule_hour=4,
                ai_dossier_active_days=30, ai_dossier_max_log_events=120,
                ai_dossier_timeout_seconds=180, ai_dossier_tls_ca_file="",
                ai_dossier_max_input_chars=30000,
            )):
                config = get_dossier_config(db)
            self.assertEqual(config["schedule_hour"], 6)
            self.assertEqual(config["active_days"], 14)
            self.assertEqual(config["max_log_events"], 40)
        finally:
            db.close()
            engine.dispose()

    def test_prompt_can_be_overridden_by_superuser_setting(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AppSetting.__table__])
        db = Session(engine)
        try:
            db.add(AppSetting(key="ai_dossier_prompt", value="custom prompt"))
            db.commit()
            self.assertEqual(get_dossier_prompt(db), "custom prompt")
        finally:
            db.close()
            engine.dispose()

    def test_ca_upload_accepts_certificate_and_rejects_private_key(self):
        self.assertTrue(validate_dossier_ca_pem(
            b"-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
        ))
        with self.assertRaises(ValueError):
            validate_dossier_ca_pem(b"-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n")

    def test_empty_numeric_env_values_fall_back_to_defaults(self):
        settings = Settings(
            rate_limit_requests="", rate_limit_window="",
            sftp_server_1_port="", sftp_server_2_port="",
        )
        self.assertEqual(settings.rate_limit_requests, 100)
        self.assertEqual(settings.rate_limit_window, 60)
        self.assertEqual(settings.sftp_server_1_port, 22)
        self.assertEqual(settings.sftp_server_2_port, 22)


if __name__ == "__main__":
    unittest.main()
