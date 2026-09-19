"""Fail-closed emergency gate for browser/admin access only."""

import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import or_, select

from models import AdminSession, AdminUser
from security import get_active_admin_session

logger = logging.getLogger(__name__)

_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "web_lockdown.json")
_STATE_VERSION = 1
_lock = threading.Lock()
_cache: Optional["WebLockdownState"] = None
_cache_read_at = 0.0


@dataclass(frozen=True)
class WebLockdownState:
    enabled: bool = False
    message: str = ""
    updated_at: str = ""


def _disabled_state() -> WebLockdownState:
    return WebLockdownState(updated_at=datetime.utcnow().isoformat())


def _parse(raw) -> WebLockdownState:
    if not isinstance(raw, dict):
        raise ValueError("marker is not an object")
    if raw.get("version") != _STATE_VERSION:
        raise ValueError("unsupported marker version")
    if not isinstance(raw.get("enabled"), bool):
        raise ValueError("marker enabled must be boolean")
    message = raw.get("message", "")
    updated_at = raw.get("updated_at", "")
    if not isinstance(message, str) or not isinstance(updated_at, str):
        raise ValueError("marker text fields are invalid")
    return WebLockdownState(enabled=raw["enabled"], message=message[:255], updated_at=updated_at[:64])


def _read_from_disk() -> WebLockdownState:
    if not os.path.exists(_STATE_FILE):
        return _disabled_state()
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as handle:
            return _parse(json.load(handle))
    except Exception as exc:
        logger.critical("Web lockdown marker is unreadable; failing closed: %s", exc)
        return WebLockdownState(
            enabled=True,
            message="Режим аварийной блокировки включён: маркер повреждён.",
            updated_at=datetime.utcnow().isoformat(),
        )


def read_state(force: bool = False) -> WebLockdownState:
    global _cache, _cache_read_at
    now = time.monotonic()
    if not force and _cache is not None and now - _cache_read_at < 1.0:
        return _cache
    with _lock:
        now = time.monotonic()
        if not force and _cache is not None and now - _cache_read_at < 1.0:
            return _cache
        _cache = _read_from_disk()
        _cache_read_at = now
        return _cache


def write_state(enabled: bool, message: str = "") -> WebLockdownState:
    global _cache, _cache_read_at
    state = WebLockdownState(
        enabled=bool(enabled),
        message=(message or "").strip()[:255],
        updated_at=datetime.utcnow().isoformat(),
    )
    payload = {
        "version": _STATE_VERSION,
        "enabled": state.enabled,
        "message": state.message,
        "updated_at": state.updated_at,
    }
    with _lock:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        tmp_path = _STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, _STATE_FILE)
        _cache = state
        _cache_read_at = time.monotonic()
    return state


def _valid_service_key(request, settings) -> bool:
    provided = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if not provided:
        return False
    for candidate in (getattr(settings, "api_key", ""), getattr(settings, "client_api_key", "")):
        if candidate and secrets.compare_digest(str(provided), str(candidate)):
            return True
    return False


def can_access_web(request, db, settings, touch_session: bool = True) -> bool:
    """Return whether a request may pass the lockdown gate."""
    if _valid_service_key(request, settings):
        return True

    login_key = request.headers.get("X-ND-Login-Key")
    if login_key is not None:
        from services_login_keys import resolve_api_login_key

        identity = resolve_api_login_key(db, login_key, request.client.host if request.client else None)
        if not identity:
            return False
        admin = db.query(AdminUser).filter(AdminUser.id == identity[1]).first()
        return bool(admin and not getattr(admin, "is_deleted", 0) and admin.is_superuser)

    token = request.cookies.get("admin_token")
    if not token:
        return False
    session = get_active_admin_session(db, token, request=request, touch=touch_session)
    if not session:
        return False
    admin = db.query(AdminUser).filter(
        AdminUser.id == session.admin_id,
        AdminUser.is_deleted == 0,
    ).first()
    return bool(admin and admin.is_superuser)


def revoke_non_superuser_sessions(db, exclude_session_id: Optional[int] = None) -> int:
    non_superuser_ids = select(AdminUser.id).where(or_(
        AdminUser.is_superuser.is_(False),
        AdminUser.is_superuser.is_(None),
    ))
    query = db.query(AdminSession).filter(
        AdminSession.admin_id.in_(non_superuser_ids),
        AdminSession.revoked == 0,
    )
    if exclude_session_id is not None:
        query = query.filter(AdminSession.id != exclude_session_id)
    affected = query.update({"revoked": 1}, synchronize_session=False)
    db.commit()
    return affected
