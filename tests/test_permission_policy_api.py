from types import SimpleNamespace
import sys
import types

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
from routes import admin
from security import DENY_PERMISSION_POLICY, LEGACY_PERMISSION_POLICY


def _client():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    db = Session(engine)
    db.add(models.AdminUser(id=1, discord_id="root", discord_username="Root", is_superuser=True))
    db.add(models.AdminUser(id=2, discord_id="moderator", discord_username="Moderator"))
    db.commit()

    app = FastAPI()

    @app.middleware("http")
    async def api_auth_context(request, call_next):
        request.state.nd_auth_type = "login_key"
        return await call_next(request)

    app.include_router(admin.router)
    app.dependency_overrides[admin.get_db] = lambda: db
    app.dependency_overrides[admin.get_current_admin] = lambda: SimpleNamespace(id=1, is_superuser=True)
    return TestClient(app), db, engine


def test_policy_impact_is_read_only_and_apply_requires_current_digest():
    client, db, engine = _client()
    try:
        before = db.query(models.AppSetting).filter_by(key="permission_policy_version").first()
        assert before is None

        impact = client.get("/api/v1/admin/permission-policy/impact")
        assert impact.status_code == 200
        payload = impact.json()
        assert payload["current_policy"] == LEGACY_PERMISSION_POLICY
        assert payload["target_policy"] == DENY_PERMISSION_POLICY
        assert payload["digest"]

        stale = client.post("/api/v1/admin/permission-policy/apply", json={
            "expected_digest": "stale",
            "confirmation": "DENY_POLICY_V2",
        })
        assert stale.status_code == 409
        assert db.query(models.AppSetting).filter_by(key="permission_policy_version").first() is None

        applied = client.post("/api/v1/admin/permission-policy/apply", json={
            "expected_digest": payload["digest"],
            "confirmation": "DENY_POLICY_V2",
        })
        assert applied.status_code == 200
        assert applied.json()["policy"] == DENY_PERMISSION_POLICY
        assert db.query(models.AppSetting).filter_by(key="permission_policy_version").one().value == DENY_PERMISSION_POLICY
    finally:
        db.close()
        engine.dispose()
