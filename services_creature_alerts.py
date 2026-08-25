"""Detection helpers for automatic AI alerts about newly observed creatures."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


WORLD_UNITS_PER_METER = 100.0
YASA_ALERT_RADIUS_METERS = 250.0

# These contacts must remain sector-wide large targets even when an old or
# operator-supplied AI_LARGE_CREATURE_SPECIES value replaces the config
# default. Giant Spineling appears under both word orders in server/mod
# payloads, so aliases are compared without separators.
BUILTIN_LARGE_CREATURE_SPECIES = frozenset(
    {
        "Watcher",
        "Spineling_giant",
        "GiantSpineling",
    }
)
_BUILTIN_LARGE_CREATURE_KEYS = frozenset(
    "".join(character for character in value.casefold() if character.isalnum())
    for value in BUILTIN_LARGE_CREATURE_SPECIES
)
_SPECIES_VARIANT_SUFFIXES = ("_male", "_female", "_m", "_f")


@dataclass(frozen=True)
class LargeCreatureSpawn:
    character_id: str
    species: str
    distance_from_yasa_m_approx: int
    contact_class: str = "large"
    yasa_perimeter: bool = False


def detect_large_creature_spawns(
    *,
    current_characters: Any,
    submarines: Any,
    current_submarines: Any,
    large_species: Iterable[str],
    abyss_species: Iterable[str],
) -> list[LargeCreatureSpawn]:
    """Return newly alertable observations relative to Yasa.

    This function deliberately evaluates every current snapshot. Durable
    deduplication is handled by ``AICreatureAlert`` after the position of Yasa
    becomes available. This avoids losing contacts when a snapshot races ahead
    of ``world/init``. Configured large contacts are alertable at any distance;
    every other non-abyss creature becomes alertable inside Yasa's 250-metre
    perimeter. Species in the abyss denylist are always rejected.
    """

    if not isinstance(current_characters, list):
        return []

    yasa_position = find_yasa_position(submarines, current_submarines)
    if yasa_position is None:
        return []

    denied = {str(value).strip().casefold() for value in abyss_species if str(value).strip()}
    result: list[LargeCreatureSpawn] = []
    seen_ids: set[str] = set()
    yasa_x, yasa_y = yasa_position
    for item in current_characters:
        if not isinstance(item, dict) or not _valid_id(item.get("id")):
            continue
        character_id = str(item["id"])
        if character_id in seen_ids:
            continue
        if not _false_flag(item.get("human")) or _truthy_flag(item.get("dead")):
            continue
        species = item.get("species")
        if not isinstance(species, str) or not species.strip():
            continue
        normalized_species = species.strip().casefold()
        if _species_matches(normalized_species, denied):
            continue
        x = _finite_number(item.get("x"))
        y = _finite_number(item.get("y"))
        if x is None or y is None:
            continue

        distance_m = math.hypot(x - yasa_x, y - yasa_y) / WORLD_UNITS_PER_METER
        is_large = is_configured_large_species(species, large_species)
        in_yasa_perimeter = distance_m <= YASA_ALERT_RADIUS_METERS
        if not is_large and not in_yasa_perimeter:
            continue
        result.append(
            LargeCreatureSpawn(
                character_id=character_id,
                species=species.strip(),
                distance_from_yasa_m_approx=_round_distance(distance_m),
                contact_class="large" if is_large else "unclassified",
                yasa_perimeter=in_yasa_perimeter,
            )
        )
        seen_ids.add(character_id)
    return result


def _species_matches(species: str, configured: set[str]) -> bool:
    """Match an exact prefab or a server-side variant such as ``*_m``/``*_f``."""

    return species in configured or any(species.startswith(f"{base}_") for base in configured)


def is_configured_large_species(species: Any, configured: Iterable[str]) -> bool:
    if not isinstance(species, str) or not species.strip():
        return False
    normalized = species.strip().casefold()
    values = {str(value).strip().casefold() for value in configured if str(value).strip()}
    return _species_matches(normalized, values) or _is_builtin_large_species(normalized)


def _is_builtin_large_species(species: str) -> bool:
    base = species
    for suffix in _SPECIES_VARIANT_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    key = "".join(character for character in base if character.isalnum())
    return key in _BUILTIN_LARGE_CREATURE_KEYS


def find_yasa_position(submarines: Any, current_submarines: Any) -> tuple[float, float] | None:
    if not isinstance(submarines, list) or not isinstance(current_submarines, list):
        return None

    def is_yasa_name(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        name = value.casefold()
        return "yasa" in name or "яса" in name

    yasa_ids = {
        str(item["id"])
        for item in submarines
        if isinstance(item, dict)
        and _valid_id(item.get("id"))
        and is_yasa_name(item.get("name"))
    }

    for item in current_submarines:
        if not isinstance(item, dict):
            continue
        if str(item.get("id")) not in yasa_ids and not is_yasa_name(item.get("name")):
            continue
        x = _finite_number(item.get("x"))
        y = _finite_number(item.get("y"))
        if x is not None and y is not None:
            return x, y
    return None


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes"}
    return bool(value)


def _false_flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"0", "false", "no"}
    return value is False or (isinstance(value, int | float) and value == 0)


def is_nonhuman_character(value: Any) -> bool:
    return isinstance(value, dict) and _false_flag(value.get("human"))


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
