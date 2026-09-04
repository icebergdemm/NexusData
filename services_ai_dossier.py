"""Durable player dossiers backed by the direct KoboldCpp OpenAI API."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import ssl
from threading import Lock
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import Session

from config import get_settings
from models import (
    AIPlayerDossier, AIPlayerDossierRun, AdminUser, AppSetting, AnticheatEvent, PlayerData, PlayerIP, PlayerKill,
    PlayerLabel, PlayerName, PlayerNote, PlayerPunishment, PlayerRoleAction,
    PlayerRoleStat, PlayerSession, PlayerStats, PlayerTrap,
)

logger = logging.getLogger(__name__)

DOSSIER_STATUSES = {"queued", "running", "succeeded", "failed"}
CONFIDENCE_VALUES = {"low", "medium", "high"}
MAX_OUTPUT_TOKENS = 650
DEFAULT_MODEL = "qwen2.5-3b-instruct"
PROMPT_PATH = Path(__file__).with_name("prompts") / "player_dossier.md"
ALLOWED_RUN_SOURCES = {"manual", "daily", "test", "health"}
AI_REQUEST_LOCK = Lock()


def validate_dossier_ca_pem(content: bytes) -> bytes:
    """Validate an uploaded public PEM certificate and return normalized bytes.

    This endpoint accepts a CA certificate only. In particular, never let a
    browser upload turn the app's config directory into a private-key drawer.
    Full trust validation still happens when httpx loads the CA bundle.
    """
    if len(content) > 64 * 1024:
        raise ValueError("CA certificate is too large")
    try:
        text = content.decode("ascii").strip() + "\n"
    except UnicodeDecodeError as exc:
        raise ValueError("CA certificate must be ASCII PEM") from exc
    if "PRIVATE KEY" in text or "BEGIN RSA" in text:
        raise ValueError("Private keys are not accepted")
    begin = "-----BEGIN CERTIFICATE-----"
    end = "-----END CERTIFICATE-----"
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ValueError("Upload exactly one PEM certificate")
    try:
        ssl.PEM_cert_to_DER_cert(text)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid PEM certificate") from exc
    return text.encode("ascii")


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _bounded_text(value: Any, limit: int = 2000) -> str:
    return str(value or "")[:limit]


def normalize_dossier_result(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("AI response must be a JSON object")
    summary = value.get("summary")
    confidence = value.get("confidence")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
        raise ValueError("AI response summary is missing or too long")
    if confidence not in CONFIDENCE_VALUES:
        raise ValueError("AI response confidence is invalid")

    def _list_of_dicts(key: str) -> list[dict]:
        rows = value.get(key, [])
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"AI response {key} must be an array of objects")
        return rows[:50]

    def _list_of_strings(key: str) -> list[str]:
        rows = value.get(key, [])
        if not isinstance(rows, list) or not all(isinstance(row, str) for row in rows):
            raise ValueError(f"AI response {key} must be an array of strings")
        return [row[:1000] for row in rows[:50]]

    return {
        "summary": summary.strip()[:1000],
        "facts": _list_of_dicts("facts"),
        "signals": _list_of_dicts("signals"),
        "caveats": _list_of_strings("caveats"),
        "confidence": confidence,
        "evidence": _list_of_dicts("evidence"),
    }


def parse_kobold_response(payload: dict) -> dict:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("KoboldCpp response has no chat content") from exc
    if not isinstance(content, str):
        raise ValueError("KoboldCpp chat content is not text")
    content = content.strip()
    candidates = [content]
    if content.startswith("```"):
        lines = content.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
            candidates.insert(0, "\n".join(lines[1:-1]).strip())

    # Reasoning tags or a short explanatory sentence can precede the object.
    # Keep this deliberately narrow: only a substring bounded by the first and
    # last braces is considered, and the normalized schema remains authoritative.
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        candidates.append(content[start:end + 1])

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return normalize_dossier_result(decoded)

    raise ValueError(f"KoboldCpp returned non-JSON content (response chars: {len(content)})")


def sanitize_dossier_result(result: dict, input_payload: dict) -> dict:
    """Redact identifiers and discard evidence references absent from input."""
    sensitive = {
        str(input_payload.get("profile", {}).get(key))
        for key in ("steamid", "discord_id", "last_ip")
        if input_payload.get("profile", {}).get(key)
    }
    sensitive.update(str(value) for value in input_payload.get("profile", {}).get("known_ips", []) if value)
    sensitive_re = re.compile("|".join(re.escape(value) for value in sorted(sensitive, key=len, reverse=True))) if sensitive else None

    def _redact(value: Any) -> Any:
        if isinstance(value, str) and sensitive_re:
            return sensitive_re.sub("[служебный идентификатор скрыт]", value)
        if isinstance(value, dict):
            return {key: _redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_redact(item) for item in value]
        return value

    known_ids = {"stats", f"profile:{input_payload.get('profile', {}).get('player_id')}"}
    for kind, source_key, field in (("session", "sessions", "id"), ("note", "notes", "id"), ("label", "labels", "id"), ("trap", "traps", "id"), ("punishment", "punishments", "id"), ("log", "logs", "id"), ("anticheat", "anticheat_events", "id")):
        known_ids.update(f"{kind}:{row[field]}" for row in input_payload.get(source_key, []) if isinstance(row, dict) and row.get(field) is not None)
    clean = _redact(result)
    clean["evidence"] = [item for item in clean["evidence"] if isinstance(item, dict) and (item.get("id") in known_ids or f"{item.get('kind')}:{item.get('id')}" in known_ids)]
    return clean


def build_kobold_request(input_payload: dict, *, api_key: str, model: str = DEFAULT_MODEL, prompt: Optional[str] = None) -> dict:
    return {
        "headers": {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        "json": {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt or PROMPT_PATH.read_text(encoding="utf-8")},
                {"role": "user", "content": json.dumps(input_payload, ensure_ascii=False, sort_keys=True)},
            ],
            "temperature": 0.2,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "stream": False,
        },
    }


def dossier_input_hash(payload: dict) -> str:
    stable_payload = dict(payload)
    if isinstance(stable_payload.get("window"), dict):
        stable_payload["window"] = dict(stable_payload["window"])
        stable_payload["window"].pop("to", None)
    canonical = json.dumps(stable_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def should_queue_dossier(report: Optional[AIPlayerDossier], input_hash: str, force: bool, now: datetime, stale_days: int) -> bool:
    if force or report is None:
        return True
    if report.status in {"queued", "running"}:
        return False
    if report.status == "failed":
        return True
    if report.input_hash != input_hash:
        return True
    generated_at = report.generated_at
    return not generated_at or generated_at < now - timedelta(days=stale_days)


def select_active_player_ids(db: Session, now: datetime, limit: int) -> list[int]:
    cutoff = now - timedelta(days=get_dossier_config(db)["active_days"])
    rows = db.query(PlayerData.player_id).outerjoin(
        PlayerSession, PlayerSession.player_id == PlayerData.player_id,
    ).filter(or_(
        PlayerData.last_login_time >= cutoff,
        PlayerSession.login_time >= cutoff,
    )).distinct().order_by(PlayerData.last_login_time.desc(), PlayerData.player_id.asc()).limit(limit).all()
    return [row[0] for row in rows]


def _safe_rows(query, serializer):
    try:
        return [serializer(row) for row in query.all()]
    except Exception as exc:
        logger.warning("AI dossier source unavailable: %s", exc)
        return []


def build_player_dossier_payload(db: Session, player_id: int, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    settings = get_settings()
    runtime_config = get_dossier_config(db)
    active_days = runtime_config["active_days"]
    max_log_events = runtime_config["max_log_events"]
    since = now - timedelta(days=active_days)
    player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
    if not player:
        raise ValueError("Player not found")

    names = db.query(PlayerName).filter(PlayerName.player_id == player_id).all()
    known_names = sorted({name.nickname for name in names if name.nickname} | {player.player_last_time_name} - {None})
    known_names = [_bounded_text(name, 200) for name in known_names[:50]]
    stats = db.query(PlayerStats).filter(PlayerStats.player_id == player_id).first()
    sessions = db.query(PlayerSession).filter(PlayerSession.player_id == player_id, PlayerSession.login_time >= since).order_by(PlayerSession.login_time.desc()).limit(100).all()
    notes = db.query(PlayerNote).filter(PlayerNote.player_id == player_id).order_by(PlayerNote.created_at.desc()).limit(100).all()
    labels = db.query(PlayerLabel).filter(PlayerLabel.player_id == player_id).limit(100).all()
    traps = db.query(PlayerTrap).filter(PlayerTrap.player_id == player_id).order_by(PlayerTrap.set_at.desc()).limit(100).all()
    punishments = db.query(PlayerPunishment).filter(PlayerPunishment.player_id == player_id).order_by(PlayerPunishment.issued_at.desc()).limit(100).all()
    kills = db.query(PlayerKill).filter(PlayerKill.player_id == player_id).limit(100).all()
    role_actions = db.query(PlayerRoleAction).filter(PlayerRoleAction.player_id == player_id).limit(100).all()
    role_stats = db.query(PlayerRoleStat).filter(PlayerRoleStat.player_id == player_id).limit(100).all()
    anticheat = db.query(AnticheatEvent).filter(AnticheatEvent.player_id == player_id, AnticheatEvent.occurred_at >= since).order_by(AnticheatEvent.occurred_at.desc()).limit(100).all()
    ips = db.query(PlayerIP).filter(PlayerIP.player_id == player_id).all()

    payload = {
        "profile": {
            "player_id": player.player_id,
            "name": player.display_name,
            "known_names": known_names,
            "steamid": player.player_steamid,
            "last_login_time": _iso(player.last_login_time),
            "first_login": _iso(player.first_login),
            "play_time_seconds": player.play_time,
            "rank_id": player.rank_id,
            "kit_level": player.kit_level,
            "discord_id": player.discord_id,
            "discord_id_confirmed": bool(player.discord_id_confirmed),
            "last_ip": player.last_ip,
            "known_ips": [ip.ip for ip in ips],
        },
        "stats": {key: getattr(stats, key) for key in (
            "successful_missions_captain", "total_missions_captain", "saved_players",
            "killed_monsters", "killed_players", "killed_npc",
        )} if stats else None,
        "sessions": [{"id": row.id, "session_number": row.session_number, "login_time": _iso(row.login_time), "logout_time": _iso(row.logout_time), "duration": row.session_duration, "avg_ping": row.avg_ping} for row in sessions],
        "notes": [{"id": row.id, "content": _bounded_text(row.content, 1500), "created_at": _iso(row.created_at)} for row in notes],
        "labels": [{"id": row.id, "category_id": row.category_id, "note": _bounded_text(row.note, 500), "assigned_at": _iso(row.assigned_at)} for row in labels],
        "traps": [{"id": row.id, "active": bool(row.active), "reason": _bounded_text(row.reason, 500), "set_at": _iso(row.set_at), "triggered_at": _iso(row.triggered_at)} for row in traps],
        "punishments": [{"id": row.id, "type": row.type, "reason": _bounded_text(row.reason, 1000), "issued_at": _iso(row.issued_at), "expires_at": _iso(row.expires_at), "active": bool(row.is_active)} for row in punishments],
        "kills": [{"id": row.id, "monster_type": row.monster_type, "count": row.kills_count} for row in kills],
        "role_actions": [{"id": row.id, "role": row.role, "action_type": row.action_type, "value": row.value} for row in role_actions],
        "role_stats": [{"id": row.id, "role": row.role, "rounds_played": row.rounds_played, "mvp_score": row.mvp_score} for row in role_stats],
        "anticheat_events": [{"id": row.id, "event_type": row.event_type, "details": _bounded_text(row.details, 500), "occurred_at": _iso(row.occurred_at)} for row in anticheat],
        "logs": [],
        "missing": [],
        "window": {"from": since.isoformat(), "to": now.isoformat(), "days": active_days},
    }

    try:
        from services_logs import search_logs
        result = search_logs(names_list=known_names, time_from=since, time_to=now, player_scope="related", limit=max_log_events, count_total=False)
        payload["logs"] = [{"id": event.row_id, "timestamp": _iso(event.timestamp), "player_name": event.player_name, "action": event.action_type, "details": _bounded_text(event.details, 500), "server": event.server_name, "source": event.source_file} for event in result.events]
    except Exception as exc:
        logger.warning("AI dossier logs unavailable for player %s: %s", player_id, exc)
        payload["missing"].append("server_logs")

    def _encoded_length() -> int:
        return len(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    if _encoded_length() > settings.ai_dossier_max_input_chars:
        # Keep the recent rows first, then progressively reduce the noisiest
        # collections. The final guard matters when notes themselves are large.
        for key in ("logs", "notes", "punishments", "traps", "labels", "sessions", "anticheat_events", "role_actions", "role_stats", "kills"):
            while _encoded_length() > settings.ai_dossier_max_input_chars and len(payload[key]) > 10:
                payload[key] = payload[key][:max(10, len(payload[key]) // 2)]
        if _encoded_length() > settings.ai_dossier_max_input_chars:
            for key in ("logs", "notes", "punishments", "traps", "labels", "sessions", "anticheat_events", "role_actions", "role_stats", "kills"):
                if _encoded_length() <= settings.ai_dossier_max_input_chars:
                    break
                payload[key] = payload[key][:3]
        if _encoded_length() > settings.ai_dossier_max_input_chars:
            payload["profile"]["known_names"] = payload["profile"]["known_names"][:10]
        if "payload_truncated" not in payload["missing"]:
            payload["missing"].append("payload_truncated")
    if not payload["logs"] and "server_logs" not in payload["missing"]:
        payload["missing"].append("no_matching_server_logs")
    return payload


def enqueue_dossier(
    db: Session,
    player_id: int,
    requested_by_admin_id: Optional[int],
    force: bool = False,
    now: Optional[datetime] = None,
    source: str = "manual",
) -> tuple[AIPlayerDossier, bool]:
    if source not in ALLOWED_RUN_SOURCES - {"health"}:
        raise ValueError("Invalid AI dossier run source")
    now = now or datetime.utcnow()
    report = db.query(AIPlayerDossier).filter(AIPlayerDossier.player_id == player_id).first()
    if report is None:
        report = AIPlayerDossier(player_id=player_id, status="queued", requested_at=now, requested_by_admin_id=requested_by_admin_id)
        db.add(report)
        db.flush()
    elif report.status in {"queued", "running"}:
        return report, False
    else:
        report.status = "queued"
        report.requested_at = now
        report.requested_by_admin_id = requested_by_admin_id
        report.started_at = None
        report.finished_at = None
        report.lease_expires_at = None
        report.error_message = None

    db.add(AIPlayerDossierRun(
        dossier_id=report.id,
        player_id=player_id,
        source=source,
        status="queued",
        requested_by_admin_id=requested_by_admin_id,
        requested_at=now,
        model=get_settings().ai_dossier_model,
    ))
    db.commit()
    db.refresh(report)
    return report, True


def enqueue_dossier_if_needed(db: Session, player_id: int, requested_by_admin_id: Optional[int] = None, now: Optional[datetime] = None) -> tuple[Optional[AIPlayerDossier], bool]:
    now = now or datetime.utcnow()
    report = db.query(AIPlayerDossier).filter(AIPlayerDossier.player_id == player_id).first()
    if report and report.status in {"queued", "running"}:
        return report, False
    payload = build_player_dossier_payload(db, player_id, now)
    if not should_queue_dossier(report, dossier_input_hash(payload), False, now, 7):
        return report, False
    return enqueue_dossier(db, player_id, requested_by_admin_id, force=False, now=now, source="daily")


def _claim_next(db: Session, now: datetime) -> Optional[AIPlayerDossier]:
    report = db.query(AIPlayerDossier).filter(
        or_(AIPlayerDossier.status == "queued", AIPlayerDossier.status == "running"),
        or_(AIPlayerDossier.status == "queued", AIPlayerDossier.lease_expires_at < now),
    ).order_by(AIPlayerDossier.requested_at.asc(), AIPlayerDossier.id.asc()).first()
    if not report or report.status == "running":
        if report and report.status == "running":
            report.status = "queued"
            report.lease_expires_at = None
            running = db.query(AIPlayerDossierRun).filter(
                AIPlayerDossierRun.dossier_id == report.id,
                AIPlayerDossierRun.status == "running",
            ).order_by(AIPlayerDossierRun.id.desc()).first()
            if running:
                running.status = "queued"
                running.started_at = None
            db.commit()
        return None
    report.status = "running"
    report.started_at = now
    report.lease_expires_at = now + timedelta(seconds=get_settings().ai_dossier_timeout_seconds + 60)
    report.error_message = None
    run = db.query(AIPlayerDossierRun).filter(
        AIPlayerDossierRun.dossier_id == report.id,
        AIPlayerDossierRun.status == "queued",
    ).order_by(AIPlayerDossierRun.id.desc()).first()
    if run is None:
        run = AIPlayerDossierRun(
            dossier_id=report.id,
            player_id=report.player_id,
            source="manual",
            status="queued",
            requested_by_admin_id=report.requested_by_admin_id,
            requested_at=report.requested_at or now,
            model=get_settings().ai_dossier_model,
        )
        db.add(run)
    run.status = "running"
    run.started_at = now
    db.commit()
    return report


def process_one_dossier() -> dict:
    from database import SessionLocal
    db = SessionLocal()
    try:
        report = _claim_next(db, datetime.utcnow())
        if not report:
            return {"status": "idle"}
        settings = get_settings()
        run = db.query(AIPlayerDossierRun).filter(
            AIPlayerDossierRun.dossier_id == report.id,
            AIPlayerDossierRun.status == "running",
        ).order_by(AIPlayerDossierRun.id.desc()).first()
        try:
            payload = build_player_dossier_payload(db, report.player_id)
            request_hash = dossier_input_hash(payload)
            if not settings.ai_dossier_url or not settings.ai_dossier_api_key:
                raise RuntimeError("AI dossier endpoint is not configured")
            request = build_kobold_request(
                payload,
                api_key=settings.ai_dossier_api_key,
                model=settings.ai_dossier_model,
                prompt=get_dossier_prompt(db),
            )
            verify: bool | str = settings.ai_dossier_tls_ca_file or True
            started = time.monotonic()
            with AI_REQUEST_LOCK:
                with httpx.Client(timeout=settings.ai_dossier_timeout_seconds, verify=verify) as client:
                    response = client.post(settings.ai_dossier_url, **request)
                    response.raise_for_status()
                    response_payload = response.json()
                    result = sanitize_dossier_result(parse_kobold_response(response_payload), payload)
            usage = response_payload.get("usage") or {}
            elapsed_ms = int((time.monotonic() - started) * 1000)
            completion_tokens = usage.get("completion_tokens")
            logger.info("AI dossier request complete player=%s latency_ms=%s prompt_tokens=%s completion_tokens=%s", report.player_id, elapsed_ms, usage.get("prompt_tokens"), completion_tokens)
            report.status = "succeeded"
            report.result_json = result
            report.summary_text = result["summary"]
            report.input_hash = request_hash
            report.model = settings.ai_dossier_model
            report.generated_at = datetime.utcnow()
            report.finished_at = report.generated_at
            report.lease_expires_at = None
            report.error_message = None
            if run:
                run.status = "succeeded"
                run.finished_at = report.finished_at
                run.latency_ms = elapsed_ms
                run.prompt_tokens = usage.get("prompt_tokens")
                run.completion_tokens = usage.get("completion_tokens")
                run.model = settings.ai_dossier_model
                run.input_hash = request_hash
            db.commit()
            return {"status": "succeeded", "player_id": report.player_id}
        except Exception as exc:
            logger.exception("AI dossier generation failed for player %s", report.player_id)
            report.status = "failed"
            if not report.model:
                report.model = settings.ai_dossier_model
            report.finished_at = datetime.utcnow()
            report.lease_expires_at = None
            report.error_message = f"{type(exc).__name__}: {exc}"[:2000]
            if run:
                run.status = "failed"
                run.finished_at = report.finished_at
                run.error_message = report.error_message
                run.model = settings.ai_dossier_model
            db.commit()
            return {"status": "failed", "player_id": report.player_id, "error": report.error_message}
    finally:
        db.close()


def run_daily_dossier_batch() -> dict:
    from database import SessionLocal
    db = SessionLocal()
    try:
        ids = select_active_player_ids(db, datetime.utcnow(), get_dossier_config(db)["daily_limit"])
        queued = 0
        for player_id in ids:
            _, created = enqueue_dossier_if_needed(db, player_id, None)
            queued += int(created)
        return {"selected": len(ids), "queued": queued}
    finally:
        db.close()


def get_dossier_config(db: Session, include_prompt: bool = False) -> dict:
    settings = get_settings()

    def _value(key: str, default: str) -> str:
        try:
            row = db.query(AppSetting).filter(AppSetting.key == key).first()
        except Exception:
            db.rollback()
            return default
        return row.value if row and row.value is not None else default

    def _int(key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            return min(maximum, max(minimum, int(_value(key, str(default)))))
        except (TypeError, ValueError):
            return default

    def _bool(key: str, default: bool) -> bool:
        return _value(key, str(default).lower()).lower() in {"true", "1", "yes", "on"}

    endpoint = settings.ai_dossier_url or ""
    config = {
        "enabled": _bool("ai_dossier_enabled", False),
        "daily_enabled": _bool("ai_dossier_daily_enabled", True),
        "test_mode": _bool("ai_dossier_test_mode", False),
        "daily_limit": _int("ai_dossier_daily_limit", settings.ai_dossier_daily_limit, 1, 10),
        "schedule_hour": _int("ai_dossier_schedule_hour", settings.ai_dossier_schedule_hour, 0, 23),
        "active_days": _int("ai_dossier_active_days", settings.ai_dossier_active_days, 1, 365),
        "max_log_events": _int("ai_dossier_max_log_events", settings.ai_dossier_max_log_events, 1, 500),
        "configured": bool(endpoint and settings.ai_dossier_api_key),
        "endpoint_host": urlsplit(endpoint).hostname if endpoint else None,
        "tls_ca_configured": bool(settings.ai_dossier_tls_ca_file),
        "model": settings.ai_dossier_model,
        "timeout_seconds": settings.ai_dossier_timeout_seconds,
    }
    if include_prompt:
        config["prompt"] = get_dossier_prompt(db)
    return config


def get_dossier_prompt(db: Session) -> str:
    """Return the superuser-configurable prompt, falling back to the Git file."""
    default_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    try:
        row = db.query(AppSetting).filter(AppSetting.key == "ai_dossier_prompt").first()
    except Exception:
        db.rollback()
        return default_prompt
    value = row.value.strip() if row and row.value else ""
    return value[:20000] if value else default_prompt


def dossier_public_dict(report: Optional[AIPlayerDossier]) -> dict:
    if not report:
        return {"status": "none", "result": None}
    result = report.result_json
    if isinstance(result, dict):
        result = dict(result)
        result["evidence"] = [
            dict(item, href=dossier_evidence_link(item.get("kind"), item.get("id"), report.player_id))
            if isinstance(item, dict) and dossier_evidence_link(item.get("kind"), item.get("id"), report.player_id)
            else item
            for item in result.get("evidence", [])
        ]
    return {
        "status": report.status,
        "result": result,
        "summary": report.summary_text,
        "input_hash": report.input_hash,
        "model": report.model,
        "requested_at": _iso(report.requested_at),
        "started_at": _iso(report.started_at),
        "generated_at": _iso(report.generated_at),
        "finished_at": _iso(report.finished_at),
        "error": report.error_message,
        "requested_by_admin_id": report.requested_by_admin_id,
    }


def dossier_response_with_run(db: Session, report: Optional[AIPlayerDossier]) -> dict:
    """Serialize an optional report without dereferencing a missing row."""
    if report is None:
        return {"status": "none", "result": None, "run": None}
    run = db.query(AIPlayerDossierRun).filter(
        AIPlayerDossierRun.dossier_id == report.id,
    ).order_by(AIPlayerDossierRun.id.desc()).first()
    response = dossier_public_dict(report)
    response["run"] = dossier_run_public_dict(run) if run else None
    return response


def dossier_evidence_link(kind: Any, source_id: Any, player_id: Optional[int]) -> Optional[str]:
    """Build links only for source types the dossier payload explicitly allows."""
    if source_id is None:
        return None
    try:
        source_id = int(source_id)
    except (TypeError, ValueError):
        return None
    if source_id < 1:
        return None
    if kind == "log":
        return f"/logs?event_id={source_id}"
    if player_id is None:
        return None
    tabs = {
        "note": "notes",
        "label": "labels",
        "trap": "traps",
        "punishment": "punishments",
        "session": "history",
        "anticheat": "history",
    }
    tab = tabs.get(kind)
    return f"/players/{int(player_id)}?tab={tab}&source_id={source_id}" if tab else None


def dossier_run_public_dict(run: AIPlayerDossierRun, player_name: Optional[str] = None) -> dict:
    return {
        "id": run.id,
        "player_id": run.player_id,
        "player_name": player_name,
        "source": run.source,
        "status": run.status,
        "requested_by_admin_id": run.requested_by_admin_id,
        "requested_at": _iso(run.requested_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "latency_ms": run.latency_ms,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "model": run.model,
        "input_hash": run.input_hash,
        "error": run.error_message,
    }


def list_dossier_runs(db: Session, limit: int, include_secret: bool) -> list[dict]:
    query = db.query(AIPlayerDossierRun, PlayerData.player_last_time_name).outerjoin(
        PlayerData, PlayerData.player_id == AIPlayerDossierRun.player_id,
    )
    if not include_secret:
        query = query.filter(
            PlayerData.is_secret.is_(False),
            ~exists().where(and_(
                AdminUser.discord_id == PlayerData.discord_id,
                AdminUser.is_superuser.is_(True),
            )),
        )
    rows = query.order_by(AIPlayerDossierRun.id.desc()).limit(min(max(limit, 1), 100)).all()
    return [dossier_run_public_dict(run, name) for run, name in rows]


def search_dossier_players(db: Session, query_text: str, limit: int, include_secret: bool) -> list[dict]:
    query = db.query(PlayerData)
    if not include_secret:
        query = query.filter(
            PlayerData.is_secret.is_(False),
            ~exists().where(and_(
                AdminUser.discord_id == PlayerData.discord_id,
                AdminUser.is_superuser.is_(True),
            )),
        )
    text = (query_text or "").strip()
    if text:
        pattern = f"%{text}%"
        query = query.filter(or_(
            PlayerData.player_last_time_name.ilike(pattern),
            PlayerData.forced_display_name.ilike(pattern),
            PlayerData.player_steamid.ilike(pattern),
        ))
    rows = query.order_by(PlayerData.last_login_time.desc(), PlayerData.player_id.asc()).limit(min(max(limit, 1), 50)).all()
    report_by_player = {
        report.player_id: report for report in db.query(AIPlayerDossier).filter(
            AIPlayerDossier.player_id.in_([row.player_id for row in rows])
        ).all()
    } if rows else {}
    return [{
        "player_id": row.player_id,
        "name": row.forced_display_name or row.player_last_time_name or f"Player {row.player_id}",
        "last_login_time": _iso(row.last_login_time),
        "status": report_by_player[row.player_id].status if row.player_id in report_by_player else "none",
        "generated_at": _iso(report_by_player[row.player_id].generated_at) if row.player_id in report_by_player else None,
    } for row in rows]


def run_ai_health_check() -> dict:
    """Check authentication/transport with a fixed, non-personal prompt."""
    from database import SessionLocal

    db = SessionLocal()
    run = AIPlayerDossierRun(
        source="health", status="running", requested_at=datetime.utcnow(),
        started_at=datetime.utcnow(), model=get_settings().ai_dossier_model,
    )
    db.add(run)
    db.commit()
    started = time.monotonic()
    settings = get_settings()
    try:
        if not settings.ai_dossier_url or not settings.ai_dossier_api_key:
            raise RuntimeError("AI dossier endpoint is not configured")
        payload = {"health_check": True, "instruction": "Return the required JSON schema with summary 'OK'. No player data is present."}
        request = build_kobold_request(
            payload,
            api_key=settings.ai_dossier_api_key,
            model=settings.ai_dossier_model,
            prompt=(
                "You are a transport health check. Return only valid JSON using the dossier schema. "
                "Set summary to 'OK', confidence to 'high', and use empty arrays for all other fields."
            ),
        )
        verify: bool | str = settings.ai_dossier_tls_ca_file or True
        with AI_REQUEST_LOCK:
            with httpx.Client(timeout=settings.ai_dossier_timeout_seconds, verify=verify) as client:
                response = client.post(settings.ai_dossier_url, **request)
                response.raise_for_status()
                response_payload = response.json()
                result = normalize_dossier_result(parse_kobold_response(response_payload))
        usage = response_payload.get("usage") or {}
        run.status = "succeeded"
        run.finished_at = datetime.utcnow()
        run.latency_ms = int((time.monotonic() - started) * 1000)
        run.prompt_tokens = usage.get("prompt_tokens")
        run.completion_tokens = usage.get("completion_tokens")
        run.model = settings.ai_dossier_model
        db.commit()
        return {"status": "succeeded", "result": result, "run": dossier_run_public_dict(run)}
    except Exception as exc:
        run.status = "failed"
        run.finished_at = datetime.utcnow()
        run.latency_ms = int((time.monotonic() - started) * 1000)
        run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        db.commit()
        return {"status": "failed", "result": None, "run": dossier_run_public_dict(run)}
    finally:
        db.close()


def get_dossier_overview(db: Session, include_secret: bool = False) -> dict:
    config = get_dossier_config(db)
    counts = {status: db.query(AIPlayerDossier).filter(AIPlayerDossier.status == status).count() for status in DOSSIER_STATUSES}
    last_run_query = db.query(AIPlayerDossierRun).outerjoin(
        PlayerData, PlayerData.player_id == AIPlayerDossierRun.player_id,
    )
    if not include_secret:
        last_run_query = last_run_query.filter(
            or_(AIPlayerDossierRun.player_id.is_(None), and_(
                PlayerData.is_secret.is_(False),
                ~exists().where(and_(
                    AdminUser.discord_id == PlayerData.discord_id,
                    AdminUser.is_superuser.is_(True),
                )),
            )),
        )
    last_run = last_run_query.order_by(AIPlayerDossierRun.id.desc()).first()
    health = db.query(AIPlayerDossierRun).filter(AIPlayerDossierRun.source == "health").order_by(AIPlayerDossierRun.id.desc()).first()
    return {
        "config": config,
        "queue": counts,
        "last_run": dossier_run_public_dict(last_run) if last_run else None,
        "last_health_check": dossier_run_public_dict(health) if health else None,
    }
