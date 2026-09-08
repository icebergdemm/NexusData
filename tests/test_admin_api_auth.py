import pytest
from fastapi import HTTPException
import sys
import types

if sys.platform == "win32" and "fcntl" not in sys.modules:
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX = 2
    fcntl.LOCK_UN = 8
    fcntl.flock = lambda *_args, **_kwargs: None
    sys.modules["fcntl"] = fcntl
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
import models
import middleware
from routes.admin import load_current_admin
from services_login_keys import create_key, resolve_api_login_key


def test_api_lookup_validates_key_without_usage_write():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine, tables=[models.AdminUser.__table__, models.AdminLoginKey.__table__])
    db = Session(engine)
    try:
        db.add(models.AdminUser(id=7, discord_id="api-user", discord_username="API user"))
        db.commit()
        row, raw = create_key(db, 7, None)

        assert resolve_api_login_key(db, raw) == (row.id, 7)
        db.refresh(row)
        assert row.use_count == 0
        assert row.last_used_at is None
    finally:
        db.close()
        engine.dispose()


def test_invalid_login_header_does_not_fall_back_to_cookie_session(monkeypatch):
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine, tables=[models.AdminUser.__table__, models.AdminSession.__table__])
    db = Session(engine)
    try:
        db.add(models.AdminUser(id=1, discord_id="cookie-user", discord_username="Cookie user"))
        db.add(models.AdminSession(admin_id=1, token="cookie", revoked=0))
        db.commit()
        monkeypatch.setattr(middleware, "resolve_api_login_key", lambda _raw: None)

        from starlette.requests import Request
        scope = {
            "type": "http", "method": "GET", "path": "/api/whoami",
            "headers": [(b"x-nd-login-key", b"bad"), (b"cookie", b"admin_token=cookie")], "query_string": b"",
            "scheme": "http", "server": ("test", 80), "client": ("127.0.0.1", 1),
            "session": {},
        }
        request = Request(scope)
        request.state.nd_auth_type = "invalid_login_key"
        with pytest.raises(HTTPException) as error:
            load_current_admin(request, db)
        assert error.value.status_code == 401
    finally:
        db.close()
        engine.dispose()
