"""Small, central writer for administrator, Discord and system audit events."""

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional
import uuid

from sqlalchemy.orm import Session
from sqlalchemy import or_, select


_current_request: ContextVar[object | None] = ContextVar("nd_audit_request", default=None)


def set_current_audit_request(request):
    return _current_request.set(request)


def reset_current_audit_request(token) -> None:
    _current_request.reset(token)


@dataclass(frozen=True)
class ActorSnapshot:
    actor_type: str
    discord_id: Optional[str] = None
    discord_name: Optional[str] = None
    nexus_username: Optional[str] = None
    display_name: str = "Системное событие"


def safe_audit_details(value: Any) -> Any:
    """Use the existing redaction policy without importing security eagerly."""
    from security import sanitize_audit_details

    return sanitize_audit_details(value)


def _discord_member(db: Session, discord_id: Optional[str]):
    if not discord_id:
        return None
    from models import DiscordMember

    try:
        return db.query(DiscordMember).filter(DiscordMember.discord_id == str(discord_id)).first()
    except Exception:
        # Additive migrations and narrow unit schemas may not have the cache
        # table yet; the AdminUser snapshot remains a valid fallback.
        db.rollback()
        return None


def resolve_actor_snapshot(
    db: Session,
    *,
    admin=None,
    actor_type: Optional[str] = None,
    actor_discord_id: Optional[str] = None,
    actor_discord_name: Optional[str] = None,
    actor_nexus_username: Optional[str] = None,
) -> ActorSnapshot:
    """Resolve a truthful actor label, never inventing an admin for bot events."""
    discord_id = str(actor_discord_id or getattr(admin, "discord_id", "") or "").strip() or None
    member = _discord_member(db, discord_id)
    discord_name = (
        getattr(member, "display_name", None)
        or actor_discord_name
        or getattr(admin, "discord_username", None)
        or getattr(member, "username", None)
    )
    discord_name = str(discord_name).strip() if discord_name else None
    nexus_name = actor_nexus_username or getattr(admin, "discord_username", None)
    nexus_name = str(nexus_name).strip() if nexus_name else None
    resolved_type = actor_type or ("web" if admin is not None else "system")

    if resolved_type == "system":
        return ActorSnapshot("system", display_name="Системное событие", nexus_username="Система")
    if resolved_type == "discord":
        fallback = f"Discord-актор · ID {discord_id}" if discord_id else "Discord-актор"
        return ActorSnapshot("discord", discord_id, discord_name, nexus_name, discord_name or fallback)
    fallback = f"Discord-актор · ID {discord_id}" if discord_id else "Веб-актор"
    return ActorSnapshot("web", discord_id, discord_name, nexus_name, discord_name or fallback)


def _request_context(request, *, outcome: str, reason: Optional[str], severity: str) -> dict:
    if request is None:
        return {"outcome": outcome, "reason": reason, "severity": severity}

    headers = request.headers
    return {
        "auth_type": getattr(request.state, "nd_auth_type", None),
        "session_id": getattr(request.state, "nd_session_id", None),
        "login_key_id": getattr(request.state, "nd_login_key_id", None),
        "request_id": (headers.get("x-request-id") or getattr(request.state, "request_id", None) or uuid.uuid4().hex)[:128],
        "source_ip": request.client.host if request.client else None,
        "user_agent": (headers.get("user-agent") or "")[:500] or None,
        "route": request.scope.get("path"),
        "http_method": request.method,
        "outcome": outcome,
        "reason": reason,
        "severity": severity,
    }


