from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import PlayerAnomaly, PlayerData, PlayerIP, PlayerPunishment, PlayerSession
from services import PunishmentService
from services_anomaly import _scan_ban_evasion


def _database() -> tuple[object, Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            PlayerData.__table__,
            PlayerIP.__table__,
            PlayerPunishment.__table__,
            PlayerSession.__table__,
            PlayerAnomaly.__table__,
        ],
    )
    return engine, Session(engine)


def _player(player_id: int, steamid: str) -> PlayerData:
    return PlayerData(
        player_id=player_id,
        player_last_time_name=f"Player {player_id}",
        player_steamid=steamid,
    )


def _ban(player_id: int, steamid: str, *, active: int = 1) -> PlayerPunishment:
    return PlayerPunishment(
        player_id=player_id,
        player_steamid=steamid,
        type="ban",
        reason="test",
        issued_by_name="test",
        is_active=active,
    )


def test_unban_deactivates_every_duplicate_ban_for_account():
    engine, db = _database()
    try:
        db.add_all([_player(1, "76561198000000001"), _player(2, "76561198000000002")])
        db.add_all(
            [
                _ban(1, "76561198000000001"),
                _ban(1, "76561198000000001"),
                _ban(1, "76561198000000001", active=0),
                _ban(2, "76561198000000002"),
            ]
        )
        db.commit()

        count = PunishmentService(db).set_all_active_bans_void("76561198000000001")

        own_bans = (
            db.query(PlayerPunishment)
            .filter(PlayerPunishment.player_steamid == "76561198000000001")
            .all()
        )
        other_ban = (
            db.query(PlayerPunishment)
            .filter(PlayerPunishment.player_steamid == "76561198000000002")
            .one()
        )
        assert count == 2
        assert all(row.is_active == 0 for row in own_bans)
        assert other_ban.is_active == 1
    finally:
        db.close()
        engine.dispose()


def test_anomaly_scanner_does_not_reban_after_manual_unban():
    engine, db = _database()
    now = datetime.utcnow()
    try:
        banned = _player(1, "76561198000000001")
        candidate = _player(2, "76561198000000002")
        db.add_all([banned, candidate])
        db.add_all(
            [
                PlayerIP(player_id=1, ip="203.0.113.10", subnet="203.0.113.0/24", first_seen=now, last_seen=now),
                PlayerIP(player_id=2, ip="203.0.113.10", subnet="203.0.113.0/24", first_seen=now, last_seen=now),
                PlayerSession(player_id=2, session_number=1, login_time=now, logout_time=None),
                _ban(1, banned.player_steamid),
                _ban(2, candidate.player_steamid, active=0),
                PlayerAnomaly(
                    category="twink",
                    subtype="ip_ban_evasion",
                    player_id=1,
                    related_player_id=2,
                    score=80,
                    evidence={"ip": "203.0.113.10"},
                    status="new",
                    auto_banned=True,
                ),
            ]
        )
        db.commit()

        with patch("services_anomaly._auto_ban_evader") as auto_ban:
            result = _scan_ban_evasion(db, time.monotonic() + 5)

        auto_ban.assert_not_called()
        assert result["auto_banned"] == 0
    finally:
        db.close()
        engine.dispose()
