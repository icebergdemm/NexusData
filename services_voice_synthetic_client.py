"""Authenticated HTTPS client for the remote synthetic-voice classifier."""

from __future__ import annotations

import base64
import logging
import ssl
import threading
from typing import Sequence

import httpx

from config import get_settings
from tools.voice_synthetic_constants import (
    FAKE_SCORE_THRESHOLD,
    MAX_WINDOWS_PER_CALL,
    MODEL_REVISION,
)

logger = logging.getLogger(__name__)
DEFAULT_VOICE_SYNTHETIC_API_URL = "https://213.108.170.48:8443"


def _build_ssl_context(ca_file: str) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=ca_file or None)
    # The existing AI-dossier CA is self-signed without CA key-usage extensions.
    # Keep normal chain, hostname, and validity checks while accepting that CA.
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


class VoiceSyntheticInferenceError(RuntimeError):
    """A safe-to-display remote inference failure without credentials or audio."""


class VoiceSyntheticInferenceClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        ca_file: str,
        timeout_seconds: float,
        *,
        transport=None,
    ):
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key
        self.ca_file = ca_file.strip()
        timeout = max(0.1, float(timeout_seconds))
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                timeout,
                connect=min(5.0, timeout),
                pool=min(5.0, timeout),
                write=min(15.0, timeout),
            ),
            verify=_build_ssl_context(self.ca_file),
            transport=transport,
        )

    @classmethod
    def from_settings(cls) -> "VoiceSyntheticInferenceClient":
        settings = get_settings()
        return cls(
            settings.voice_synthetic_api_url.strip() or DEFAULT_VOICE_SYNTHETIC_API_URL,
            settings.voice_synthetic_api_key or settings.ai_dossier_api_key,
            settings.voice_synthetic_tls_ca_file or settings.ai_dossier_tls_ca_file,
            settings.voice_synthetic_timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self.configured():
            raise VoiceSyntheticInferenceError("Удалённый классификатор не настроен")
        try:
            response = self._client.request(
                method,
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                **kwargs,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("Synthetic voice inference service request failed: %s", type(exc).__name__)
            raise VoiceSyntheticInferenceError("Удалённый классификатор недоступен") from exc
        except ValueError as exc:
            raise VoiceSyntheticInferenceError("Удалённый классификатор вернул некорректный JSON") from exc
        if not isinstance(payload, dict):
            raise VoiceSyntheticInferenceError("Удалённый классификатор вернул некорректный ответ")
        return payload

    def status(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "revision": None, "device": None}
        try:
            payload = self._request("GET", "/v1/voice-synthetic/health")
        except VoiceSyntheticInferenceError:
            return {"status": "unavailable", "revision": None, "device": None}
        revision = payload.get("revision")
        if revision != MODEL_REVISION:
            return {"status": "revision_mismatch", "revision": revision, "device": payload.get("device")}
        return {
            "status": "ready" if payload.get("status") == "ready" else "unavailable",
            "revision": revision,
            "device": payload.get("device"),
        }

    def classify_windows(self, wav_windows: Sequence[bytes]) -> list[float]:
        if not wav_windows or len(wav_windows) > MAX_WINDOWS_PER_CALL:
            raise ValueError(f"expected 1 to {MAX_WINDOWS_PER_CALL} WAV windows")
        payload = self._request(
            "POST",
            "/v1/voice-synthetic/classify",
            json={
                "revision": MODEL_REVISION,
                "windows": [base64.b64encode(window).decode("ascii") for window in wav_windows],
            },
        )
        if payload.get("revision") != MODEL_REVISION:
            raise VoiceSyntheticInferenceError("Ревизия удалённого классификатора не совпадает")
        raw_scores = payload.get("fake_scores")
        if not isinstance(raw_scores, list) or not 1 <= len(raw_scores) <= len(wav_windows):
            raise VoiceSyntheticInferenceError("Удалённый классификатор вернул неверное число оценок")
        try:
            scores = [float(value) for value in raw_scores]
        except (TypeError, ValueError) as exc:
            raise VoiceSyntheticInferenceError("Удалённый классификатор вернул некорректную оценку") from exc
        if any(not 0.0 <= score <= 1.0 for score in scores):
            raise VoiceSyntheticInferenceError("Оценка удалённого классификатора вне диапазона [0, 1]")
        if len(scores) != len(wav_windows) and scores[-1] < FAKE_SCORE_THRESHOLD:
            raise VoiceSyntheticInferenceError("Удалённый классификатор вернул неполный пакет оценок")
        return scores


_client: VoiceSyntheticInferenceClient | None = None
_client_lock = threading.Lock()


def get_voice_synthetic_client() -> VoiceSyntheticInferenceClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = VoiceSyntheticInferenceClient.from_settings()
        return _client


def close_voice_synthetic_client() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None
