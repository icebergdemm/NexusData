"""
Hash-based change-detection helpers for the game-server cache sync API
(routes/cache_sync.py).

Hashes are computed on demand from live DB rows — nothing is persisted in
the schema. The Barotrauma plugin (PlayerCacheStore.cs) keeps its own SQLite
copy of these hashes alongside the cached data; when a hash it holds no
longer matches what these functions compute, that's the signal to refetch
the full payload instead of trusting the local copy.
"""
import hashlib
import json
from typing import Any, Dict, List, Optional


def _stable_hash(data: Any) -> str:
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def compute_player_profile_hash(player) -> str:
    """Hash of everything the game server's cached player profile carries —
    mirrors the fields GET /players/info exposes (PlayerDataOut)."""
    raw_ach = player.achievements
    achievements_data = raw_ach if isinstance(raw_ach, dict) else {}
    data = {
        "player_id": player.player_id,
        "player_steamid": player.player_steamid,
        "display_name": player.display_name,
        "rank_id": player.rank_id,
        "kit_level": player.kit_level,
        "achievements_data": achievements_data,
        "labels": sorted(player.labels or []),
        "is_secret": player.is_secret,
    }
    return _stable_hash(data)


def compute_privilege_hash(merged: dict) -> str:
    """Hash of the merged privilege set a player effectively has — `merged`
    is the dict returned by privilege_resolution.merge_privilege_rows,
    already summed across every row that applies (manual grants per
    server_scope + a department-derived grant, not just one row). Opaque to
    callers — they just store and echo it back, so the internal shape can
    change freely."""
    data = {
        "group_keys": sorted(merged.get("group_keys") or []),
        "commands": sorted(merged.get("commands") or []),
        "allowed_action_ids": sorted(merged.get("allowed_action_ids") or []),
        "sources": sorted(
            (
                (s.get("server_scope") or "", bool(s.get("is_auto")))
                for s in (merged.get("sources") or [])
            ),
        ),
    }
    return _stable_hash(data)


def compute_reference_data_hash(kits: List, mappings: Dict[str, dict]) -> str:
    """Hash of the whole kit catalog + static mapping bundle ("наборы и
    прочая служебная информация") — refreshed as a unit since it's small
    and changes rarely, unlike per-player data."""
    data = {
        "kits": sorted(
            [
                {
                    "id": k.id,
                    "job_id": k.job_id,
                    "kit_level": k.kit_level,
                    "items": k.items,
                    "variation_2_items": k.variation_2_items,
                    "variation_3_items": k.variation_3_items,
                    "is_special": k.is_special,
                    "special_variation": k.special_variation,
                }
                for k in kits
            ],
            key=lambda d: d["id"],
        ),
        "mappings": mappings,
    }
    return _stable_hash(data)
