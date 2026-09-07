import importlib.util
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import models
from database import get_game_db
from security import verify_external_api_key_dependency


_ROUTE_PATH = Path(__file__).resolve().parents[1] / "routes" / "rating_events.py"


def _route_module():
    if not _ROUTE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("rating_events_under_test", _ROUTE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _client(monkeypatch):
    route_module = _route_module()
    assert route_module is not None, "rating events v2 route is not implemented"

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(
        engine,
        tables=[
            models.RatingEventLedger.__table__,
            models.RatingProjectionState.__table__,
            models.RatingDeliveryLog.__table__,
            models.RatingCalculationRun.__table__,
            models.RatingConfigVersion.__table__,
            models.RatingRankHistory.__table__,
        ],
    )
    db = Session(engine)
    app = FastAPI()
    app.include_router(route_module.router)

    def database_override():
        yield db

    app.dependency_overrides[get_game_db] = database_override
    app.dependency_overrides[verify_external_api_key_dependency] = lambda: None
    monkeypatch.setattr(route_module, "GameSessionLocal", lambda: db)
    return TestClient(app), db, engine


def _event(**overrides):
    event = {
        "event_id": "round-42:alice:repair:1",
        "version": 1,
        "steam_id": "76561198000000001",
        "mode": "mission",
        "role": "mechanic",
        "event_type": "repair.hull",
        "raw_value": 12.5,
        "server_name": "ASPER1",
        "server_session_id": "server-session-1",
        "round_token": "round-42-token",
        "plugin_version": "rating-rework/1",
        "occurred_at": "2026-09-07T12:00:00Z",
        "source": {"server": "ASPER1", "plugin": "rating-rework", "sequence": 41},
        "round": {"id": "round-42", "level": "Europa Ridge", "submarine": "Dugong"},
    }
    event.update(overrides)
    return event


def test_batch_accepts_versioned_event_and_projects_its_context(monkeypatch):
    """Removing ledger persistence or the projection update must fail this contract."""
    client, db, engine = _client(monkeypatch)
    try:
        with client:
            response = client.post("/api/v2/rating/events:batch", json={"events": [_event()]})

        assert response.status_code == 200
        assert response.json()["results"] == [
            {"event_id": "round-42:alice:repair:1", "status": "accepted"}
        ]

        ledger = db.query(models.RatingEventLedger).one()
        assert ledger.version == 1
        assert ledger.source_context["server_name"] == "ASPER1"
        assert ledger.source_context["server_session_id"] == "server-session-1"
        assert ledger.round_context["round_token"] == "round-42-token"
        assert ledger.round_context["id"] == "round-42"
        assert ledger.event_metadata == {}
        assert ledger.projected_at is not None

        state = db.query(models.RatingProjectionState).one()
        assert (state.steam_id, state.mode, state.role) == (
            "76561198000000001", "mission", "mechanic"
        )
        assert state.cumulative_points == 18.75
    finally:
        db.close()
        engine.dispose()


def test_batch_marks_retries_duplicate_and_keeps_one_projection_delta(monkeypatch):
    """Dropping event-id idempotency must fail rather than double-award points."""
    client, db, engine = _client(monkeypatch)
    try:
        with client:
            first = client.post("/api/v2/rating/events:batch", json={"events": [_event()]})
            retry = client.post("/api/v2/rating/events:batch", json={"events": [_event()]})

        assert first.json()["results"][0]["status"] == "accepted"
        assert retry.json()["results"][0] == {
            "event_id": "round-42:alice:repair:1",
            "status": "duplicate",
        }
        assert db.query(models.RatingEventLedger).count() == 1
        assert db.query(models.RatingProjectionState).one().cumulative_points == 18.75
    finally:
        db.close()
        engine.dispose()


def test_batch_rejects_bad_event_without_discarding_valid_events(monkeypatch):
    """Replacing per-event rejection with whole-batch failure must fail this contract."""
    client, db, engine = _client(monkeypatch)
    try:
        invalid = _event(event_id="round-42:alice:bad-mode", mode="campaign")
        valid = _event(event_id="round-42:alice:pvp:1", mode="pvp", raw_value=7.0)
        with client:
            response = client.post("/api/v2/rating/events:batch", json={"events": [invalid, valid]})

        assert response.status_code == 200
        results = response.json()["results"]
        assert results[0]["event_id"] == "round-42:alice:bad-mode"
        assert results[0]["status"] == "rejected"
        assert results[1] == {"event_id": "round-42:alice:pvp:1", "status": "accepted"}
        assert db.query(models.RatingEventLedger).count() == 1
        assert db.query(models.RatingProjectionState).one().mode == "pvp"
    finally:
        db.close()
        engine.dispose()


def test_batch_rejects_an_unsupported_event_version(monkeypatch):
    """Treating a future event schema as v1 must fail rather than corrupt its projection."""
    client, db, engine = _client(monkeypatch)
    try:
        future_event = _event(event_id="round-42:alice:future:1", version=2)
        with client:
            response = client.post("/api/v2/rating/events:batch", json={"events": [future_event]})

        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "rejected"
        assert db.query(models.RatingEventLedger).count() == 0
        assert db.query(models.RatingProjectionState).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_projection_state_is_separate_for_mission_and_pvp(monkeypatch):
    """Collapsing mode into the legacy steam-id-plus-role key must fail this contract."""
    client, db, engine = _client(monkeypatch)
    try:
        pvp = _event(event_id="round-42:alice:pvp:1", mode="pvp", raw_value=3.0)
        with client:
            response = client.post("/api/v2/rating/events:batch", json={"events": [_event(), pvp]})

        assert [item["status"] for item in response.json()["results"]] == ["accepted", "accepted"]
        states = db.query(models.RatingProjectionState).order_by(models.RatingProjectionState.mode).all()
        assert [(state.mode, state.cumulative_points) for state in states] == [
            ("mission", 18.75),
            ("pvp", 4.5),
        ]
    finally:
        db.close()
        engine.dispose()


def test_raw_value_is_weighted_server_side_and_unknown_action_is_visible(monkeypatch):
    client, db, engine = _client(monkeypatch)
    try:
        known = _event(
            event_id="round-42:alice:revive:1",
            role="medic",
            event_type="medical.revive",
            raw_value=2.0,
            points_delta=None,
        )
        unknown = _event(
            event_id="round-42:alice:unknown:1",
            role="medic",
            event_type="medical.telepathy",
            raw_value=3.0,
            points_delta=None,
        )
        with client:
            response = client.post("/api/v2/rating/events:batch", json={"events": [known, unknown]})

        assert [item["status"] for item in response.json()["results"]] == ["accepted", "accepted"]
        ledger = db.query(models.RatingEventLedger).order_by(models.RatingEventLedger.id).all()
        assert ledger[0].points_delta == 16.0
        assert ledger[0].mapping_status == "mapped"
        assert ledger[1].points_delta == 0.0
        assert ledger[1].mapping_status == "unmapped"
        state = db.query(models.RatingProjectionState).one()
        assert state.cumulative_points == 16.0
        assert state.unmapped_events == 1
    finally:
        db.close()
        engine.dispose()


def test_reusing_event_id_with_different_payload_is_rejected(monkeypatch):
    client, db, engine = _client(monkeypatch)
    try:
        with client:
            first = client.post("/api/v2/rating/events:batch", json={"events": [_event()]})
            conflict = client.post("/api/v2/rating/events:batch", json={"events": [_event(raw_value=99.0)]})

        assert first.json()["results"][0]["status"] == "accepted"
        assert conflict.json()["results"][0]["status"] == "rejected"
        assert "different payload" in conflict.json()["results"][0]["reason"]
        assert db.query(models.RatingEventLedger).count() == 1
    finally:
        db.close()
        engine.dispose()


def test_semantically_identical_events_with_different_ids_are_deduplicated(monkeypatch):
    """Retries with a new id must not award the same gameplay event twice."""
    client, db, engine = _client(monkeypatch)
    try:
        first_event = _event(event_id="round-42:alice:repair:1")
        retry_event = _event(event_id="round-42:alice:repair:retry")
        with client:
            first = client.post("/api/v2/rating/events:batch", json={"events": [first_event]})
            retry = client.post("/api/v2/rating/events:batch", json={"events": [retry_event]})

        assert first.json()["results"][0]["status"] == "accepted"
        assert retry.json()["results"][0] == {
            "event_id": "round-42:alice:repair:retry",
            "status": "duplicate",
        }
        assert db.query(models.RatingEventLedger).count() == 1
        assert db.query(models.RatingProjectionState).one().cumulative_points == 18.75
        delivery = db.query(models.RatingDeliveryLog).order_by(models.RatingDeliveryLog.id).all()
        assert [row.status for row in delivery] == ["accepted", "duplicate"]
    finally:
        db.close()
        engine.dispose()


def test_semantic_dedup_normalizes_metadata_but_preserves_later_events(monkeypatch):
    client, db, engine = _client(monkeypatch)
    try:
        first = _event(
            event_id="round-42:alice:repair:1",
            metadata={"device": "reactor", "parts": {"a": 1, "b": 2}},
        )
        retry = _event(
            event_id="round-42:alice:repair:retry",
            metadata={"parts": {"b": 2, "a": 1}, "device": "reactor"},
        )
        later = _event(
            event_id="round-42:alice:repair:2",
            occurred_at="2026-09-07T12:00:03Z",
            metadata={"device": "reactor", "parts": {"a": 1, "b": 2}},
        )
        with client:
            response = client.post("/api/v2/rating/events:batch", json={"events": [first, retry, later]})

        assert [item["status"] for item in response.json()["results"]] == [
            "accepted", "duplicate", "accepted"
        ]
        assert db.query(models.RatingEventLedger).count() == 2
    finally:
        db.close()
        engine.dispose()
