"""Shared privacy helpers for site-facing player payloads."""

from __future__ import annotations

from copy import deepcopy
import re
from collections.abc import Iterable
from typing import Any

from security import check_permission


_IP_KEY_NAMES = {
    "ip", "ip_address", "player_ip", "last_ip", "last_subnet",
    "shared_ip", "ip_a", "ip_b", "subnet", "subnet_a", "subnet_b",
    "shared_subnet",
}
_IP_LIST_KEY_NAMES = {"ips", "known_ips", "shared_ips"}
_IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")


def can_view_player_ips(admin: Any) -> bool:
    return check_permission(admin, "players", "view_ip")


def _redact_text(value: str, known_ips: Iterable[str] = ()) -> str:
    result = value
    for ip in sorted({str(ip) for ip in known_ips if ip}, key=len, reverse=True):
        result = result.replace(ip, "IP скрыт")
    return _IPV4_RE.sub("IP скрыт", result)


def _redact_nested(value: Any, known_ips: Iterable[str] = ()) -> Any:
    if isinstance(value, str):
        return _redact_text(value, known_ips)
    if isinstance(value, list):
        return [_redact_nested(item, known_ips) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in _IP_KEY_NAMES:
                result[key] = None
            elif key in _IP_LIST_KEY_NAMES:
                result[key] = []
            else:
                result[key] = _redact_nested(item, known_ips)
        return result
    return value


def redact_sauron_results(results: Any, *, allow: bool) -> Any:
    if allow:
        return results
    redacted = _redact_nested(deepcopy(results))
    if not isinstance(redacted, list):
        return redacted
    for row in redacted:
        if not isinstance(row, dict):
            continue
        for key in _IP_KEY_NAMES | _IP_LIST_KEY_NAMES:
            if key in row:
                row[key] = [] if key in _IP_LIST_KEY_NAMES else None
        for signal in row.get("signals") or []:
            if not isinstance(signal, dict):
                continue
            if signal.get("type") == "shared_ip":
                signal["label"] = "Совпадение IP"
            elif signal.get("type") == "shared_subnet":
                signal["label"] = "Совпадение подсети"
            else:
                signal["label"] = _redact_text(str(signal.get("label") or ""))
    return redacted


def redact_anomaly_evidence(evidence: Any, *, allow: bool) -> Any:
    if allow:
        return evidence
    redacted = _redact_nested(deepcopy(evidence))
    if not isinstance(redacted, dict):
        return redacted
    for signal in redacted.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        if signal.get("type") == "shared_ip":
            signal["label"] = "Совпадение IP"
        elif signal.get("type") == "shared_subnet":
            signal["label"] = "Совпадение подсети"
    return redacted


def redact_dossier_payload(payload: Any, *, known_ips: Iterable[str] = (), allow: bool) -> Any:
    if allow:
        return payload
    return _redact_nested(deepcopy(payload), known_ips)
