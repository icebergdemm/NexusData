import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import models
import routes.admin as admin_routes


def _db():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine, tables=[
        models.AdminUser.__table__, models.AdminSession.__table__,
        models.AdminActionLog.__table__, models.DiscordMember.__table__,
    ])
    return engine, Session(engine)


def test_audit_sessions_groups_events_by_real_browser_session():
    engine, db = _db()
    try:
        now = datetime.utcnow()
        admin = models.AdminUser(id=1, discord_id="123", discord_username="Petr Gromov", is_superuser=True)
        db.add_all([
            admin,
            models.DiscordMember(discord_id="123", display_name="Petr Gromov"),
            models.AdminSession(id=41, admin_id=1, token="session-41", created_at=now - timedelta(minutes=20), expires_at=now + timedelta(hours=1), revoked=0),
            models.AdminSession(id=42, admin_id=1, token="session-42", created_at=now - timedelta(days=1), expires_at=now - timedelta(hours=1), revoked=0),
            models.AdminActionLog(id=1, admin_id=1, session_id=41, actor_type="web", action="login", details={}, created_at=now - timedelta(minutes=19)),
            models.AdminActionLog(id=2, admin_id=1, session_id=41, actor_type="web", action="task_delete", details={"task_id": 51}, created_at=now - timedelta(minutes=10)),
            models.AdminActionLog(id=3, admin_id=1, session_id=42, actor_type="web", action="logout", details={}, created_at=now - timedelta(days=1)),
            models.AdminActionLog(id=4, actor_type="discord", actor_discord_id="123", actor_discord_name="Petr Gromov", action="discord:close", details={}, created_at=now - timedelta(minutes=5)),
        ])
        db.commit()

        result = asyncio.run(admin_routes.api_audit_sessions(
            request=SimpleNamespace(state=SimpleNamespace(nd_session_id=41)),
            page=1,
            page_size=50,
            admin_id=None,
            actor_type=None,
            q=None,
            active=None,
            db=db,
            current_admin=admin,
        ))

        sessions = result["sessions"]
        browser = next(item for item in sessions if item["session_id"] == 41)
        expired = next(item for item in sessions if item["session_id"] == 42)
        discord = next(item for item in sessions if item["session_id"] is None)
        assert browser["event_count"] == 2
        assert browser["is_current"] is True
        assert browser["status"] == "active"
        assert expired["status"] == "expired"
        assert discord["actor"]["discord_name"] == "Petr Gromov"
    finally:
        db.close()
        engine.dispose()


def test_audit_sessions_active_filter_is_explicit_and_history_keeps_unbound_groups():
    engine, db = _db()
    try:
        now = datetime.utcnow()
        admin = models.AdminUser(id=1, discord_id="123", discord_username="Petr Gromov", is_superuser=True)
        db.add_all([
            admin,
            models.AdminSession(id=51, admin_id=1, token="session-51", created_at=now, expires_at=now + timedelta(hours=1), revoked=0),
            models.AdminSession(id=52, admin_id=1, token="session-52", created_at=now, expires_at=now + timedelta(hours=1), revoked=1),
            models.AdminActionLog(id=11, admin_id=1, session_id=51, actor_type="web", action="login", details={}, created_at=now),
            models.AdminActionLog(id=12, actor_type="discord", actor_discord_id="123", actor_discord_name="Petr Gromov", action="discord:close", details={}, created_at=now),
        ])
        db.commit()
        request = SimpleNamespace(state=SimpleNamespace(nd_session_id=51))

        active = asyncio.run(admin_routes.api_audit_sessions(
            request=request, page=1, page_size=50, admin_id=None, actor_type=None,
            q=None, active=True, db=db, current_admin=admin,
        ))
        history = asyncio.run(admin_routes.api_audit_sessions(
            request=request, page=1, page_size=50, admin_id=None, actor_type=None,
            q=None, active=False, db=db, current_admin=admin,
        ))

        assert [item["session_id"] for item in active["sessions"]] == [51]
        assert all(item["status"] != "actor" for item in active["sessions"])
        assert {item["session_id"] for item in history["sessions"]} == {51, 52, None}
    finally:
        db.close()
        engine.dispose()
