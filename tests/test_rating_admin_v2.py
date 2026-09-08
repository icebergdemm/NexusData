from datetime import datetime, timezone
import sys
import types
from concurrent.futures import Future
from types import SimpleNamespace

if sys.platform == "win32" and "fcntl" not in sys.modules:
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX = 2
    fcntl.LOCK_UN = 8
    fcntl.flock = lambda *_args, **_kwargs: None
    sys.modules["fcntl"] = fcntl

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import models
from routes import rating_events
from routes import rating_admin_v2


def _client():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(
        engine,
        tables=[
            models.PlayerData.__table__,
            models.RatingEventLedger.__table__,
            models.RatingProjectionState.__table__,
            models.RatingAdjustment.__table__,
            models.RatingRankHistory.__table__,
            models.PlayerRating.__table__,
            models.RatingDeliveryLog.__table__,
        ],
    )
    db = Session(engine)
    db.add(models.PlayerData(player_id=1, player_steamid="76561198000000001", player_last_time_name="Ada", play_time=10800))
    db.add(models.RatingProjectionState(
        steam_id="76561198000000001", mode="mission", role="mechanic",
        cumulative_points=12.5, current_rating=3.1,
        last_event_at=datetime(2026, 9, 7, 12, tzinfo=timezone.utc),
    ))
    db.add(models.RatingEventLedger(
        event_id="r1", version=1, steam_id="76561198000000001", mode="mission",
        role="mechanic", event_type="repair_hull", points_delta=12.5,
        occurred_at=datetime(2026, 9, 7, 12, tzinfo=timezone.utc),
        source_context={"server": "ASPER1"}, round_context={"round": "42"},
    ))
    db.add(models.RatingDeliveryLog(event_id="r1", mode="mission", role="mechanic", status="accepted"))
    db.add(models.RatingDeliveryLog(event_id="pvp-event", mode="pvp", role="security", status="rejected", reason="test"))
    db.commit()

    app = FastAPI()
    app.include_router(rating_admin_v2.router)
    app.dependency_overrides[rating_admin_v2.get_db] = lambda: db
    admin = SimpleNamespace(is_superuser=True)
    app.dependency_overrides[rating_admin_v2.get_current_admin_v2] = lambda: admin
    rating_admin_v2.check_permission = lambda *_args, **_kwargs: True
    return TestClient(app), db, engine


def test_admin_leaderboard_is_scoped_by_mode_and_role():
    client, db, engine = _client()
    try:
        response = client.get("/api/v2/admin/ratings?mode=mission&role=mechanic")
        assert response.status_code == 200
        assert response.json()["leaderboard"][0]["player_name"] == "Ada"
        assert response.json()["leaderboard"][0]["play_time_hours"] == 3.0

        pvp = client.get("/api/v2/admin/ratings?mode=pvp&role=mechanic")
        assert pvp.status_code == 200
        assert pvp.json()["leaderboard"] == []
    finally:
        db.close()
        engine.dispose()


def test_admin_can_force_discord_rating_publish(monkeypatch):
    scheduled = {}

    class FakeBot:
        def publish_rating_task(self):
            return object()

        def schedule(self, coroutine, description=""):
            scheduled["coroutine"] = coroutine
            scheduled["description"] = description
            future = Future()
            future.set_result(None)
            return future

    monkeypatch.setitem(sys.modules, "discord_bot", types.SimpleNamespace(bot=FakeBot()))
    async def skip_csrf(*_args, **_kwargs):
        return None

    monkeypatch.setattr(rating_admin_v2, "ensure_csrf_or_valid_admin_session", skip_csrf)
    client, db, engine = _client()
    try:
        response = client.post("/api/v2/admin/ratings/discord-refresh")
        assert response.status_code == 200
        assert response.json()["status"] == "published"
        assert scheduled["description"] == "Принудительное обновление рейтинга в Discord"
    finally:
        db.close()
        engine.dispose()


def test_admin_leaderboard_supports_one_paginated_table_for_all_roles():
    client, db, engine = _client()
    try:
        db.add_all([
            models.RatingProjectionState(
                steam_id="76561198000000002", mode="mission", role="captain",
                cumulative_points=25.0, current_rating=6.0,
            ),
            models.RatingProjectionState(
                steam_id="76561198000000003", mode="mission", role="medic",
                cumulative_points=20.0, current_rating=5.0,
            ),
        ])
        db.commit()
        response = client.get("/api/v2/admin/ratings?mode=mission&role=all&page=2&page_size=1")
        assert response.status_code == 200
        payload = response.json()
        assert payload["page"] == 2
        assert payload["page_size"] == 1
        assert payload["total"] == 3
        assert payload["total_pages"] == 3
        assert payload["has_next"] is True
        assert payload["leaderboard"][0]["role"] == "medic"
    finally:
        db.close()
        engine.dispose()


