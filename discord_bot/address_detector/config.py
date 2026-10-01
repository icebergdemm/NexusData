from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping

from .scope import ScopeConfig


KNOWN_KEYS = (
    "moderation_address_detector_enabled",
    "moderation_signals_channel_id",
    "moderator_role_id",
    "redadmin_emoji_id",
    "monitored_channel_ids",
    "monitored_category_ids",
    "excluded_channel_ids",
    "address_context_messages",
    "address_incident_window_seconds",
    "address_analyzer_mode",
    "address_likely_threshold",
    "address_clean_threshold",
    "address_analysis_timeout_seconds",
    "onnx_model_path",
    "onnx_tokenizer_path",
    "address_queue_size",
    "address_worker_count",
    "address_llm_shadow_enabled",
    "address_llm_quota_min",
    "address_llm_queue_limit",
    "address_llm_queue_wait_timeout_seconds",
    "address_llm_backend_timeout_seconds",
    "address_llm_max_request_age_seconds",
)


def _raw(settings: object, overrides: Mapping[str, object], key: str, default: object) -> object:
    if key in overrides and overrides[key] is not None:
        return overrides[key]
    return getattr(settings, key, default)


def _bool(value: object, key: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{key}: expected boolean")


def _int(value: object, key: str, minimum: int, maximum: int | None = None) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key}: expected integer") from exc
    if parsed < minimum or (maximum is not None and parsed > maximum):
        raise ValueError(f"{key}: out of range")
    return parsed


def _float(value: object, key: str) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key}: expected number") from exc
    if not 0 <= parsed <= 1:
        raise ValueError(f"{key}: expected 0..1")
    return parsed


def _id(value: object, key: str) -> str:
    text = str(value or "").strip()
    if text and (not text.isdigit() or int(text) <= 0):
        raise ValueError(f"{key}: expected Discord ID")
    return text


def _id_set(value: object, key: str) -> set[int]:
    if isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        text = str(value or "").strip()
        if not text:
            return set()
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{key}: invalid JSON list") from exc
        else:
            raw = text.split(",")
    if not isinstance(raw, list):
        raise ValueError(f"{key}: expected list of Discord IDs")
    result: set[int] = set()
    for item in raw:
        text = str(item).strip()
        if text:
            result.add(int(_id(text, key)))
    return result


@dataclass(frozen=True)
class DetectorConfig:
    enabled: bool = False
    signals_channel_id: str = ""
    moderator_role_id: str = ""
    redadmin_emoji_id: str = ""
    monitored_channel_ids: set[int] | frozenset[int] = frozenset()
    monitored_category_ids: set[int] | frozenset[int] = frozenset()
    excluded_channel_ids: set[int] | frozenset[int] = frozenset()
    context_messages: int = 4
    incident_window_seconds: int = 120
    analyzer_mode: str = "hybrid"
    likely_threshold: float = 0.75
    clean_threshold: float = 0.80
    analysis_timeout_seconds: float = 15.0
    onnx_model_path: str = ""
    onnx_tokenizer_path: str = ""
    queue_size: int = 128
    worker_count: int = 1
    llm_shadow_enabled: bool = False
    llm_quota_min: int = 6
    llm_queue_limit: int = 10
    llm_queue_wait_timeout_seconds: int = 60
    llm_backend_timeout_seconds: int = 25
    llm_max_request_age_seconds: int = 120

    @classmethod
    def from_settings(cls, settings: object) -> "DetectorConfig":
        return load_detector_config(settings, {})

    @property
    def scope(self) -> ScopeConfig:
        return ScopeConfig(
            monitored_channel_ids=set(self.monitored_channel_ids),
            monitored_category_ids=set(self.monitored_category_ids),
            excluded_channel_ids=set(self.excluded_channel_ids),
        )

    def public_status(self) -> dict:
        return {
            "enabled": self.enabled,
            "monitored_channel_count": len(self.monitored_channel_ids),
            "signals_channel_configured": bool(self.signals_channel_id),
            "moderator_role_configured": bool(self.moderator_role_id),
            "emoji_configured": bool(self.redadmin_emoji_id),
            "analyzer_mode": self.analyzer_mode,
            "model_configured": bool(self.onnx_model_path),
            "queue_size": self.queue_size,
            "worker_count": self.worker_count,
            "llm_shadow_enabled": self.llm_shadow_enabled,
            "llm_quota_min": self.llm_quota_min,
            "llm_queue_limit": self.llm_queue_limit,
        }


