from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import AdminUser, DiscordMember
from services import format_admin_issuer_name


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
