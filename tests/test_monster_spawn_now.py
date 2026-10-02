from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes import monster_spawn as route


def row():
    now = datetime.utcnow()
    return SimpleNamespace(config={"waves": [{"name": "reef", "spawn_type": "mainpath", "monsters": [{"species": "crawler", "count": 3}]}]},
                           memory={"learned_waves": {"learned": {"wave": {"name": "learned", "monsters": [{"species": "crawler", "count": 2}]}}}},
                           telemetry={"round_active": True, "round_key": "round-a", "species_baseline": [{"species": "crawler"}],
                                      "live_snapshot": {"round_key": "round-a", "snapshot_at": now.isoformat() + "Z"}},
                           last_seen_at=now, plans=[], desired_revision=0)


@pytest.mark.parametrize("kind,key,count,expected", [("monster", "crawler", 4, 4), ("wave", "reef", 1, 3), ("learned", "learned", 1, 2)])
def test_spawn_uses_existing_catalog_and_round_bound_plan(kind, key, count, expected):
    state = row()
    plan = route._immediate_spawn_plan(state, route.ImmediateSpawn(kind=kind, key=key, count=count))
    assert plan["monsters"][0]["count"] == expected
    assert plan["source"] == "manual_immediate" and plan["round_key"] == "round-a"
    assert plan["delay_between_spawns"] == 0 and plan["reset_time"] == 0 and plan["chain_wave"] is None
    plan["monsters"][0]["count"] = 99
    assert state.config["waves"][0]["monsters"][0]["count"] == 3


@pytest.mark.parametrize("fault", ["inactive", "stale", "different_round", "unknown", "oversize"])
def test_spawn_rejects_stale_or_invalid_request(fault):
    state = row()
    key = "reef"
    if fault == "inactive": state.telemetry["round_active"] = False
    if fault == "stale": state.telemetry["live_snapshot"]["snapshot_at"] = (datetime.utcnow() - timedelta(seconds=60)).isoformat()
    if fault == "different_round": state.telemetry["live_snapshot"]["round_key"] = "round-b"
    if fault == "unknown": key = "does-not-exist"
    if fault == "oversize": state.config["waves"][0]["monsters"][0]["count"] = 51
    with pytest.raises(HTTPException):
        route._immediate_spawn_plan(state, route.ImmediateSpawn(kind="wave", key=key))


def test_queue_preserves_settings_and_prevents_duplicate_commands(monkeypatch):
    state = row()
    db = SimpleNamespace(commit=lambda: None, refresh=lambda *args, **kwargs: None)
    monkeypatch.setattr(route, "_require", lambda *args: None)
    monkeypatch.setattr(route, "_get_or_create", lambda *args: state)
    request = route.ImmediateSpawn(kind="wave", key="reef")
    result = route.admin_spawn_now(request, "ASPER1", SimpleNamespace(id=1), db)
    assert result["status"] == "queued" and state.desired_revision == 1
    with pytest.raises(HTTPException) as error:
        route.admin_spawn_now(request, "ASPER1", SimpleNamespace(id=1), db)
    assert error.value.status_code == 409
    monkeypatch.setattr(route, "_serialize", lambda row: row)
    route.admin_update(route.AdminUpdate(plans=[]), "ASPER1", SimpleNamespace(id=1), db)
    assert state.plans[0]["id"] == result["plan_id"]


def test_spawn_requires_edit_permission(monkeypatch):
    def deny(*args):
        raise HTTPException(status_code=403)
    monkeypatch.setattr(route, "_require", deny)
    with pytest.raises(HTTPException) as error:
        route.admin_spawn_now(route.ImmediateSpawn(kind="monster", key="crawler"), "ASPER1", SimpleNamespace(id=1), None)
    assert error.value.status_code == 403
