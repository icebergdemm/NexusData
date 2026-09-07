from datetime import datetime, timezone
import sys
import types
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
    finally:
        db.close()
        engine.dispose()