def test_admin_quality_is_honest_about_legacy_data_instead_of_calling_it_confidence():
    client, db, engine = _client()
    try:
        state = db.query(models.RatingProjectionState).filter_by(
            steam_id="76561198000000001", mode="mission", role="mechanic"
        ).one()
        state.qualified_rounds = 1
        state.round_tokens = ["legacy:player:1"]
        db.commit()
        response = client.get("/api/v2/admin/ratings?mode=mission&role=mechanic")
        assert response.status_code == 200
        row = response.json()["leaderboard"][0]
        assert row["qualified_rounds"] == 1
        assert row["data_quality"] == "legacy"
        assert row["confidence"] is None
    finally:
        db.close()
        engine.dispose()


def test_admin_player_explanation_and_event_log_are_available():
    client, db, engine = _client()
    try:
        detail = client.get("/api/v2/admin/ratings/76561198000000001?mode=mission&role=mechanic")
        assert detail.status_code == 200
        assert detail.json()["rating"]["current_rating"] == 3.1

        explanation = client.get("/api/v2/admin/ratings/76561198000000001/explanation?mode=mission&role=mechanic")
        assert explanation.status_code == 200
        assert explanation.json()["mode"] == "mission"
        assert explanation.json()["event_count"] == 1

        events = client.get("/api/v2/admin/rating-events?mode=mission&role=mechanic&steam_id=76561198000000001")
        assert events.status_code == 200
        assert events.json()["events"][0]["event_id"] == "r1"
    finally:
        db.close()
        engine.dispose()


def test_admin_player_preview_defaults_to_all_roles():
    client, db, engine = _client()
    try:
        db.add(models.RatingProjectionState(
            steam_id="76561198000000001", mode="mission", role="captain",
            cumulative_points=8.0, current_rating=2.5,
        ))
        db.add(models.RatingEventLedger(
            event_id="captain-event", version=1, steam_id="76561198000000001", mode="mission",
            role="captain", event_type="captain.navigate", points_delta=8.0,
            occurred_at=datetime(2026, 9, 7, 13, tzinfo=timezone.utc),
            source_context={}, round_context={},
        ))
        db.commit()

        detail = client.get("/api/v2/admin/ratings/76561198000000001?mode=mission&role=all")
        assert detail.status_code == 200
        assert detail.json()["role"] == "all"
        assert {row["role"] for row in detail.json()["ratings"]} == {"captain", "mechanic"}

        explanation = client.get("/api/v2/admin/ratings/76561198000000001/explanation?mode=mission&role=all")
        assert explanation.status_code == 200
        assert {row["role"] for row in explanation.json()["roles"]} == {"captain", "mechanic"}

        history = client.get("/api/v2/admin/ratings/76561198000000001/history?mode=mission&role=all")
        assert history.status_code == 200
        assert history.json()["role"] == "all"
    finally:
        db.close()
        engine.dispose()


def test_admin_event_log_is_paginated():
    client, db, engine = _client()
    try:
        db.add_all([
            models.RatingEventLedger(
                event_id="r2", version=1, steam_id="76561198000000001", mode="mission",
                role="mechanic", event_type="repair.device", points_delta=2.0,
                occurred_at=datetime(2026, 9, 7, 11, tzinfo=timezone.utc),
                source_context={}, round_context={},
            ),
            models.RatingEventLedger(
                event_id="r3", version=1, steam_id="76561198000000001", mode="mission",
                role="mechanic", event_type="engineering.wire", points_delta=1.0,
                occurred_at=datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
                source_context={}, round_context={},
            ),
        ])
        db.commit()
        response = client.get("/api/v2/admin/rating-events?mode=mission&page=2&page_size=1")
        assert response.status_code == 200
        payload = response.json()
        assert payload["events"][0]["event_id"] == "r2"
        assert payload["pagination"] == {
            "page": 2,
            "page_size": 1,
            "total": 3,
            "total_pages": 3,
            "has_next": True,
        }
    finally:
        db.close()
        engine.dispose()


def test_admin_exposes_rank_history_and_mode_scoped_delivery_log():
    client, db, engine = _client()
    try:
        history = client.get("/api/v2/admin/ratings/76561198000000001/history?mode=mission&role=mechanic")
        assert history.status_code == 200
        assert history.json()["history"] == []

        delivery = client.get("/api/v2/admin/rating-delivery-log?mode=mission&role=mechanic")
        assert delivery.status_code == 200
        assert delivery.json()["entries"] == [{
            "event_id": "r1",
            "mode": "mission",
            "role": "mechanic",
            "status": "accepted",
            "reason": None,
            "received_at": delivery.json()["entries"][0]["received_at"],
        }]
        assert delivery.json()["summary"] == {
            "accepted": 1,
            "duplicate": 0,
            "rejected": 0,
            "retry": 0,
            "failed": 0,
            "total": 1,
        }
    finally:
        db.close()
        engine.dispose()
