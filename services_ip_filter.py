"""Shared IP exclusions for moderation-only account-link analysis."""

from __future__ import annotations

import ipaddress
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import AppSetting, PlayerIP
from utils import is_bogon_ip

AI_DOSSIER_IGNORED_IPS_SETTING = "ai_dossier_ignored_ips"
AUTO_IP_IGNORE_DAYS = 30
AUTO_IP_IGNORE_MIN_PLAYERS = 5
MAX_MANUAL_IP_ENTRIES = 100
MAX_MANUAL_IP_VALUE_CHARS = 1900


def normalize_ip_entries(value: Any) -> list[str]:
    """Validate and canonicalize one IP or CIDR per entry."""
    if isinstance(value, str):
        values = value.replace(",", "\n").splitlines()
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        raise ValueError("ignored_ips must be a list or newline-separated text")

    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        entry = str(raw).strip()
        if not entry:
            continue
        try:
            parsed = ipaddress.ip_address(entry) if "/" not in entry else ipaddress.ip_network(entry, strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid IP or CIDR: {entry}") from exc
        canonical = str(parsed)
        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    if len(result) > MAX_MANUAL_IP_ENTRIES:
        raise ValueError(f"ignored_ips cannot contain more than {MAX_MANUAL_IP_ENTRIES} entries")
    return result


def _load_manual_entries(db: Session) -> list[str]:
    try:
        row = db.query(AppSetting).filter(AppSetting.key == AI_DOSSIER_IGNORED_IPS_SETTING).first()
    except Exception:
        db.rollback()
        return []
    if not row or not row.value:
        return []
    try:
        value = json.loads(row.value)
    except (TypeError, ValueError):
        value = row.value
    try:
        return normalize_ip_entries(value)
    except ValueError:
        return []


def get_manual_ip_entries(db: Session) -> list[str]:
    return _load_manual_entries(db)


@dataclass(frozen=True)
class AnalysisIPPolicy:
    manual_entries: tuple[str, ...]
    auto_ignored_ips: frozenset[str]

    @property
    def manual_networks(self) -> tuple[ipaddress._BaseNetwork, ...]:
        return tuple(
            ipaddress.ip_network(entry, strict=False)
            for entry in self.manual_entries
        )

    def is_ignored(self, value: Any) -> bool:
        raw = str(value).strip()
        try:
            if "/" in raw:
                network = ipaddress.ip_network(raw, strict=False)
                return (
                    any(network.overlaps(manual) for manual in self.manual_networks)
                    or any(ipaddress.ip_address(ip) in network for ip in self.auto_ignored_ips)
                )
            address = ipaddress.ip_address(raw)
        except (TypeError, ValueError):
            return True
        return str(address) in self.auto_ignored_ips or any(address in network for network in self.manual_networks)

    @property
    def excluded_count(self) -> int:
        return len(self.auto_ignored_ips) + len(self.manual_entries)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {"manual": self.manual_entries, "auto": sorted(self.auto_ignored_ips)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @property
    def exact_ignored_ips(self) -> tuple[str, ...]:
        return tuple(sorted({ip for ip in self.auto_ignored_ips} | {
            entry for entry in self.manual_entries if "/" not in entry
        }))


def build_analysis_ip_policy(
    db: Session,
    now: datetime | None = None,
    *,
    auto_days: int = AUTO_IP_IGNORE_DAYS,
    auto_min_players: int = AUTO_IP_IGNORE_MIN_PLAYERS,
) -> AnalysisIPPolicy:
    """Build manual plus mass-player IP exclusions for analysis only."""
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=auto_days)
    rows = (
        db.query(PlayerIP.ip)
        .filter(PlayerIP.last_seen >= cutoff)
        .group_by(PlayerIP.ip)
        .having(func.count(func.distinct(PlayerIP.player_id)) >= auto_min_players)
        .all()
    )
    auto_ignored = {
        str(row[0]).strip()
        for row in rows
        if row[0] and not is_bogon_ip(row[0])
    }
    return AnalysisIPPolicy(tuple(_load_manual_entries(db)), frozenset(auto_ignored))


def save_manual_ip_entries(db: Session, entries: Any) -> list[str]:
    normalized = normalize_ip_entries(entries)
    row = db.query(AppSetting).filter(AppSetting.key == AI_DOSSIER_IGNORED_IPS_SETTING).first()
    encoded = json.dumps(normalized, ensure_ascii=False)
    if len(encoded) > MAX_MANUAL_IP_VALUE_CHARS:
        raise ValueError(f"ignored_ips configuration is too long (maximum {MAX_MANUAL_IP_VALUE_CHARS} characters)")
    if row:
        row.value = encoded
    else:
        db.add(AppSetting(key=AI_DOSSIER_IGNORED_IPS_SETTING, value=encoded))
    return normalized
