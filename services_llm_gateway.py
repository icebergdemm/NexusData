"""Durable, client-aware gateway for OpenAI-compatible LLM backends."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_

from config import get_settings
from database import SessionLocal
from models import AILlmRequest

logger = logging.getLogger(__name__)

LLM_CLIENTS = {
    "dossier": {"backend": "dossier", "priority": 100, "quota_min": 2,
                "timeout": 180.0, "max_tokens": 260, "max_queue_depth": 20},
    "dispatcher": {"backend": "dispatcher", "priority": 10, "quota_min": 12,
                    "timeout": 60.0, "max_tokens": 512, "max_queue_depth": 50},
    "future": {"backend": "dossier", "priority": 200, "quota_min": 1,
                "timeout": 180.0, "max_tokens": 260, "max_queue_depth": 5},
    "log_analysis_dry_run": {"backend": "dossier", "priority": 150, "quota_min": 12,
                              "timeout": 240.0, "max_tokens": 2048, "max_queue_depth": 20},
    "monster_llm": {"backend": "dossier", "priority": 120, "quota_min": 12,
                    "timeout": 180.0, "max_tokens": 4096, "max_queue_depth": 50},
}
BACKENDS = {
    "dossier": {"url_key": "ai_dossier_url", "key": "ai_dossier_api_key",
                "tls_key": "ai_dossier_tls_ca_file"},
    "dispatcher": {"url_key": "ai_dispatcher_url", "key": "ai_dispatcher_api_key",
                    "tls_key": "ai_dispatcher_tls_ca_file"},
}
_paused: set[str] = set()
_wake = None
MAX_RESULT_CHARS = 12000


class LLMGatewayError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _database_local_now() -> datetime:
    """Return naive local time matching the database DateTime convention."""
    return datetime.now()


def set_wake_callback(callback) -> None:
    global _wake
    _wake = callback


def _request_hash(client_id: str, messages: Any, params: dict) -> str:
    body = {"client_id": client_id, "messages": messages, "params": params}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validate(client_id: str, messages: Any, params: dict) -> tuple[dict, dict]:
    client = LLM_CLIENTS.get(client_id)
    if not client:
        raise LLMGatewayError("Unknown LLM client")
    if not isinstance(messages, list) or not messages:
        raise LLMGatewayError("messages must be a non-empty list")
    params = dict(params or {})
    # Keep the gateway OpenAI-compatible for future callers that need a
    # model-selected tool call followed by an application-provided tool
    # message. The payload is still passed through unchanged; this layer only
    # keeps the queue's small allowlist from rejecting supported fields.
    allowed = {
        "model", "max_tokens", "temperature", "response_format", "top_p", "stop",
        "tools", "tool_choice", "parallel_tool_calls",
    }
    if set(params) - allowed:
        raise LLMGatewayError("Unsupported LLM parameter")
    if "max_tokens" in params and (not isinstance(params["max_tokens"], int) or
                                    params["max_tokens"] < 1 or params["max_tokens"] > client["max_tokens"]):
        raise LLMGatewayError("max_tokens exceeds client limit")
    if "temperature" in params and (not isinstance(params["temperature"], (int, float)) or
                                     not 0 <= params["temperature"] <= 2):
        raise LLMGatewayError("temperature must be between 0 and 2")
    if "response_format" in params and not isinstance(params["response_format"], dict):
        raise LLMGatewayError("response_format must be an object")
    return client, {"messages": messages, **params}


def submit(client_id, messages, params, priority=None, idempotency_key=None,
           timeout=None, requested_by=None) -> int:
    client, payload = _validate(client_id, messages, params)
    key = (idempotency_key or _request_hash(client_id, messages, payload))[:128]
    db = SessionLocal()
    try:
        existing = db.query(AILlmRequest).filter(AILlmRequest.idempotency_key == key).first()
        if existing:
            return existing.id
        depth = db.query(AILlmRequest).filter(
            AILlmRequest.client_id == client_id, AILlmRequest.status == "queued"
        ).count()
        if depth >= client["max_queue_depth"]:
            raise LLMGatewayError("LLM client queue is full", 429)
        minute_ago = _database_local_now() - timedelta(minutes=1)
        recent = db.query(AILlmRequest).filter(
            AILlmRequest.client_id == client_id,
            AILlmRequest.created_at >= minute_ago,
            AILlmRequest.status != "cancelled",
        ).count()
        if recent >= client["quota_min"]:
            raise LLMGatewayError("LLM client rate quota exceeded", 429)
        row = AILlmRequest(
            client_id=client_id, backend=client["backend"],
            priority=int(priority if priority is not None else client["priority"]),
            request_json={"payload": payload, "timeout": float(timeout or client["timeout"])},
            idempotency_key=key, requested_by=requested_by,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        if _wake:
            _wake()
        return row.id
    except LLMGatewayError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        # A concurrent submit may win the unique idempotency race.
        existing = db.query(AILlmRequest).filter(AILlmRequest.idempotency_key == key).first()
        if existing:
            return existing.id
        raise
    finally:
        db.close()


def poll(ticket_id: int) -> dict:
    db = SessionLocal()
    try:
        row = db.query(AILlmRequest).filter(AILlmRequest.id == ticket_id).first()
        if not row:
            raise LLMGatewayError("Unknown LLM ticket", 404)
        return {"ticket_id": row.id, "client_id": row.client_id, "status": row.status,
                "result": row.result_ref, "error": row.error, "attempts": row.attempts}
    finally:
        db.close()


def cancel(ticket_id: int) -> bool:
    db = SessionLocal()
    try:
        row = db.query(AILlmRequest).filter(AILlmRequest.id == ticket_id).first()
        if not row:
            raise LLMGatewayError("Unknown LLM ticket", 404)
        if row.status != "queued":
            return False
        row.status, row.finished_at = "cancelled", datetime.utcnow()
        db.commit()
        return True
    finally:
        db.close()


def claim_next(last_client: str | None = None) -> AILlmRequest | None:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        db.query(AILlmRequest).filter(
            AILlmRequest.status == "running", AILlmRequest.lease_expires_at < now
        ).update({"status": "queued", "lease_expires_at": None}, synchronize_session=False)
        candidates = db.query(AILlmRequest).filter(AILlmRequest.status == "queued").all()
        candidates = [r for r in candidates if r.client_id not in _paused]
        if last_client and len(candidates) > 1:
            different = [r for r in candidates if r.client_id != last_client]
            if different:
                candidates = different
        if not candidates:
            db.commit()
            return None
        row = min(candidates, key=lambda r: (
            r.priority - max(0, (now - r.created_at).total_seconds() / 60) * 1,
            r.created_at,
        ))
        row.status, row.attempts = "running", row.attempts + 1
        row.started_at = now
        row.lease_expires_at = now + timedelta(seconds=240)
        db.commit()
        # commit() expires ORM attributes by default.  The worker consumes the
        # detached row after this function returns, so reload the scalar
        # fields before expunging it instead of handing it a time bomb.
        db.refresh(row)
        db.expunge(row)
        return row
    finally:
        db.close()


def finish(ticket_id: int, status: str, result=None, error=None) -> None:
    db = SessionLocal()
    try:
        row = db.query(AILlmRequest).filter(AILlmRequest.id == ticket_id).first()
        if row:
            bounded = result
            if result is not None:
                encoded = json.dumps(result, ensure_ascii=True, separators=(",", ":"))
                limit = 120000 if row.client_id == "monster_llm" else MAX_RESULT_CHARS
                if len(encoded) > limit:
                    bounded = {"truncated": True, "content": encoded[:limit]}
            row.status, row.result_ref, row.error = status, bounded, str(error)[:2000] if error else None
            row.finished_at, row.lease_expires_at = datetime.utcnow(), None
            db.commit()
    finally:
        db.close()


def get_queue_snapshot() -> dict:
    db = SessionLocal()
    try:
        rows = db.query(AILlmRequest).filter(AILlmRequest.status.in_(["queued", "running"])).all()
        return {"total": len(rows), "tickets": [
            {"ticket_id": row.id, "client_id": row.client_id, "backend": row.backend,
             "priority": row.priority, "status": row.status, "attempts": row.attempts,
             "created_at": row.created_at.isoformat() if row.created_at else None,
             "started_at": row.started_at.isoformat() if row.started_at else None}
            for row in sorted(rows, key=lambda item: (item.status != "running", item.priority, item.created_at))
        ], "clients": {
            client: {"queued": sum(r.status == "queued" for r in rows if r.client_id == client),
                      "running": sum(r.status == "running" for r in rows if r.client_id == client),
                      "paused": client in _paused}
            for client in LLM_CLIENTS
        }}
    finally:
        db.close()


def pause_client(client_id):
    if client_id not in LLM_CLIENTS: raise LLMGatewayError("Unknown LLM client", 404)
    _paused.add(client_id)


def resume_client(client_id):
    _paused.discard(client_id)


def boost(ticket_id, priority: int) -> None:
    db = SessionLocal()
    try:
        row = db.query(AILlmRequest).filter(AILlmRequest.id == ticket_id).first()
        if not row: raise LLMGatewayError("Unknown LLM ticket", 404)
        row.priority = int(priority); db.commit()
    finally: db.close()


def purge_client(client_id) -> int:
    db = SessionLocal()
    try:
        count = db.query(AILlmRequest).filter(AILlmRequest.client_id == client_id,
                                              AILlmRequest.status == "queued").update(
                                                  {"status": "cancelled", "finished_at": datetime.utcnow()})
        db.commit(); return count
    finally: db.close()


def cancel_queued(client_id=None) -> int:
    """Cancel queued tickets, optionally restricted to one client."""
    db = SessionLocal()
    try:
        query = db.query(AILlmRequest).filter(AILlmRequest.status == "queued")
        if client_id:
            query = query.filter(AILlmRequest.client_id == client_id)
        count = query.update({"status": "cancelled", "finished_at": datetime.utcnow()},
                             synchronize_session=False)
        db.commit()
        return count
    finally:
        db.close()


def retry_failed(client_id=None) -> int:
    db = SessionLocal()
    try:
        query = db.query(AILlmRequest).filter(AILlmRequest.status == "failed")
        if client_id:
            query = query.filter(AILlmRequest.client_id == client_id)
        count = query.update({"status": "queued", "error": None, "lease_expires_at": None},
                             synchronize_session=False)
        db.commit()
        if count and _wake:
            _wake()
        return count
    finally:
        db.close()


def post_backend(backend: str, payload: dict, timeout: float):
    """Perform the sole outbound LLM request used by the queue worker."""
    import time
    if backend == "dossier":
        from services_ai_client import post_chat_completions
        return post_chat_completions(payload, timeout)
    import httpx
    settings = get_settings()
    spec = BACKENDS[backend]
    url = getattr(settings, spec["url_key"], "")
    key = getattr(settings, spec["key"], "")
    if not url or not key:
        raise LLMGatewayError(f"LLM backend {backend} is not configured", 503)
    started = time.monotonic()
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
        verify=getattr(settings, spec["tls_key"], "") or True,
    )
    response.raise_for_status()
    return response.json(), int((time.monotonic() - started) * 1000)
