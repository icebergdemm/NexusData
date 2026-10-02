from copy import deepcopy
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import AppSetting, MonsterSpawnLLMRun
import services_llm_monster as service


def snapshot():
    return {
        "round_active": True, "round_key": "round-a", "max_short_term_difficulty": 6, "short_term_difficulty": 0,
        "species_baseline": [{"species": "crawler", "effective_threat": 1, "hp_override": 0, "base_hp": 100}],
        "active_waves": [], "live_snapshot": {
            "round_key": "round-a", "snapshot_at": datetime.utcnow().isoformat() + "Z", "round_seconds": 1800,
            "crew": [{"id": 1, "dead": False, "hp_percent": 100, "vitality": 100, "max_vitality": 100,
                      "incapacitated": False, "outside": False}],
            "equipment": [{"id": 1, "kind": "reactor", "condition_percent": 100, "power_on": True, "temperature": 50, "fuel": 50},
                          {"id": 2, "kind": "engine", "condition_percent": 100, "voltage": 1, "min_voltage": .5},
                          {"id": 3, "kind": "navigation", "condition_percent": 100, "voltage": 1, "min_voltage": .5},
                          {"id": 4, "kind": "supercapacitor", "condition_percent": 100, "charge": 100, "capacity": 100}],
            "water_percent": 0, "oxygen_percent": 100, "fire_count": 0,
            "crew_idle_seconds": 1200, "progress_idle_seconds": 1200, "combat_waves": 0,
        },
    }


def metrics(telemetry):
    return service._live_metrics(service._controller_live_world(telemetry), telemetry, {})


def action_inputs(telemetry):
    state = {"config": {"global": {"vanilla_events": {"mode": "controller"}},
                        "waves": [{"monsters": [{"species": "crawler"}]}]},
             "memory": {}, "telemetry": telemetry, "world_overview": service._controller_live_world(telemetry),
             "live_analysis": True, "live_control_enabled": True, "live_metrics": metrics(telemetry)}
    result = {"confidence": .8, "live_proposal": {"action": "spawn", "reason_code": "afk", "evidence": ["idle 1200s"],
              "requests": [{"species": "crawler", "count": 2, "hp_multiplier": 1, "position_hint": "ahead", "reason": "idle"}]}}
    runtime = {"enabled": True, "live_analysis_enabled": True, "live_control_enabled": True}
    return state, result, runtime


def test_individual_health_and_equipment_support_long_idle_pressure():
    telemetry = snapshot()
    world = service._controller_live_world(telemetry)
    assert world["crew"]["individuals"][0]["vitality"] == 100
    current = metrics(telemetry)
    assert current["control"] == {"spawn_ready": True, "afk_eligible": True, "hold_recommended": False}
    state, result, runtime = action_inputs(telemetry)
    plans = service._live_action_plans(state, result, telemetry, runtime, [], datetime.utcnow())
    assert len(plans) == 1 and plans[0]["source"] == "llm_live"
    assert plans[0]["round_key"] == "round-a" and plans[0]["control_action"] == "spawn"
    assert plans[0]["monsters"][0]["count"] == 2
    assert 115 <= (service._snapshot_time(plans[0]["expires_at"]) - datetime.utcnow()).total_seconds() <= 120


@pytest.mark.parametrize("change", ["injury", "repair", "combat", "oxygen", "fire", "engine_power", "missing_capacitor", "missing_hp", "meltdown"])
def test_damage_work_and_unknown_equipment_never_receive_afk_wave(change):
    telemetry = snapshot()
    state, result, runtime = action_inputs(telemetry)
    live = telemetry["live_snapshot"]
    if change == "injury":
        live["crew"][0]["hp_percent"] = 20
    elif change == "repair":
        live["crew_idle_seconds"] = 10
    elif change == "combat":
        live["combat_waves"] = 1
    elif change == "oxygen":
        live["oxygen_percent"] = 5
    elif change == "fire":
        live["fire_count"] = 1
    elif change == "engine_power":
        live["equipment"][1]["voltage"] = 0
    elif change == "missing_capacitor":
        live["equipment"].pop()
    elif change == "missing_hp":
        live["crew"][0]["hp_percent"] = None
    else:
        live["equipment"][0]["melted"] = True
    assert not metrics(telemetry)["control"]["afk_eligible"]
    assert service._live_action_plans(state, result, telemetry, runtime, [], datetime.utcnow()) == []


