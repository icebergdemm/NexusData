import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import AppSetting
import services_llm_monster
from services_llm_monster import (
    QueueBusyError,
    LLMError,
    _active_run_live,
    _process_server_llm_queue,
    get_llm_runtime,
    queue_llm_run,
)


def _configured_settings():
    return SimpleNamespace(
        ai_dossier_url="https://ai.example/v1/chat/completions",
        ai_dossier_api_key="test-key",
        ai_dossier_tls_ca_file="",
        monster_llm_model="qwen3-4b-instruct",
        monster_llm_timeout_seconds=90.0,
        monster_llm_temperature=0.15,
        monster_llm_top_p=0.8,
        monster_llm_auto_interval_minutes=0,
    )


def _main_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[AppSetting.__table__])
    db = Session(engine)
    db._test_engine = engine
    return db


def _close(db):
    db.close()
    db._test_engine.dispose()


class MonsterLLMQueueTests(unittest.TestCase):
    def test_queue_stores_pending_run(self):
        db = _main_db()
        try:
            with patch.object(services_llm_monster, "get_settings", return_value=_configured_settings()):
                run = queue_llm_run(db, "ASPER1", mode="spawn", instruction="давление", admin_id=7)
            self.assertEqual(run["mode"], "spawn")
            self.assertEqual(run["instruction"], "давление")
            self.assertEqual(run["requested_by"], 7)
            self.assertEqual(get_llm_runtime(db, "ASPER1")["pending_run"]["id"], run["id"])
        finally:
            _close(db)

    def test_queue_truncates_long_instruction(self):
        db = _main_db()
        try:
            with patch.object(services_llm_monster, "get_settings", return_value=_configured_settings()):
                run = queue_llm_run(db, "ASPER1", instruction="x" * 5000)
            self.assertEqual(len(run["instruction"]), 2000)
        finally:
            _close(db)

    def test_second_queue_conflicts_while_pending(self):
        db = _main_db()
        try:
            with patch.object(services_llm_monster, "get_settings", return_value=_configured_settings()):
                queue_llm_run(db, "ASPER1", mode="analyze")
                with self.assertRaises(QueueBusyError):
                    queue_llm_run(db, "ASPER1", mode="apply")
        finally:
            _close(db)

    def test_queue_refuses_unknown_mode_and_dead_aggregator(self):
        db = _main_db()
        try:
            with patch.object(services_llm_monster, "get_settings", return_value=_configured_settings()):
                with self.assertRaises(LLMError):
                    queue_llm_run(db, "ASPER1", mode="turbo")
            dead = _configured_settings()
            dead.ai_dossier_api_key = ""
            with patch.object(services_llm_monster, "get_settings", return_value=dead):
                with self.assertRaises(LLMError):
                    queue_llm_run(db, "ASPER1", mode="analyze")
            # Nothing was queued by the failed attempts.
            self.assertIsNone(get_llm_runtime(db, "ASPER1")["pending_run"])
        finally:
            _close(db)

    def test_stale_active_is_reaped_and_new_run_accepted(self):
        db = _main_db()
        try:
            with patch.object(services_llm_monster, "get_settings", return_value=_configured_settings()):
                queue_llm_run(db, "ASPER1", mode="analyze")
                runtime = get_llm_runtime(db, "ASPER1")
                active = dict(runtime["pending_run"])
                active["started_at"] = (datetime.utcnow() - timedelta(hours=2)).isoformat()
                active["lease_expires_at"] = (datetime.utcnow() - timedelta(hours=1)).isoformat()
                runtime["pending_run"] = None
                runtime["active_run"] = active
                services_llm_monster.save_llm_runtime(db, "ASPER1", runtime)
                # Stale lease: the new request reaps it back to pending and
                # then conflicts (the run will be retried by the worker).
                with self.assertRaises(QueueBusyError):
                    queue_llm_run(db, "ASPER1", mode="apply")
                self.assertIsNotNone(get_llm_runtime(db, "ASPER1")["pending_run"])
        finally:
            _close(db)

    def test_active_run_liveness(self):
        now = datetime.utcnow()
        live = {"lease_expires_at": (now + timedelta(seconds=60)).isoformat()}
        stale = {"lease_expires_at": (now - timedelta(seconds=1)).isoformat()}
        self.assertTrue(_active_run_live(live, now))
        self.assertFalse(_active_run_live(stale, now))
        self.assertFalse(_active_run_live(None, now))
        self.assertFalse(_active_run_live({}, now))

    def test_process_executes_pending_and_applies_revision(self):
        db = _main_db()
        try:
            with patch.object(services_llm_monster, "get_settings", return_value=_configured_settings()):
                queued = queue_llm_run(db, "ASPER1", mode="apply", admin_id=9)

            row = SimpleNamespace(
                config={"global": {"intensity_threshold": 0.2}},
                memory={}, telemetry={}, plans=[{"id": "old"}],
                desired_revision=4, applied_revision=4,
                updated_by_admin_id=None, updated_at=None,
            )
            game_db = SimpleNamespace(commit=lambda: None, refresh=lambda _row: None)
            fake_result = {
                "applied_changes": [{"key": "intensity_threshold", "new_value": 0.25}],
                "spawn_requests": [],
                "spawn_plans": [{"id": "llm-plan-1"}],
                "rejected": [],
            }

            def _fake_cycle(main_db, server, state, mode="analyze", instruction=None):
                self.assertEqual(server, "ASPER1")
                self.assertEqual(mode, "apply")
                state["config"]["global"]["intensity_threshold"] = 0.25
                return dict(fake_result)

            with patch.object(services_llm_monster, "run_llm_cycle", side_effect=_fake_cycle):
                outcome = _process_server_llm_queue(db, game_db, "ASPER1", row)

            self.assertEqual(outcome["status"], "ok")
            self.assertEqual(outcome["run_id"], queued["id"])
            self.assertEqual(row.desired_revision, 5)
            self.assertEqual(row.updated_by_admin_id, 9)
            self.assertEqual(row.config["global"]["intensity_threshold"], 0.25)
            self.assertEqual([plan["id"] for plan in row.plans], ["old", "llm-plan-1"])
            runtime = get_llm_runtime(db, "ASPER1")
            self.assertIsNone(runtime["pending_run"])
            self.assertIsNone(runtime["active_run"])
        finally:
            _close(db)

    def test_process_records_failure_and_clears_active(self):
        db = _main_db()
        try:
            with patch.object(services_llm_monster, "get_settings", return_value=_configured_settings()):
                queue_llm_run(db, "ASPER1", mode="analyze")
            row = SimpleNamespace(
                config={}, memory={}, telemetry={}, plans=[],
                desired_revision=1, applied_revision=1,
                updated_by_admin_id=None, updated_at=None,
            )
            game_db = SimpleNamespace(commit=lambda: None, refresh=lambda _row: None)
            with patch.object(services_llm_monster, "run_llm_cycle", side_effect=LLMError("AI-агрегатор недоступен")):
                outcome = _process_server_llm_queue(db, game_db, "ASPER1", row)
            self.assertEqual(outcome["status"], "error")
            self.assertIn("недоступен", outcome["error"])
            runtime = get_llm_runtime(db, "ASPER1")
            self.assertIsNone(runtime["active_run"])
            self.assertTrue(any("Ошибка" in entry.get("summary", "") for entry in runtime["history"]))
            self.assertEqual(row.desired_revision, 1)
        finally:
            _close(db)


if __name__ == "__main__":
    unittest.main()
