from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import models
from audit_service import record_audit_event


def _request(session_id: int, auth_type: str = "cookie") -> Request:
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/admin/tasks/51",
        "headers": [
            (b"x-request-id", b"request-51"),
            (b"user-agent", b"Audit Test Browser"),
        ],
        "client": ("203.0.113.10", 443),
        "scheme": "https",
    })
    request.state.nd_auth_type = auth_type
    request.state.nd_session_id = session_id
    return request


def _db() -> Session:
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine, tables=[
        models.AdminUser.__table__,
        models.AdminSession.__table__,
        models.AdminActionLog.__table__,
        models.DiscordMember.__table__,
    ])
    return Session(engine)


def test_record_event_snapshots_discord_and_nexus_identity_for_browser_session():
    db = _db()
    try:
        admin = models.AdminUser(id=7, discord_id="123", discord_username="Petr Gromov")
        db.add_all([
            admin,
            models.DiscordMember(discord_id="123", username="petr.g", display_name="Петр Громов"),
            models.AdminSession(id=41, admin_id=7, token="hashed", expires_at=datetime.utcnow() + timedelta(hours=1)),
        ])
        db.commit()

        event = record_audit_event(
            db,
            "task_delete",
            {"task_id": 51, "token": "must-not-persist"},
            request=_request(41),
            admin=admin,
        )

        assert event.admin_id == 7
        assert event.session_id == 41
        assert event.actor_type == "web"
        assert event.actor_discord_id == "123"
        assert event.actor_discord_name == "Петр Громов"
        assert event.actor_nexus_username == "Petr Gromov"
        assert event.details == {"task_id": 51}
        assert event.request_id == "request-51"
        assert event.route == "/api/v1/admin/tasks/51"
        assert event.http_method == "POST"
    finally:
        db.close()


def test_record_event_keeps_discord_actor_without_fake_browser_session():
    db = _db()
    try:
        db.add(models.DiscordMember(discord_id="555", username="volkov", display_name="Мичман Дмитрий Волков"))
        db.commit()

        event = record_audit_event(
            db,
            "discord:close",
            {"ticket_id": 183},
            actor_type="discord",
            actor_discord_id="555",
        )

        assert event.admin_id is None
        assert event.session_id is None
        assert event.actor_type == "discord"
        assert event.actor_discord_name == "Мичман Дмитрий Волков"
        assert event.actor_nexus_username is None
    finally:
        db.close()


def test_record_event_uses_stable_system_actor_label():
    db = _db()
    try:
        event = record_audit_event(db, "audit_retention_prune", {"deleted_count": 4}, actor_type="system")
        assert event.actor_type == "system"
        assert event.actor_nexus_username == "Система"
        assert event.session_id is None
    finally:
        db.close()
