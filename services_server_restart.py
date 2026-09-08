"""Shared helpers and Pterodactyl transport for server restarts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import secrets
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from models import ServerRestartSchedule


MSK = ZoneInfo("Europe/Moscow")
MAX_MSG_BOX_LENGTH = 500
WARNING_DELAY = timedelta(seconds=15)
RESTART_LEASE = timedelta(seconds=60)
RESTART_SERVERS = ("ASPER1", "ASPER2")
DEFAULT_DAILY_TIME = "05:30"
DEFAULT_MESSAGE = "Сервер будет перезапущен через 15 секунд."


@dataclass(frozen=True)
class ScheduleDecision:
    action: str
    execution_due_at: datetime | None = None


class PterodactylError(RuntimeError):
    """A Pterodactyl request failed without exposing credentials."""


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    """Serialize a DB timestamp with an explicit UTC suffix for browsers."""
    aware = _utc_aware(value)
    return aware.isoformat().replace("+00:00", "Z") if aware else None


def normalize_server(server: str) -> str:
    normalized = (server or "").strip().upper()
    if normalized not in RESTART_SERVERS:
        raise ValueError("Неизвестный сервер (ожидается ASPER1 или ASPER2)")
    return normalized


def validate_daily_time(value: str) -> str:
    candidate = str(value or "").strip()
    try:
        hour, minute = (int(part) for part in candidate.split(":", 1))
        time(hour=hour, minute=minute)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("daily_time must use HH:MM format") from exc
    return f"{hour:02d}:{minute:02d}"


def _default_schedule(server: str) -> "ServerRestartSchedule":
    from models import ServerRestartSchedule

    return ServerRestartSchedule(
        server_name=server,
        enabled=False,
        daily_time=DEFAULT_DAILY_TIME,
        message_text=DEFAULT_MESSAGE,
        require_no_online=True,
        require_round_finished=True,
        last_status="disabled",
    )


def ensure_restart_schedules(db: "Session") -> list["ServerRestartSchedule"]:
    """Create missing rows while preserving existing operator settings."""
    from models import ServerRestartSchedule
    from sqlalchemy.exc import IntegrityError

    rows: list[ServerRestartSchedule] = []
    for server in RESTART_SERVERS:
        row = db.query(ServerRestartSchedule).filter(ServerRestartSchedule.server_name == server).first()
        if row is None:
            row = _default_schedule(server)
            db.add(row)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                row = db.query(ServerRestartSchedule).filter(ServerRestartSchedule.server_name == server).first()
        if row is not None:
            rows.append(row)
    db.commit()
    return rows


class PterodactylClient:
    def __init__(self, settings: Any | None = None):
        if settings is None:
            from config import get_settings
            settings = get_settings()
        self.settings = settings

    def _target(self, server: str) -> tuple[str, str, str]:
        server = normalize_server(server)
        panel_url = (getattr(self.settings, "pterodactyl_panel_url", "") or "").strip()
        api_key = (getattr(self.settings, "pterodactyl_api_key", "") or "").strip()
        uuid = getattr(self.settings, f"pterodactyl_server{1 if server == 'ASPER1' else 2}_uuid", "")
        if not panel_url or not api_key:
            raise PterodactylError("Интеграция с Pterodactyl не настроена")
        if not uuid:
            raise PterodactylError(f"UUID сервера {server} не настроен")
        return panel_url.rstrip("/"), api_key, uuid

    def _post(self, server: str, suffix: str, payload: dict) -> None:
        import httpx

        panel_url, api_key, uuid = self._target(server)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(
                    f"{panel_url}/api/client/servers/{uuid}/{suffix}",
                    headers=headers,
                    json=payload,
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PterodactylError(f"Ошибка обращения к Pterodactyl: {exc}") from exc

    async def _post_async(self, server: str, suffix: str, payload: dict) -> None:
        import httpx

        panel_url, api_key, uuid = self._target(server)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{panel_url}/api/client/servers/{uuid}/{suffix}",
                    headers=headers,
                    json=payload,
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PterodactylError(f"Ошибка обращения к Pterodactyl: {exc}") from exc

    def send_console_command(self, server: str, command: str) -> None:
        self._post(server, "command", {"command": command})

    async def send_console_command_async(self, server: str, command: str) -> None:
        await self._post_async(server, "command", {"command": command})

    def restart(self, server: str) -> None:
        self._post(server, "power", {"signal": "restart"})

    async def restart_async(self, server: str) -> None:
        await self._post_async(server, "power", {"signal": "restart"})


def _next_schedule(now: datetime, daily_time: str) -> datetime:
    return _utc_naive(next_daily_run_utc(_utc_aware(now), daily_time))  # type: ignore[arg-type]


def _find_schedule(db: Session, server: str) -> ServerRestartSchedule:
    from models import ServerRestartSchedule

    normalized = normalize_server(server)
    row = db.query(ServerRestartSchedule).filter(ServerRestartSchedule.server_name == normalized).first()
    if row is None:
        ensure_restart_schedules(db)
        row = db.query(ServerRestartSchedule).filter(ServerRestartSchedule.server_name == normalized).first()
    if row is None:  # pragma: no cover - protected by ensure_restart_schedules
        raise RuntimeError("Не удалось создать расписание авторестарта")
    return row


def _condition_state(db: "Session", server: str) -> tuple[int, bool]:
    from routes.dispatch import get_round_state
    from services import ServerModerationService

    online_count = len(ServerModerationService(db).get_online_players(server_name=server))
    round_active = bool(get_round_state(db, server).get("active"))
    return online_count, round_active


def _status_for_row(
    row: "ServerRestartSchedule",
    *,
    now: datetime,
    online_count: int,
    round_active: bool,
) -> str:
    if not row.enabled:
        return "disabled"
    if row.last_status == "error" and row.last_error:
        return "error"
    if row.execution_due_at is not None:
        return "warning_sent" if _utc_aware(row.execution_due_at) > _utc_aware(now) else "executing"
    if row.scheduled_for is not None and _utc_aware(row.scheduled_for) <= _utc_aware(now):
        if conditions_satisfied(
            row.require_no_online,
            row.require_round_finished,
            online_count=online_count,
            round_active=round_active,
        ):
            return "scheduled"
        return "waiting_conditions"
    return "scheduled"


def serialize_schedule(
    row: "ServerRestartSchedule",
    *,
    online_count: int,
    round_active: bool,
    now: datetime,
) -> dict:
    status = _status_for_row(row, now=now, online_count=online_count, round_active=round_active)
    return {
        "server": row.server_name,
        "enabled": bool(row.enabled),
        "daily_time": row.daily_time,
        "msg_box": row.message_text,
        "conditions": {
            "no_online": bool(row.require_no_online),
            "round_finished": bool(row.require_round_finished),
        },
        "next_run_at": iso_utc(row.execution_due_at or row.scheduled_for),
        "scheduled_for": iso_utc(row.scheduled_for),
        "online_count": online_count,
        "round_active": round_active,
        "conditions_met": conditions_satisfied(
            row.require_no_online,
            row.require_round_finished,
            online_count=online_count,
            round_active=round_active,
        ),
        "status": status,
        "last_action": row.last_action,
        "last_action_at": iso_utc(row.last_action_at),
        "last_error": row.last_error,
    }


def schedules_snapshot(db: "Session", now: datetime | None = None) -> list[dict]:
    current = now or datetime.now(timezone.utc)
    rows = ensure_restart_schedules(db)
    result = []
    for row in rows:
        online_count, round_active = _condition_state(db, row.server_name)
        result.append(
            serialize_schedule(
                row,
                online_count=online_count,
                round_active=round_active,
                now=current,
            )
        )
    return result


def save_schedule(
    db: "Session",
    server: str,
    payload: dict,
    *,
    admin_id: int | None = None,
    now: datetime | None = None,
) -> "ServerRestartSchedule":
    current = now or datetime.now(timezone.utc)
    row = _find_schedule(db, server)
    enabled_value = payload.get("enabled")
    enabled = row.enabled if enabled_value is None else bool(enabled_value)
    daily_value = payload.get("daily_time")
    daily_time = validate_daily_time(row.daily_time if daily_value is None else daily_value)
    message_value = payload.get("msg_box")
    message = str(row.message_text if message_value is None else message_value or "").strip()
    if not message:
        raise ValueError("msg_box text must not be empty")
    if len(" ".join(message.split())) > MAX_MSG_BOX_LENGTH:
        raise ValueError(f"msg_box text must be at most {MAX_MSG_BOX_LENGTH} characters")
    no_online_value = payload.get("no_online")
    round_finished_value = payload.get("round_finished")
    require_no_online = row.require_no_online if no_online_value is None else bool(no_online_value)
    require_round_finished = row.require_round_finished if round_finished_value is None else bool(round_finished_value)
    reschedule = not row.enabled or row.daily_time != daily_time or row.scheduled_for is None

    row.enabled = enabled
    row.daily_time = daily_time
    row.message_text = message
    row.require_no_online = require_no_online
    row.require_round_finished = require_round_finished
    row.last_error = None
    row.updated_by_admin_id = admin_id
    row.warning_sent_at = None
    row.execution_due_at = None
    if enabled:
        if reschedule:
            row.scheduled_for = _next_schedule(current, daily_time)
        row.last_status = "scheduled"
    else:
        row.scheduled_for = None
        row.last_status = "disabled"
    db.commit()
    db.refresh(row)
    return row


def postpone_schedule(
    db: "Session",
    server: str,
    *,
    admin_id: int | None = None,
    now: datetime | None = None,
) -> "ServerRestartSchedule":
    current = now or datetime.now(timezone.utc)
    row = _find_schedule(db, server)
    if not row.enabled:
        raise ValueError("Авторестарт выключен")
    base = row.execution_due_at or row.scheduled_for or _next_schedule(current, row.daily_time)
    row.scheduled_for = _utc_naive(_utc_aware(base) + timedelta(hours=1))  # type: ignore[arg-type]
    row.warning_sent_at = None
    row.execution_due_at = None
    row.last_status = "scheduled"
    row.last_action = "postpone"
    row.last_action_at = _utc_naive(current)
    row.last_error = None
    row.updated_by_admin_id = admin_id
    db.commit()
    db.refresh(row)
    return row


def _claim_schedule(db: "Session", row: "ServerRestartSchedule", now: datetime) -> bool:
    fresh = (
        db.query(ServerRestartSchedule)
        .filter(ServerRestartSchedule.id == row.id)
        .with_for_update()
        .first()
    )
    if fresh is None:
        return False
    if fresh.processing_until and fresh.processing_until > _utc_naive(now):
        return False
    fresh.processing_token = secrets.token_hex(16)
    fresh.processing_until = _utc_naive(now + RESTART_LEASE)
    db.commit()
    row.processing_token = fresh.processing_token
    row.processing_until = fresh.processing_until
    return True


def manual_restart(
    db: "Session",
    server: str,
    *,
    admin_id: int,
    client: PterodactylClient | None = None,
    now: datetime | None = None,
) -> dict:
    current = now or datetime.now(timezone.utc)
    row = _find_schedule(db, server)
    if not row.enabled:
        raise ValueError("Сначала включите авторестарт")
    if not _claim_schedule(db, row, current):
        raise RuntimeError("Для этого сервера уже выполняется действие")

    transport = client or PterodactylClient()
    warning_error = None
    try:
        try:
            transport.send_console_command(row.server_name, build_msg_box_command(row.message_text))
        except (PterodactylError, ValueError) as exc:
            warning_error = str(exc)
        transport.restart(row.server_name)
    except PterodactylError as exc:
        row.last_status = "error"
        row.last_error = str(exc)
        row.last_action = "manual_restart_failed"
        row.last_action_at = _utc_naive(current)
        row.processing_until = None
        row.processing_token = None
        db.commit()
        raise

    row.scheduled_for = _next_schedule(current, row.daily_time)
    row.warning_sent_at = None
    row.execution_due_at = None
    row.last_status = "scheduled"
    row.last_error = warning_error
    row.last_action = "manual_restart"
    row.last_action_at = _utc_naive(current)
    row.processing_until = None
    row.processing_token = None
    row.updated_by_admin_id = admin_id
    db.commit()
    return {"warning": warning_error, "next_run_at": iso_utc(row.scheduled_for)}


async def manual_restart_async(
    db: "Session",
    server: str,
    *,
    admin_id: int,
    client: PterodactylClient | None = None,
    now: datetime | None = None,
) -> dict:
    """Async HTTP variant used by FastAPI so Pterodactyl cannot block uvicorn."""
    current = now or datetime.now(timezone.utc)
    row = _find_schedule(db, server)
    if not row.enabled:
        raise ValueError("Сначала включите авторестарт")
    if not _claim_schedule(db, row, current):
        raise RuntimeError("Для этого сервера уже выполняется действие")

    transport = client or PterodactylClient()
    warning_error = None
    try:
        try:
            await transport.send_console_command_async(row.server_name, build_msg_box_command(row.message_text))
        except (PterodactylError, ValueError) as exc:
            warning_error = str(exc)
        await transport.restart_async(row.server_name)
    except PterodactylError as exc:
        row.last_status = "error"
        row.last_error = str(exc)
        row.last_action = "manual_restart_failed"
        row.last_action_at = _utc_naive(current)
        row.processing_until = None
        row.processing_token = None
        db.commit()
        raise

    row.scheduled_for = _next_schedule(current, row.daily_time)
    row.warning_sent_at = None
    row.execution_due_at = None
    row.last_status = "scheduled"
    row.last_error = warning_error
    row.last_action = "manual_restart"
    row.last_action_at = _utc_naive(current)
    row.processing_until = None
    row.processing_token = None
    row.updated_by_admin_id = admin_id
    db.commit()
    return {"warning": warning_error, "next_run_at": iso_utc(row.scheduled_for)}


def next_daily_run_utc(now: datetime, daily_time: str) -> datetime:
    """Return the next occurrence of ``daily_time`` interpreted as MSK."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        hour, minute = (int(part) for part in daily_time.split(":", 1))
        target_time = time(hour=hour, minute=minute)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("daily_time must use HH:MM format") from exc

    now_msk = now.astimezone(MSK)
    candidate = datetime.combine(now_msk.date(), target_time, tzinfo=MSK)
    if candidate <= now_msk:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def conditions_satisfied(
    no_online: bool,
    round_finished: bool,
    *,
    online_count: int,
    round_active: bool,
) -> bool:
    """Apply the UI's OR semantics; no selected guard means time-only mode."""
    enabled_results = []
    if no_online:
        enabled_results.append(online_count == 0)
    if round_finished:
        enabled_results.append(not round_active)
    return not enabled_results or any(enabled_results)