def record_audit_event(
    db: Session,
    action: str,
    details: Optional[dict] = None,
    *,
    request=None,
    admin=None,
    admin_id: Optional[int] = None,
    actor_type: Optional[str] = None,
    actor_discord_id: Optional[str] = None,
    actor_discord_name: Optional[str] = None,
    actor_nexus_username: Optional[str] = None,
    outcome: str = "success",
    reason: Optional[str] = None,
    severity: str = "info",
):
    from models import AdminActionLog

    request = request or _current_request.get()

    # During an additive/rolling migration an auth failure must not become a
    # 500 merely because the audit table is not present on this worker yet.
    from sqlalchemy import inspect
    if db.bind is not None and not inspect(db.bind).has_table("admin_action_logs"):
        return None

    if admin is None and admin_id is not None:
        from models import AdminUser

        admin = db.query(AdminUser).filter(AdminUser.id == admin_id).first()

    actor = resolve_actor_snapshot(
        db,
        admin=admin,
        actor_type=actor_type,
        actor_discord_id=actor_discord_id,
        actor_discord_name=actor_discord_name,
        actor_nexus_username=actor_nexus_username,
    )
    context = _request_context(request, outcome=outcome, reason=reason, severity=severity)
    event = AdminActionLog(
        admin_id=admin_id if admin_id is not None else getattr(admin, "id", None),
        action=action,
        details=safe_audit_details(details or {}),
        actor_type=actor.actor_type,
        actor_discord_id=actor.discord_id,
        actor_discord_name=actor.discord_name,
        actor_nexus_username=actor.nexus_username,
        **context,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    if request is not None:
        request.state.audit_logged = True
    return event


AUDIT_ACTION_LABELS = {
    "login": "Вход в панель",
    "logout": "Выход из панели",
    "csrf_failure": "Ошибка проверки CSRF",
    "login_key_create": "Создан ключ входа",
    "login_key_revoke": "Отозван ключ входа",
    "login_key_use": "Использован ключ входа",
    "revoke_session": "Отозвана сессия",
    "task_delete": "Удалена задача",
    "ticket_delete": "Удалён тикет",
    "punishment_create": "Создано наказание",
    "punishment_void": "Аннулировано наказание",
    "moderation.kick": "Исключён игрок с сервера",
    "moderation.ban": "Заблокирован игрок",
    "view_player_profile": "Открыт профиль игрока",
    "view_player_ip": "Просмотрен IP игрока",
    "web_lockdown_change": "Изменён режим шатдауна",
}


def audit_action_label(action: str) -> str:
    if action in AUDIT_ACTION_LABELS:
        return AUDIT_ACTION_LABELS[action]
    if action.startswith("discord:"):
        return f"Discord: {action.removeprefix('discord:').replace('_', ' ')}"
    return action.replace("_", " ").replace(".", " · ").capitalize()


def _event_target(action: str, details: dict) -> Optional[dict]:
    target = details.get("target") if isinstance(details.get("target"), dict) else None
    if target and target.get("id") is not None:
        return {"type": str(target.get("type") or "object"), "id": target["id"], "label": target.get("label")}

    pairs = (
        ("login_key", "key_id", "ключ входа"),
        ("task", "task_id", "задача"),
        ("ticket", "ticket_id", "тикет"),
        ("punishment", "punishment_id", "наказание"),
        ("player", "player_id", "игрок"),
        ("session", "session_id", "сессия"),
        ("admin", "target_admin_id", "администратор"),
    )
    for target_type, key, label in pairs:
        if details.get(key) is not None:
            value = details[key]
            return {"type": target_type, "id": value, "label": f"{label} #{value}"}
    return None


def format_audit_event(event, *, include_raw: bool = False) -> dict:
    """Convert persisted details into a stable UI contract."""
    details = safe_audit_details(getattr(event, "details", None) or {})
    action = getattr(event, "action", "event") or "event"
    changes = details.get("changes") if isinstance(details.get("changes"), list) else []
    if not changes and isinstance(details.get("before"), dict) and isinstance(details.get("after"), dict):
        fields = set(details["before"]) | set(details["after"])
        changes = [
            {"field": field, "from": details["before"].get(field), "to": details["after"].get(field)}
            for field in sorted(fields)
            if details["before"].get(field) != details["after"].get(field)
        ]
    source = "Discord" if action.startswith("discord:") else "Панель"
    actor_name = getattr(event, "actor_discord_name", None) or getattr(event, "actor_nexus_username", None)
    return {
        "id": getattr(event, "id", None),
        "summary": audit_action_label(action),
        "action": action,
        "target": _event_target(action, details),
        "changes": changes,
        "reason": getattr(event, "reason", None) or details.get("reason"),
        "actor": {
            "type": getattr(event, "actor_type", None) or ("web" if getattr(event, "admin_id", None) else "system"),
            "discord_id": getattr(event, "actor_discord_id", None),
            "discord_name": actor_name,
            "nexus_username": getattr(event, "actor_nexus_username", None),
        },
        "context": {
            "source": source,
            "auth_type": getattr(event, "auth_type", None),
            "session_id": getattr(event, "session_id", None),
            "request_id": getattr(event, "request_id", None),
            "source_ip": getattr(event, "source_ip", None),
            "user_agent": getattr(event, "user_agent", None),
        },
        "created_at": getattr(event, "created_at", None).isoformat() if getattr(event, "created_at", None) else None,
        "outcome": getattr(event, "outcome", None),
        "severity": getattr(event, "severity", None),
        "raw": details if include_raw else None,
    }


def prune_audit_logs(db: Session, now: datetime, retention_days: int = 365) -> int:
    """Delete old audit rows without cutting an active browser chain in half."""
    from models import AdminActionLog, AdminSession

    cutoff = now - timedelta(days=max(1, retention_days))
    active_session_ids = select(AdminSession.id).where(
        AdminSession.revoked == 0,
        or_(AdminSession.expires_at.is_(None), AdminSession.expires_at > now),
    )
    query = db.query(AdminActionLog).filter(
        AdminActionLog.created_at < cutoff,
        or_(
            AdminActionLog.session_id.is_(None),
            ~AdminActionLog.session_id.in_(active_session_ids),
        ),
    )
    deleted_count = query.count()
    if not deleted_count:
        return 0

    query.delete(synchronize_session=False)
    db.commit()
    record_audit_event(
        db,
        "audit_retention_prune",
        {
            "deleted_count": deleted_count,
            "retention_days": retention_days,
            "cutoff": cutoff.isoformat(),
        },
        actor_type="system",
    )
    return deleted_count
