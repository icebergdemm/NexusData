import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from database import Base
from models import AppSetting, MonsterSpawnControl, MonsterSpawnLLMRun, MonsterSpawnRoundReport
import services_llm_monster
from services_llm_monster import (
    QueueBusyError,
    LLMError,
    _active_run_live,
    _pending_servers_from_setting,
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
    Base.metadata.create_all(engine, tables=[AppSetting.__table__, MonsterSpawnLLMRun.__table__])
    db = Session(engine)
    db._test_engine = engine
    return db


def _close(db):
    db.close()
    db._test_engine.dispose()


class MonsterLLMQueueTests(unittest.TestCase):
    def setUp(self):
        # _task (stage logging) opens its own production DB session — never
        # appropriate for unit tests.
        self._task_patch = patch.object(services_llm_monster, "_task", MagicMock())
        self._task_patch.start()

    def tearDown(self):
        self._task_patch.stop()

    def test_generation_uses_ai_centre_gateway(self):
        response = {"choices": [{"message": {"content": '{"conclusion":"ok"}'}}]}
        with patch("services_llm_gateway.submit", return_value=42) as submit, \
                patch("services_llm_gateway.poll", return_value={"status": "succeeded", "result": response}):
            content = services_llm_monster._chat_completions(
                [{"role": "user", "content": "round"}], "model", 30, 0.1, 0.8)
        self.assertEqual(content, '{"conclusion":"ok"}')
        self.assertEqual(submit.call_args.args[0], "monster_llm")

    def test_three_rounds_get_conclusions_and_one_aggregate_report(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[
            AppSetting.__table__, MonsterSpawnControl.__table__,
            MonsterSpawnLLMRun.__table__, MonsterSpawnRoundReport.__table__])
        factory = sessionmaker(bind=engine)
        db = factory()
        try:
            db.add(MonsterSpawnControl(server="ASPER1", config={"global": {"intensity_threshold": 0.2}},
                                       memory={}, telemetry={}, plans=[], round_reports="[]"))
            for index in range(3):
                db.add(MonsterSpawnRoundReport(server="ASPER1", round_key=f"r{index}",
                                               report_json={"round_start_utc": f"r{index}",
                                                            "waves": [{"wave_name": "crawler", "repel_seconds": 40}]}))
            db.commit()
            with patch("database.GameSessionLocal", factory), patch("database.SessionLocal", factory), \
                    patch.object(services_llm_monster, "get_settings", return_value=_configured_settings()), \
                    patch.object(services_llm_monster, "_chat_completions", return_value='{"conclusion":"Бой завершён"}'), \
                    patch.object(services_llm_monster, "run_llm_cycle", return_value={
                        "confidence": 0.8, "duration_ms": 10, "proposed_changes": [],
                        "applied_changes": [], "rejected": [], "spawn_plans": []}):
                for _ in range(3):
                    services_llm_monster.process_pending_round_reports()
            db.expire_all()
            self.assertEqual(db.query(MonsterSpawnRoundReport).filter_by(analysis_status="succeeded").count(), 3)
            self.assertEqual(db.query(MonsterSpawnLLMRun).filter_by(
                source="auto", status="succeeded").count(), 1)
        finally:
            db.close()
            engine.dispose()

    def test_queue_stores_pending_run_and_durable_record(self):
        db = _main_db()
        try:
            with patch.object(services_llm_monster, "get_settings", return_value=_configured_settings()):
                run = queue_llm_run(db, "ASPER1", mode="spawn", instruction="давление", admin_id=7)
            self.assertEqual(run["mode"], "spawn")
            self.assertEqual(run["instruction"], "давление")
            self.assertEqual(run["requested_by"], 7)
            self.assertEqual(get_llm_runtime(db, "ASPER1")["pending_run"]["id"], run["id"])
            record = db.query(MonsterSpawnLLMRun).one()
            self.assertEqual(record.run_id, run["id"])
            self.assertEqual(record.status, "queued")
            self.assertEqual(record.instruction, "давление")
            self.assertEqual(record.requested_by_admin_id, 7)
        finally:
            _close(db)

    def test_queue_truncates_long_instruction(self):
        db = _main_db()
        try:
            with patch.object(services_llm_monster, "get_settings", return_value=_configured_settings()):
                run = queue_llm_run(db, "ASPER1", instruction="x" * 5000)
            self.assertEqual(len(run["instruction"]), 300)
            # Full instruction lives on the durable record, not the 2000-char
            # AppSetting blob (which used to corrupt the whole runtime JSON).
            record = db.query(MonsterSpawnLLMRun).one()
            self.assertEqual(record.instruction, "x" * 2000)
            runtime = get_llm_runtime(db, "ASPER1")
            self.assertEqual(runtime["pending_run"]["id"], run["id"])
            self.assertEqual(runtime["enabled"], False)
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
            self.assertEqual(db.query(MonsterSpawnLLMRun).count(), 0)
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

    def test_pending_servers_are_indexed_from_main_db_setting(self):
        value = '{"asper1": {"pending_run": {"id": "abc"}}, "ASPER2": {"active_run": {}}}'
        self.assertEqual(_pending_servers_from_setting(value), {"ASPER1"})
        self.assertEqual(_pending_servers_from_setting("not-json"), set())

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
                "at": datetime.utcnow().isoformat(),
                "duration_ms": 500,
                "confidence": 0.8,
                "applied_changes": [{"key": "intensity_threshold", "new_value": 0.25}],
                "spawn_requests": [],
                "spawn_plans": [{"id": "llm-plan-1"}],
                "rejected": [],
                "situation": "test",
            }

            def _fake_cycle(main_db, server, state, mode="analyze", instruction=None, task_ref=None):
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
            # The durable record carries the full result.
            record = db.query(MonsterSpawnLLMRun).one()
            self.assertEqual(record.status, "succeeded")
            self.assertEqual(record.applied_count, 1)
            self.assertEqual(record.spawned_count, 1)
            self.assertIsNotNone(record.result_json)
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
            record = db.query(MonsterSpawnLLMRun).one()
            self.assertEqual(record.status, "failed")
            self.assertIn("недоступен", record.error_message)
        finally:
            _close(db)

    def test_analyze_returns_validated_proposal_without_changing_config(self):
        db = _main_db()
        state = {"config": {"global": {"intensity_threshold": 0.2}},
                 "memory": {}, "telemetry": {}, "dashboard": {}}
        answer = ('{"situation":"ровный темп","crew_summary":"экипаж справляется",'
                  '"recommendations":[],"config_changes":['
                  '{"key":"intensity_threshold","new_value":0.25,"reason":"темп"}],'
                  '"spawn_requests":[],"confidence":0.8}')
        try:
            with patch.object(services_llm_monster, "get_settings", return_value=_configured_settings()), \
                    patch.object(services_llm_monster, "_chat_completions", return_value=answer):
                result = services_llm_monster.run_llm_cycle(db, "ASPER1", state)
            self.assertEqual(result["proposed_changes"][0]["new_value"], 0.25)
            self.assertEqual(result["applied_changes"], [])
            self.assertEqual(state["config"]["global"]["intensity_threshold"], 0.2)
        finally:
            _close(db)

    def test_learning_mode_proposes_settings_without_spawning_or_applying(self):
        db = _main_db()
        state = {"config": {"global": {"intensity_threshold": 0.2,
                 "vanilla_events": {"mode": "learning"}}}, "memory": {},
                 "telemetry": {}, "dashboard": {}}
        answer = ('{"situation":"ванильный раунд","crew_summary":"тяжело",'
                  '"recommendations":[],"config_changes":['
                  '{"key":"intensity_threshold","new_value":0.25,"reason":"темп"}],'
                  '"spawn_requests":[{"species":"crawler","count":1}],"confidence":0.8}')
        try:
            with patch.object(services_llm_monster, "get_settings", return_value=_configured_settings()), \
                    patch.object(services_llm_monster, "_chat_completions", return_value=answer):
                result = services_llm_monster.run_llm_cycle(db, "ASPER1", state, mode="apply")
            self.assertEqual(result["proposed_changes"][0]["new_value"], 0.25)
            self.assertEqual(result["applied_changes"], [])
            self.assertEqual(result["spawn_plans"], [])
            self.assertEqual(state["config"]["global"]["intensity_threshold"], 0.2)
            prompt = services_llm_monster._build_user_prompt(state, None, [])
            self.assertIn("learning_mode_note", prompt)
        finally:
            _close(db)


if __name__ == "__main__":
    unittest.main()
