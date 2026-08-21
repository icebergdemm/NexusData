"""Detection helpers for automatic AI alerts about newly spawned large creatures."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


WORLD_UNITS_PER_METER = 100.0


@dataclass(frozen=True)
class LargeCreatureSpawn:
    character_id: str
    species: str
    distance_from_yasa_m_approx: int


def detect_large_creature_spawns(
    *,
    current_characters: Any,
    submarines: Any,
    current_submarines: Any,
    large_species: Iterable[str],
    abyss_species: Iterable[str],
) -> list[LargeCreatureSpawn]:
    """Return alive, configured large-creature observations relative to Yasa.

    This function deliberately evaluates every current snapshot. Durable
    deduplication is handled by ``AICreatureAlert`` after the position of Yasa
    becomes available. This avoids losing contacts when a snapshot races ahead
    of ``world/init``. Species in the abyss denylist are rejected even if
    accidentally added to the large-creature allowlist.
    """

    if not isinstance(current_characters, list):
        return []

    yasa_position = _find_yasa_position(submarines, current_submarines)
    if yasa_position is None:
        return []

    allowed = {str(value).strip().casefold() for value in large_species if str(value).strip()}
    denied = {str(value).strip().casefold() for value in abyss_species if str(value).strip()}
    if not allowed:
        return []

    result: list[LargeCreatureSpawn] = []
    seen_ids: set[str] = set()
    yasa_x, yasa_y = yasa_position
    for item in current_characters:
        if not isinstance(item, dict) or not _valid_id(item.get("id")):
            continue
        character_id = str(item["id"])
        if character_id in seen_ids:
            continue
        if item.get("human") is not False or item.get("dead") is True:
            continue
        species = item.get("species")
        if not isinstance(species, str) or not species.strip():
            continue
        normalized_species = species.strip().casefold()
        if not _species_matches(normalized_species, allowed) or _species_matches(
            normalized_species,
            denied,
        ):
            continue
        x = _finite_number(item.get("x"))
        y = _finite_number(item.get("y"))
        if x is None or y is None:
            continue

        distance_m = math.hypot(x - yasa_x, y - yasa_y) / WORLD_UNITS_PER_METER
        result.append(
            LargeCreatureSpawn(
                character_id=character_id,
                species=species.strip(),
                distance_from_yasa_m_approx=_round_distance(distance_m),
            )
        )
        seen_ids.add(character_id)
    return result


def _species_matches(species: str, configured: set[str]) -> bool:
    """Match an exact prefab or a server-side variant such as ``*_m``/``*_f``."""

    return species in configured or any(species.startswith(f"{base}_") for base in configured)


def _find_yasa_position(submarines: Any, current_submarines: Any) -> tuple[float, float] | None:
    if not isinstance(submarines, list) or not isinstance(current_submarines, list):
        return None

    yasa_ids = {
        str(item["id"])
        for item in submarines
        if isinstance(item, dict)
        and _valid_id(item.get("id"))
        and isinstance(item.get("name"), str)
        and "yasa" in item["name"].casefold()
    }
    if not yasa_ids:
        return None

    for item in current_submarines:
        if not isinstance(item, dict) or str(item.get("id")) not in yasa_ids:
            continue
        x = _finite_number(item.get("x"))
        y = _finite_number(item.get("y"))
        if x is not None and y is not None:
            return x, y
    return None


def _round_distance(distance_m: float) -> int:
    if distance_m < 100:
        step = 5
    elif distance_m < 500:
        step = 10
    elif distance_m < 2_000:
        step = 25
    else:
        step = 100
    return int(math.floor(distance_m / step + 0.5)) * step


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _valid_id(value: Any) -> bool:
    return isinstance(value, str | int) and not isinstance(value, bool) and str(value) != ""
