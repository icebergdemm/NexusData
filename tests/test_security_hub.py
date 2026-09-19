import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import models
import routes.admin as admin_routes


def _log(action, admin_id=1, created_at=None, details=None, admin_name="Petr"):
    return SimpleNamespace(
        id=1,
        action=action,
        admin_id=admin_id,
        admin_name=admin_name,
        created_at=created_at or datetime(2026, 1, 1, 12, 0),
        details=details or {},
        reason=None,
        severity="info",
        outcome="success",
        auth_type="cookie",
    )


def test_security_activity_whitelist_keeps_dangerous_actions_and_hides_views():
    assert admin_routes.is_security_activity_action("login") is True
    assert admin_routes.is_security_activity_action("task_delete") is True
    assert admin_routes.is_security_activity_action("moderation.kick") is True
    assert admin_routes.is_security_activity_action("view_player_profile") is False
    assert admin_routes.is_security_activity_action("rating_adjust") is False


def test_security_activity_groups_adjacent_actions_for_same_admin():
    start = datetime(2026, 1, 1, 12, 0)
    logs = [
        _log("task_delete", created_at=start, details={"task_id": 51}),
        _log("task_delete", created_at=start + timedelta(minutes=2), details={"task_id": 67}),
        _log("login", created_at=start + timedelta(minutes=3)),
    ]

    grouped = admin_routes.group_security_activity(logs)

    assert len(grouped) == 2
    assert grouped[1]["action"] == "task_delete"
    assert grouped[1]["count"] == 2
    assert [item["task_id"] for item in grouped[1]["details"]] == [67, 51]


def test_security_attention_groups_failures_within_last_hour():
    now = datetime(2026, 1, 1, 13, 0)
    logs = [
        _log("login_key_failure", created_at=now - timedelta(minutes=10)),
        _log("login_key_failure", created_at=now - timedelta(minutes=20)),
        _log("csrf_failure", created_at=now - timedelta(minutes=5)),
        _log("login_key_failure", created_at=now - timedelta(hours=2)),
    ]

    attention = admin_routes.build_security_attention(logs, now=now)

    assert [(item["action"], item["count"]) for item in attention] == [
        ("csrf_failure", 1),
        ("login_key_failure", 2),
    ]


def test_active_session_requires_not_revoked_and_not_expired():
    now = datetime(2026, 1, 1, 13, 0)
    assert admin_routes.is_active_admin_session(
        SimpleNamespace(revoked=0, expires_at=now + timedelta(minutes=1)), now
    )
    assert not admin_routes.is_active_admin_session(
        SimpleNamespace(revoked=1, expires_at=now + timedelta(minutes=1)), now
    )
    assert not admin_routes.is_active_admin_session(
        SimpleNamespace(revoked=0, expires_at=now - timedelta(seconds=1)), now
    )


def test_security_hub_requires_superuser():
    with pytest.raises(HTTPException) as error:
        admin_routes._require_superuser(SimpleNamespace(is_superuser=False))

    assert error.value.status_code == 403


def test_sessions_api_defaults_to_active_and_marks_current_session():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine, tables=[models.AdminUser.__table__, models.AdminSession.__table__])
    db = Session(engine)
    try:
        now = datetime.utcnow()
        db.add(models.AdminUser(id=1, discord_id="root", discord_username="Root", is_superuser=True))
        db.add_all([
            models.AdminSession(id=10, admin_id=1, created_at=now, expires_at=now + timedelta(hours=1), revoked=0),
            models.AdminSession(id=11, admin_id=1, created_at=now, expires_at=now + timedelta(hours=1), revoked=1),
        ])
        db.commit()
        request = SimpleNamespace(state=SimpleNamespace(nd_session_id=10))

        result = asyncio.run(admin_routes.api_sessions_list(request, active=True, admin_id=None, db=db, current_admin=db.get(models.AdminUser, 1)))

        assert [item["id"] for item in result["sessions"]] == [10]
        assert result["sessions"][0]["is_current"] is True
    finally:
        db.close()
        engine.dispose()


def test_audit_api_filters_views_and_discord_id_search():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine, tables=[models.AdminUser.__table__, models.AdminActionLog.__table__])
    db = Session(engine)
    try:
        admin = models.AdminUser(id=1, discord_id="123456", discord_username="Root", is_superuser=True)
        db.add(admin)
        db.add_all([
            models.AdminActionLog(admin_id=1, action="view_player_profile", created_at=datetime.utcnow(), details={}),
            models.AdminActionLog(admin_id=1, action="task_delete", created_at=datetime.utcnow(), details={}),
        ])
        db.commit()
        request = SimpleNamespace()
        response = asyncio.run(admin_routes.api_audit_logs(
            request=request, source="panel", page=1, page_size=50, q="123456", category=None,
            admin_id=None, admin=1, date_from=None, date_to=None, from_date=None, to_date=None,
            action=None, actions=["task_delete", "view_player_profile"], exclude_views=True,
            auth_type=None, outcome=None, severity=None, db=db, current_admin=admin,
        ))

        assert response.body
        assert b"task_delete" in response.body
        assert b"view_player_profile" not in response.body
    finally:
        db.close()
        engine.dispose()
