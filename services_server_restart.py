"""Shared helpers and Pterodactyl transport for server restarts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import secrets
import threading
import time as monotonic_clock
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
STATE_CACHE_TTL_SECONDS = 30.0
RUNNING_SERVER_STATE = "running"
DEFAULT_DAILY_TIME = "05:30"
DEFAULT_MESSAGE = "Сервер будет перезапущен через 15 секунд."


@dataclass(frozen=True)
class ScheduleDecision:
    action: str
    execution_due_at: datetime | None = None


class PterodactylError(RuntimeError):
    """A Pterodactyl request failed without exposing credentials."""


_state_cache_lock = threading.Lock()
_state_cache: dict[tuple[str, str], tuple[float, str | None]] = {}


def clear_state_cache() -> None:
    """Clear cached panel state after configuration changes and in tests."""
    with _state_cache_lock:
        _state_cache.clear()


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


def server_state_allows_restart(state: str | None) -> bool:
    """Only a fully running Pterodactyl server may enter restart flow."""
    return (state or "").strip().lower() == RUNNING_SERVER_STATE


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
        self._sync_client = None

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

    def _get(self, server: str, suffix: str) -> dict:
        import httpx

        panel_url, api_key, uuid = self._target(server)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        try:
            if self._sync_client is None:
                self._sync_client = httpx.Client(timeout=10.0)
            response = self._sync_client.get(
                f"{panel_url}/api/client/servers/{uuid}/{suffix}",
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PterodactylError(f"Ошибка обращения к Pterodactyl: {exc}") from exc

    def close(self) -> None:
        client = self._sync_client
        self._sync_client = None
        if client is not None:
            client.close()

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

    async def _get_async(self, server: str, suffix: str) -> dict:
        import httpx

        panel_url, api_key, uuid = self._target(server)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{panel_url}/api/client/servers/{uuid}/{suffix}",
                    headers=headers,
                )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PterodactylError(f"Ошибка обращения к Pterodactyl: {exc}") from exc

    def get_state(self, server: str) -> str | None:
        attributes = self._get(server, "resources").get("attributes", {})
        state = attributes.get("current_state")
        return str(state).strip().lower() if state else None

    def get_state_cached(
        self,
        server: str,
        *,
        force: bool = False,
        now: float | None = None,
    ) -> str | None:
        """Read panel state with a short TTL for the automatic scheduler."""
        panel_url, _api_key, uuid = self._target(server)
        cache_key = (panel_url, uuid)
        current = monotonic_clock.monotonic() if now is None else now
        if not force:
            with _state_cache_lock:
                cached = _state_cache.get(cache_key)
                if cached is not None and current - cached[0] < STATE_CACHE_TTL_SECONDS:
                    return cached[1]

        state = self.get_state(server)
        with _state_cache_lock:
            _state_cache[cache_key] = (current, state)
        return state

    async def get_state_async(self, server: str) -> str | None:
        attributes = (await self._get_async(server, "resources")).get("attributes", {})
        state = attributes.get("current_state")
        return str(state).strip().lower() if state else None

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


def _next_schedule_after_manual(
    scheduled_for: datetime | None,
    now: datetime,
    daily_time: str,
) -> datetime:
    """Skip a still-future automatic cycle consumed by a manual restart."""
    current = _utc_aware(now)
    scheduled = _utc_aware(scheduled_for)
    if scheduled is not None and current is not None and scheduled > current:
        return _utc_naive(next_daily_run_utc(scheduled, daily_time))  # type: ignore[arg-type]
    return _next_schedule(now, daily_time)


def suspend_schedule_for_offline(row: "ServerRestartSchedule", now: datetime) -> None:
    """Hide the timer and cancel an in-flight warning while the server is off."""
    current = _utc_aware(now)
    scheduled = _utc_aware(row.scheduled_for)
    row.warning_sent_at = None
    row.execution_due_at = None
    if scheduled is None or (current is not None and scheduled <= current):
        row.scheduled_for = _next_schedule(now, row.daily_time)
    row.last_status = "server_offline"
    row.last_error = None


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
    server_running: bool | None = None,
) -> str:
    if not row.enabled:
        return "disabled"
    if server_running is False:
        return "server_offline"
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
    server_state: str | None = None,
    server_running: bool | None = None,
) -> dict:
    status = _status_for_row(
        row,
        now=now,
        online_count=online_count,
        round_active=round_active,
        server_running=server_running,
    )
    next_run = row.execution_due_at or row.scheduled_for
    if server_running is False:
        next_run = None
    return {
        "server": row.server_name,
        "enabled": bool(row.enabled),
        "daily_time": row.daily_time,
        "msg_box": row.message_text,
        "conditions": {
            "no_online": bool(row.require_no_online),
            "round_finished": bool(row.require_round_finished),
        },
        "next_run_at": iso_utc(next_run),
        "scheduled_for": iso_utc(row.scheduled_for) if server_running is not False else None,
        "server_state": server_state,
        "server_running": server_running,
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
    client = PterodactylClient()
    result = []
    try:
        for row in rows:
            online_count, round_active = _condition_state(db, row.server_name)
            server_state = None
            server_running = None
            if row.enabled:
                try:
                    server_state = client.get_state(row.server_name)
                    server_running = server_state_allows_restart(server_state)
                except PterodactylError:
                    server_running = None
            result.append(
                serialize_schedule(
                    row,
                    online_count=online_count,
                    round_active=round_active,
                    now=current,
                    server_state=server_state,
                    server_running=server_running,
                )
            )
        return result
    finally:
        client.close()


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
    from models import ServerRestartSchedule

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
    transport = client or PterodactylClient()
    try:
        if not server_state_allows_restart(transport.get_state(row.server_name)):
            raise ValueError("Сервер выключен или ещё не перешёл в состояние running")
        if not _claim_schedule(db, row, current):
            raise RuntimeError("Для этого сервера уже выполняется действие")

        warning_error = None
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
    finally:
        if client is None:
            transport.close()

    row.scheduled_for = _next_schedule_after_manual(row.scheduled_for, current, row.daily_time)
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
    transport = client or PterodactylClient()
    if not server_state_allows_restart(await transport.get_state_async(row.server_name)):
        raise ValueError("Сервер выключен или ещё не перешёл в состояние running")
    if not _claim_schedule(db, row, current):
        raise RuntimeError("Для этого сервера уже выполняется действие")

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

    row.scheduled_for = _next_schedule_after_manual(row.scheduled_for, current, row.daily_time)
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