def evaluate_schedule_tick(
    *,
    enabled: bool,
    scheduled_for: datetime | None,
    execution_due_at: datetime | None,
    warning_sent_at: datetime | None,
    no_online: bool,
    round_finished: bool,
    online_count: int,
    round_active: bool,
    now: datetime,
) -> ScheduleDecision:
    """Decide the next durable action for one scheduler tick."""
    if not enabled or scheduled_for is None:
        return ScheduleDecision("noop", execution_due_at)
    if execution_due_at is not None:
        if now >= execution_due_at:
            return ScheduleDecision("restart", execution_due_at)
        return ScheduleDecision("noop", execution_due_at)
    if now < scheduled_for:
        return ScheduleDecision("noop", None)
    if not conditions_satisfied(
        no_online,
        round_finished,
        online_count=online_count,
        round_active=round_active,
    ):
        return ScheduleDecision("wait_conditions", None)
    return ScheduleDecision("send_warning", now + WARNING_DELAY)


def build_msg_box_command(message: str) -> str:
    """Build a safe Pterodactyl console command for the configured message."""
    normalized = " ".join((message or "").split())
    if not normalized:
        raise ValueError("msg_box text must not be empty")
    if len(normalized) > MAX_MSG_BOX_LENGTH:
        raise ValueError(f"msg_box text must be at most {MAX_MSG_BOX_LENGTH} characters")
    escaped = normalized.replace("\\", "\\\\").replace('"', '\\"')
    return f'msg_box "{escaped}"'
