from types import SimpleNamespace
import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import models
import routes.admin as admin_routes

from middleware import audit_fallback_action, should_capture_audit_request


def _request(method: str, path: str):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path), state=SimpleNamespace(audit_logged=False))


def test_audit_capture_covers_meaningful_mutations_only():
    assert should_capture_audit_request(_request("POST", "/api/v1/admin/tasks/51"), 200)
    assert should_capture_audit_request(_request("DELETE", "/api/v1/admin/tickets/12"), 204)
    assert not should_capture_audit_request(_request("GET", "/api/v1/admin/tasks"), 200)
    assert not should_capture_audit_request(_request("GET", "/healthz"), 200)
    assert not should_capture_audit_request(_request("GET", "/api/v1/admin/security/overview"), 200)


def test_audit_capture_does_not_duplicate_explicit_events():
    request = _request("POST", "/api/v1/admin/tasks/51")
    request.state.audit_logged = True
    assert not should_capture_audit_request(request, 200)


def test_fallback_action_contains_method_and_route_without_query_data():
    assert audit_fallback_action(_request("PATCH", "/api/v1/admin/groups/4")) == "http.patch.admin.groups"


def test_sensitive_view_endpoint_records_one_web_event():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine, tables=[models.AdminUser.__table__, models.AdminActionLog.__table__])
    db = Session(engine)
    admin = models.AdminUser(id=1, discord_id="123", discord_username="Petr", is_superuser=True)
    db.add(admin)
    db.commit()
    request = SimpleNamespace(
        method="POST",
        scope={"path": "/api/v1/admin/audit/view"},
        state=SimpleNamespace(nd_session_id=41, nd_auth_type="cookie"),
        headers={},
        client=SimpleNamespace(host="203.0.113.10"),
    )
    try:
        result = asyncio.run(admin_routes.api_audit_view(
            request=request,
            view_key="player_profile",
            route="/players/44",
            db=db,
            current_admin=admin,
        ))
        assert result["recorded"] is True
        event = db.query(models.AdminActionLog).one()
        assert event.action == "view_player_profile"
        assert event.session_id == 41
        assert event.actor_type == "web"
    finally:
        db.close()
        engine.dispose()
