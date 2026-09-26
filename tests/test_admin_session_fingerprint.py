from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

import models
from routes.admin import _issue_admin_session
from security import (
    get_active_admin_session,
    hash_admin_browser_fingerprint,
    hash_admin_session_token,
)


def _database():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine, tables=[models.AdminSession.__table__])
    return engine, Session(engine)


def _request(browser_fingerprint=None, cookie_fingerprint=None):
    cookie = "admin_token=raw-token"
    if cookie_fingerprint is not None:
        cookie += f"; nd_browser_fingerprint={cookie_fingerprint}"
    headers = [(b"cookie", cookie.encode())]
    if browser_fingerprint is not None:
        headers.append((b"x-nd-browser-fingerprint", browser_fingerprint.encode()))
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/v1/me",
        "headers": headers,
        "query_string": b"",
        "scheme": "https",
        "server": ("nexusdata.test", 443),
        "client": ("127.0.0.1", 1234),
        "session": {},
    })


def _add_session(db, **values):
    defaults = {
        "admin_id": 1,
        "token": hash_admin_session_token("raw-token"),
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(days=14),
        "last_seen_at": datetime.utcnow(),
        "revoked": 0,
    }
    defaults.update(values)
    session = models.AdminSession(**defaults)
    db.add(session)
    db.commit()
    return session


def test_fingerprinted_session_survives_more_than_two_idle_hours():
    engine, db = _database()
    try:
        now = datetime.utcnow()
        session = _add_session(db, last_seen_at=now - timedelta(hours=3))
        session.browser_fingerprint_bound = True
        session.browser_fingerprint_hash = hash_admin_browser_fingerprint("browser-id")

        active = get_active_admin_session(db, "raw-token", _request("browser-id"))

        assert active is session
        assert session.revoked == 0
    finally:
        db.close()
        engine.dispose()


def test_fingerprinted_session_is_revoked_when_fingerprint_changes():
    engine, db = _database()
    try:
        session = _add_session(db)
        session.browser_fingerprint_bound = True
        session.browser_fingerprint_hash = hash_admin_browser_fingerprint("expected-browser")

        active = get_active_admin_session(db, "raw-token", _request("different-browser"))

        assert active is None
        assert session.revoked == 1
    finally:
        db.close()
        engine.dispose()


def test_fingerprinted_session_is_revoked_when_fingerprint_is_missing():
    engine, db = _database()
    try:
        session = _add_session(db)
        session.browser_fingerprint_bound = True
        session.browser_fingerprint_hash = hash_admin_browser_fingerprint("browser-id")

        active = get_active_admin_session(db, "raw-token", _request())

        assert active is None
        assert session.revoked == 1
    finally:
        db.close()
        engine.dispose()


def test_fingerprinted_session_rejects_disagreeing_header_and_cookie():
    engine, db = _database()
    try:
        session = _add_session(db)
        session.browser_fingerprint_bound = True
        session.browser_fingerprint_hash = hash_admin_browser_fingerprint("browser-id")

        active = get_active_admin_session(
            db, "raw-token", _request("browser-id", cookie_fingerprint="other-browser")
        )

        assert active is None
        assert session.revoked == 1
    finally:
        db.close()
        engine.dispose()


def test_legacy_session_without_fingerprint_is_revoked():
    engine, db = _database()
    try:
        session = _add_session(db)

        active = get_active_admin_session(db, "raw-token", _request("browser-id"))

        assert active is None
        assert session.revoked == 1
    finally:
        db.close()
        engine.dispose()


def test_three_day_fallback_session_is_accepted_until_its_absolute_expiry():
    engine, db = _database()
    try:
        session = _add_session(
            db,
            expires_at=datetime.utcnow() + timedelta(days=2),
            browser_fingerprint_hash=None,
            browser_fingerprint_bound=False,
        )
        assert get_active_admin_session(db, "raw-token", _request("new-browser-id")) is session

        session.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()

        assert get_active_admin_session(db, "raw-token", _request()) is None
    finally:
        db.close()
        engine.dispose()


def test_login_without_fingerprint_issues_three_day_session():
    engine, db = _database()
    try:
        admin = SimpleNamespace(id=1, last_login=None)
        cookie = {}
        response = SimpleNamespace(set_cookie=lambda name, value, **options: cookie.update({"name": name, "value": value, **options}))
        request = Request({
            "type": "http",
            "method": "POST",
            "path": "/slogin",
            "headers": [],
            "query_string": b"",
            "scheme": "https",
            "server": ("nexusdata.test", 443),
            "client": ("127.0.0.1", 1234),
            "session": {},
        })
        before = datetime.utcnow()

        _issue_admin_session(db, admin, response, request, "login_key")

        session = db.query(models.AdminSession).one()
        assert session.browser_fingerprint_bound is False
        assert timedelta(days=2, hours=23) < session.expires_at - before <= timedelta(days=3)
        assert cookie["expires"] == session.expires_at
        assert "max_age" not in cookie
    finally:
        db.close()
        engine.dispose()


def test_login_with_fingerprint_issues_fourteen_day_session():
    engine, db = _database()
    try:
        admin = SimpleNamespace(id=1, last_login=None)
        cookie = {}
        response = SimpleNamespace(set_cookie=lambda name, value, **options: cookie.update({"name": name, "value": value, **options}))
        request = Request({
            "type": "http",
            "method": "POST",
            "path": "/slogin",
            "headers": [(b"cookie", b"nd_browser_fingerprint=browser-id")],
            "query_string": b"",
            "scheme": "https",
            "server": ("nexusdata.test", 443),
            "client": ("127.0.0.1", 1234),
            "session": {},
        })
        before = datetime.utcnow()

        _issue_admin_session(db, admin, response, request, "discord_oauth")

        session = db.query(models.AdminSession).one()
        assert session.browser_fingerprint_bound is True
        assert session.browser_fingerprint_hash
        assert session.browser_fingerprint_hash != "browser-id"
        assert timedelta(days=13, hours=23) < session.expires_at - before <= timedelta(days=14)
        assert cookie["expires"] == session.expires_at
        assert "max_age" not in cookie
        assert session.auth_type == "discord_oauth"
    finally:
        db.close()
        engine.dispose()