def test_diver_and_short_idle_are_not_afk():
    telemetry = snapshot()
    telemetry["live_snapshot"]["crew"][0]["outside"] = True
    assert metrics(telemetry)["control"]["spawn_ready"]
    assert not metrics(telemetry)["control"]["afk_eligible"]
    telemetry["live_snapshot"]["crew"][0]["outside"] = False
    telemetry["live_snapshot"]["crew_idle_seconds"] = 899
    assert not metrics(telemetry)["control"]["afk_eligible"]


def test_old_round_stale_data_and_disabled_control_cancel_actions():
    telemetry = snapshot()
    state, result, runtime = action_inputs(telemetry)
    changed_round = deepcopy(telemetry)
    changed_round["round_key"] = changed_round["live_snapshot"]["round_key"] = "round-b"
    assert not service._live_action_plans(state, result, changed_round, runtime, [], datetime.utcnow())
    telemetry["live_snapshot"]["snapshot_at"] = (datetime.utcnow() - timedelta(minutes=2)).isoformat()
    assert service._controller_live_world(telemetry) is None
    telemetry = snapshot()
    runtime["live_control_enabled"] = False
    assert not service._live_action_plans(state, result, telemetry, runtime, [], datetime.utcnow())


@pytest.mark.parametrize("mode", ["native", "learning", "disabled"])
def test_observation_and_disabled_modes_cannot_receive_live_spawn_actions(mode):
    telemetry = snapshot()
    state, result, runtime = action_inputs(telemetry)
    state["config"]["global"]["vanilla_events"]["mode"] = mode
    assert not service._live_action_plans(state, result, telemetry, runtime, [], datetime.utcnow())
    assert result["live_execution"]["reason"] == "observation_mode"


def test_budget_pending_and_cooldown_bound_live_actions():
    telemetry = snapshot()
    state, result, runtime = action_inputs(telemetry)
    telemetry["short_term_difficulty"] = 5
    assert not service._live_action_plans(state, result, telemetry, runtime, [], datetime.utcnow())
    assert result["live_execution"]["reason"] == "live_pressure_budget_exceeded"
    telemetry["short_term_difficulty"] = 0
    pending = {"source": "llm_live", "round_key": "round-a", "status": "planned", "expires_at": (datetime.utcnow() + timedelta(seconds=30)).isoformat()}
    assert not service._live_action_plans(state, result, telemetry, runtime, [pending], datetime.utcnow())
    pending.update(status="spawned", triggered_at=datetime.utcnow().isoformat())
    assert not service._live_action_plans(state, result, telemetry, runtime, [pending], datetime.utcnow())


def test_large_monster_cannot_hide_behind_saturated_threat_score():
    telemetry = snapshot()
    state, result, runtime = action_inputs(telemetry)
    telemetry["species_baseline"][0].update(base_hp=8000, effective_threat=2.5)
    result["live_proposal"]["requests"][0]["count"] = 1
    assert not service._live_action_plans(state, result, telemetry, runtime, [], datetime.utcnow())
    assert result["live_execution"]["reason"] == "live_pressure_budget_exceeded"


def test_hold_is_short_and_supported_by_current_danger():
    telemetry = snapshot()
    telemetry["live_snapshot"]["crew"][0]["hp_percent"] = 10
    telemetry["live_snapshot"]["water_percent"] = 90
    telemetry["live_snapshot"]["fire_count"] = 2
    state, result, runtime = action_inputs(telemetry)
    result["live_proposal"].update(action="hold", hold_seconds=999)
    assert metrics(telemetry)["control"]["hold_recommended"]
    plans = service._live_action_plans(state, result, telemetry, runtime, [], datetime.utcnow())
    assert plans[0]["hold_seconds"] == 120
    assert plans[0]["monsters"] == []