def load_detector_config(settings: object, overrides: Mapping[str, object]) -> DetectorConfig:
    mode = str(_raw(settings, overrides, "address_analyzer_mode", "hybrid")).strip().lower()
    if mode not in {"rules", "hybrid"}:
        raise ValueError("address_analyzer_mode: expected rules or hybrid")
    return DetectorConfig(
        enabled=_bool(_raw(settings, overrides, "moderation_address_detector_enabled", False), "moderation_address_detector_enabled"),
        signals_channel_id=_id(_raw(settings, overrides, "moderation_signals_channel_id", ""), "moderation_signals_channel_id"),
        moderator_role_id=_id(_raw(settings, overrides, "moderator_role_id", ""), "moderator_role_id"),
        redadmin_emoji_id=_id(_raw(settings, overrides, "redadmin_emoji_id", ""), "redadmin_emoji_id"),
        monitored_channel_ids=_id_set(_raw(settings, overrides, "monitored_channel_ids", ""), "monitored_channel_ids"),
        monitored_category_ids=_id_set(_raw(settings, overrides, "monitored_category_ids", ""), "monitored_category_ids"),
        excluded_channel_ids=_id_set(_raw(settings, overrides, "excluded_channel_ids", ""), "excluded_channel_ids"),
        context_messages=_int(_raw(settings, overrides, "address_context_messages", 4), "address_context_messages", 1, 5),
        incident_window_seconds=_int(_raw(settings, overrides, "address_incident_window_seconds", 120), "address_incident_window_seconds", 1, 86400),
        analyzer_mode=mode,
        likely_threshold=_float(_raw(settings, overrides, "address_likely_threshold", 0.75), "address_likely_threshold"),
        clean_threshold=_float(_raw(settings, overrides, "address_clean_threshold", 0.80), "address_clean_threshold"),
        analysis_timeout_seconds=float(_raw(settings, overrides, "address_analysis_timeout_seconds", 15)),
        onnx_model_path=str(_raw(settings, overrides, "onnx_model_path", "") or "").strip(),
        onnx_tokenizer_path=str(_raw(settings, overrides, "onnx_tokenizer_path", "") or "").strip(),
        queue_size=_int(_raw(settings, overrides, "address_queue_size", 128), "address_queue_size", 1, 10000),
        worker_count=_int(_raw(settings, overrides, "address_worker_count", 1), "address_worker_count", 1, 16),
        llm_shadow_enabled=_bool(_raw(settings, overrides, "address_llm_shadow_enabled", False), "address_llm_shadow_enabled"),
        llm_quota_min=_int(_raw(settings, overrides, "address_llm_quota_min", 6), "address_llm_quota_min", 1, 60),
        llm_queue_limit=_int(_raw(settings, overrides, "address_llm_queue_limit", 10), "address_llm_queue_limit", 1, 100),
        llm_queue_wait_timeout_seconds=_int(_raw(settings, overrides, "address_llm_queue_wait_timeout_seconds", 60), "address_llm_queue_wait_timeout_seconds", 1, 600),
        llm_backend_timeout_seconds=_int(_raw(settings, overrides, "address_llm_backend_timeout_seconds", 25), "address_llm_backend_timeout_seconds", 1, 120),
        llm_max_request_age_seconds=_int(_raw(settings, overrides, "address_llm_max_request_age_seconds", 120), "address_llm_max_request_age_seconds", 1, 3600),
    )
