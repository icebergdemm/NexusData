from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import AdminUser, DiscordMember, PlayerPunishment
from services import format_admin_issuer_name, refresh_recent_punishment_issuers


def _database() -> tuple[object, Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def test_format_admin_issuer_name_prefers_server_nickname():
    engine, db = _database()
    try:
        admin = AdminUser(discord_id="42", discord_username="Global Name")
        db.add(admin)
        db.flush()
        db.add(DiscordMember(discord_id="42", username="account_name", display_name="Server Nick"))
        db.commit()

        assert format_admin_issuer_name(db, admin) == "ND. Server Nick"
    finally:
        db.close()
        engine.dispose()


def test_format_admin_issuer_name_falls_back_without_synced_member():
    engine, db = _database()
    try:
        admin = AdminUser(discord_id="42", discord_username="Global Name")
        db.add(admin)
        db.commit()

        assert format_admin_issuer_name(db, admin) == "ND. Global Name"

        admin.discord_username = None
        db.commit()
        assert format_admin_issuer_name(db, admin) == "ND. User_42"
    finally:
        db.close()
        engine.dispose()


def test_refresh_recent_punishment_issuers_updates_only_30_latest_linked_rows():
    engine, db = _database()
    try:
        admin = AdminUser(discord_id="42", discord_username="Global Name")
        db.add(admin)
        db.flush()
        db.add(DiscordMember(discord_id="42", display_name="Current Server Nick"))

        base_time = datetime(2026, 1, 1)
        rows = [
            PlayerPunishment(
                player_id=1,
                player_steamid=str(index),
                type="warning",
                issued_by_admin_id=admin.id,
                issued_by_name="ND. Old Name",
                issued_at=base_time + timedelta(minutes=index),
            )
            for index in range(31)
        ]
        external = PlayerPunishment(
            player_id=1,
            player_steamid="external",
            type="warning",
            issued_by_admin_id=None,
            issued_by_name="GameServer",
            issued_at=base_time + timedelta(minutes=100),
        )
        db.add_all(rows + [external])
        db.commit()

        assert refresh_recent_punishment_issuers(db, limit=30) == 30

        refreshed = db.query(PlayerPunishment).filter(PlayerPunishment.issued_by_admin_id == admin.id).all()
        assert sum(row.issued_by_name == "ND. Current Server Nick" for row in refreshed) == 30
        oldest = min(refreshed, key=lambda row: row.issued_at)
        assert oldest.issued_by_name == "ND. Old Name"
        assert db.query(PlayerPunishment).filter(PlayerPunishment.player_steamid == "external").one().issued_by_name == "GameServer"
    finally:
        db.close()
        engine.dispose()