def test_alive_threat_and_same_round_trends_detect_recognition_errors():
    telemetry = snapshot()
    telemetry["species_threats"] = [{"species": "crawler", "effective_threat": 1}]
    telemetry["active_waves"] = [{"wave_name": "crawler", "monsters_total": 10, "monsters_alive": 1,
                                  "monsters_dead": 8, "monsters_unknown": 0, "monster_counts": {"crawler": 10}}]
    world = service._controller_live_world(telemetry)
    current = service._live_metrics(world, telemetry, {}, {"round_key": "round-a", "workload_index": 10})
    assert current["estimated_monster_threat_points"] == 1
    assert current["workload_change"] == -10
    assert current["diagnostics"] == ["monster_state_count_mismatch:crawler"]
    assert not current["control"]["spawn_ready"]
    assert service._live_metrics(world, telemetry, {}, {"round_key": "other", "workload_index": 10})["workload_change"] is None


def test_live_model_output_is_a_proposal_and_preserves_self_check():
    telemetry = snapshot()
    state, _, _ = action_inputs(telemetry)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[AppSetting.__table__, MonsterSpawnLLMRun.__table__])
    settings = SimpleNamespace(ai_dossier_url="https://ai.example", ai_dossier_api_key="test",
        monster_llm_model="qwen-14b", monster_llm_timeout_seconds=240, monster_llm_temperature=.15, monster_llm_top_p=.8)
    decision = {"confidence": .8, "config_changes": [{"key": "min_seconds_between_spawns", "new_value": 10}],
                "spawn_requests": [{"species": "crawler", "count": 6, "hp_multiplier": 3, "position_hint": "ahead"}],
                "live_action": {"action": "spawn", "reason_code": "afk", "evidence": ["idle 1200s"]},
                "findings": [{"kind": "recognition", "detail": "Tracking is incomplete", "evidence": "unknown state"}]}
    with Session(engine) as db, patch.object(service, "get_settings", return_value=settings), \
         patch.object(service, "_chat_completions", return_value=json.dumps(decision)) as chat, patch.object(service, "_task"):
        result = service.run_llm_cycle(db, "ASPER1", state, mode="spawn")
    assert result["applied_changes"] == [] and result["spawn_plans"] == []
    assert result["live_proposal"]["requests"][0]["count"] == 2
    assert result["live_proposal"]["requests"][0]["hp_multiplier"] == 1.2
    assert result["findings"][0]["kind"] == "recognition"
    assert chat.call_args.kwargs["timeout"] == 600
    assert chat.call_args.kwargs["max_tokens"] == 3072
    assert "spawn_catalog" in chat.call_args.kwargs["messages"][1]["content"]
    assert sum(len(m["content"].encode()) for m in chat.call_args.kwargs["messages"]) < 23000


@pytest.mark.parametrize("round_changed", [False, True])
def test_live_queue_revalidates_and_writes_through_existing_revision_channel(round_changed):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[AppSetting.__table__, MonsterSpawnLLMRun.__table__])
    telemetry = snapshot()
    state, result, runtime = action_inputs(telemetry)
    result.update(applied_changes=[], spawn_plans=[], rejected=[], situation="Idle crew")
    row = SimpleNamespace(config=state["config"], memory={}, telemetry=telemetry, plans=[],
                          desired_revision=5, applied_revision=5, updated_at=None)
    game_db = SimpleNamespace(commit=lambda: None, refresh=lambda row: None, expire_all=lambda: None)
    def infer(*args, **kwargs):
        if round_changed:
            row.telemetry = deepcopy(telemetry)
            row.telemetry["round_key"] = row.telemetry["live_snapshot"]["round_key"] = "round-b"
        return result
    with Session(engine) as db, patch.object(service, "_task"), patch.object(service, "run_llm_cycle", side_effect=infer):
        service._create_run_record(db, "ASPER1", "analyze", "Live", "live", "live-test", None)
        service.save_llm_runtime(db, "ASPER1", {**runtime, "pending_run": {
            "id": "live-test", "mode": "analyze", "source": "live", "requested_at": datetime.utcnow().isoformat(), "attempts": 0}})
        outcome = service._process_server_llm_queue(db, game_db, "ASPER1", row)
        assert outcome["status"] == "ok", outcome
        persisted = db.query(MonsterSpawnLLMRun).filter_by(run_id="live-test").one()
        assert persisted.status == "succeeded"
        assert len(row.plans) == (0 if round_changed else 1)
        assert row.desired_revision == (5 if round_changed else 6)
        assert persisted.result_json["live_execution"]["status"] == ("skipped" if round_changed else "planned")
