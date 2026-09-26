"""Shared transport client for the site's AI aggregator.

All server-side LLM consumers (player dossiers, monster-spawn assistant,
health checks) talk to the SAME OpenAI-compatible endpoint
(AI_DOSSIER_URL + AI_DOSSIER_API_KEY, TLS via AI_DOSSIER_TLS_CA_FILE) and go
through this module as clients — in particular they all serialize on the
single AI_REQUEST_LOCK below, because the Qwen/KoboldCpp host behind the
aggregator serves generations one at a time.

Callers only build the JSON body (model/messages/sampling params stay
per-consumer); URL, auth, TLS verification, queueing and transport error
mapping live here, so a new consumer cannot accidentally bypass the queue
with a direct HTTP call.
"""

from __future__ import annotations

import logging
import re
import time
from threading import Lock
from typing import Any, Dict, Tuple

import httpx

from config import get_settings

logger = logging.getLogger(__name__)

# The single site-wide queue for AI generations. Every consumer must hold
# this lock while its request is in flight — never POST to the aggregator
# directly, always go through post_chat_completions().
AI_REQUEST_LOCK = Lock()
AI_CLIENT_TTL_SECONDS = 300.0
_AI_CLIENT = None
_AI_CLIENT_FINGERPRINT = None
_AI_CLIENT_CREATED_AT = 0.0


class AIClientError(Exception):
    """Transport-level failure talking to the AI aggregator (no secrets)."""


def _client_fingerprint(settings) -> tuple:
    return (
        (settings.ai_dossier_url or "").strip(),
        settings.ai_dossier_api_key or "",
        settings.ai_dossier_tls_ca_file or "",
        settings.ai_dossier_model or "",
    )


def _build_timeout(timeout: float) -> httpx.Timeout:
    total = max(0.1, float(timeout))
    return httpx.Timeout(
        timeout=total,
        connect=min(10.0, total),
        pool=min(5.0, total),
        write=min(30.0, total),
    )


def _get_ai_client(settings, timeout: float):
    global _AI_CLIENT, _AI_CLIENT_FINGERPRINT, _AI_CLIENT_CREATED_AT
    fingerprint = _client_fingerprint(settings)
    now = time.monotonic()
    if (
        _AI_CLIENT is None
        or _AI_CLIENT_FINGERPRINT != fingerprint
        or now - _AI_CLIENT_CREATED_AT >= AI_CLIENT_TTL_SECONDS
    ):
        if _AI_CLIENT is not None:
            _AI_CLIENT.close()
        _AI_CLIENT = httpx.Client(
            timeout=_build_timeout(timeout),
            verify=settings.ai_dossier_tls_ca_file or True,
        )
        _AI_CLIENT_FINGERPRINT = fingerprint
        _AI_CLIENT_CREATED_AT = now
    return _AI_CLIENT


def close_ai_client() -> None:
    """Close the shared transport during worker/application shutdown."""
    global _AI_CLIENT, _AI_CLIENT_FINGERPRINT, _AI_CLIENT_CREATED_AT
    with AI_REQUEST_LOCK:
        if _AI_CLIENT is not None:
            _AI_CLIENT.close()
        _AI_CLIENT = None
        _AI_CLIENT_FINGERPRINT = None
        _AI_CLIENT_CREATED_AT = 0.0


def _invalidate_ai_client(client) -> None:
    global _AI_CLIENT, _AI_CLIENT_FINGERPRINT, _AI_CLIENT_CREATED_AT
    if _AI_CLIENT is client:
        client.close()
        _AI_CLIENT = None
        _AI_CLIENT_FINGERPRINT = None
        _AI_CLIENT_CREATED_AT = 0.0


def is_ai_configured() -> bool:
    settings = get_settings()
    return bool((settings.ai_dossier_url or "").strip() and settings.ai_dossier_api_key)


def get_ai_endpoint_info() -> Dict[str, Any]:
    """Non-secret aggregator info safe to expose in admin UIs."""
    settings = get_settings()
    return {
        "url": settings.ai_dossier_url,
        "has_url": bool((settings.ai_dossier_url or "").strip()),
        "api_key_set": bool(settings.ai_dossier_api_key),
        "tls_ca_configured": bool(settings.ai_dossier_tls_ca_file),
        "timeout_seconds": settings.ai_dossier_timeout_seconds,
    }


def post_chat_completions(payload: Dict[str, Any], timeout: float) -> Tuple[Dict[str, Any], int]:
    """POST one OpenAI-compatible chat/completions body, observing the queue.

    Returns (response_json, elapsed_ms). Raises AIClientError with a
    human-readable (Russian), secret-free message on any transport failure.
    """
    settings = get_settings()
    url = (settings.ai_dossier_url or "").strip()
    if not url or not settings.ai_dossier_api_key:
        raise AIClientError("AI-агрегатор не настроен: задайте AI_DOSSIER_URL и AI_DOSSIER_API_KEY")
    headers = {
        "Authorization": f"Bearer {settings.ai_dossier_api_key}",
        "Content-Type": "application/json",
    }
    started = time.monotonic()
    try:
        with AI_REQUEST_LOCK:
            client = _get_ai_client(settings, timeout)
            try:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            except httpx.RequestError:
                _invalidate_ai_client(client)
                raise
    except AIClientError:
        raise
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        detail = ""
        if exc.response is not None:
            try:
                body = exc.response.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                error = body.get("error") if isinstance(body.get("error"), dict) else body
                message = error.get("message") or error.get("detail") or error.get("code")
                if isinstance(message, str):
                    message = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [скрыто]", message)
                    message = re.sub(r"(?i)(api[_-]?key|token|authorization)(\s*[:=]\s*)[^\s,;]+",
                                     r"\1\2[скрыто]", message)
                    message = " ".join(message.split())[:300]
                    if message:
                        detail = f" ({message})"
        raise AIClientError(f"AI-агрегатор вернул HTTP {status}{detail}") from exc
    except httpx.RequestError as exc:
        raise AIClientError(f"AI-агрегатор недоступен ({url}): {exc}") from exc
    except ValueError as exc:
        raise AIClientError(f"Некорректный ответ AI-агрегатора: {exc}") from exc
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not isinstance(data, dict):
        raise AIClientError("Некорректный ответ AI-агрегатора: не JSON-объект")
    return data, elapsed_ms


def chat_content(response_payload: Dict[str, Any]) -> str:
    """Extract choices[0].message.content text or raise AIClientError."""
    try:
        content = response_payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIClientError(f"Ответ агрегатора без choices/message: {exc}") from exc
    if not isinstance(content, str):
        raise AIClientError("Ответ агрегатора не содержит текста")
    return content
