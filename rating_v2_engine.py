"""Versioned, server-owned rating calculation for the v2 event ledger.

The weights are deliberately small and explicit. They are a dev candidate,
not a claim that the final production constants have been magically proven;
the backtest/preview path is where those numbers get accepted or replaced.
"""

import math

FORMULA_VERSION = "rating-v2-ledger-v1"

# Separate maps make it impossible for a PvP action to accidentally borrow a
# Mission-only score. Missing mappings are intentionally unmapped, not zeroed
# silently in the UI.
EVENT_WEIGHTS = {
    "mission": {
        "captain": {
            "navigation.destination": 5.0,
            "pilot.controlled": 0.5,
            "combat.monster_kill": 3.0,
            "player.self_death": -80.0,
        },
        "mechanic": {
            "repair.hull": 1.5,
            "repair.device": 2.0,
            "engineering.craft": 0.5,
            "engineering.wire": 0.6,
            "player.self_death": -60.0,
        },
        "medic": {
            "medical.heal": 1.5,
            "medical.revive": 8.0,
            "player.self_death": -60.0,
        },
        "security": {
            "combat.monster_damage": 0.8,
            "combat.monster_kill": 4.0,
            "combat.shoot": 0.05,
            "player.self_death": -50.0,
        },
        "engineer": {
            "engineering.reactor": 1.5,
            "engineering.wire": 2.0,
            "engineering.craft": 1.5,
            "repair.hull": 0.5,
            "repair.device": 0.6,
            "player.self_death": -60.0,
        },
    },
    "pvp": {
        "captain": {
            "navigation.destination": 2.0,
            "combat.player_damage": 0.2,
            "combat.player_kill": 5.0,
            "player.self_death": -30.0,
        },
        "mechanic": {
            "repair.hull": 1.5,
            "repair.device": 2.0,
            "engineering.craft": 0.5,
            "engineering.wire": 0.6,
            "player.self_death": -45.0,
        },
        "medic": {
            "medical.heal": 1.5,
            "medical.revive": 8.0,
            "player.self_death": -45.0,
        },
        "security": {
            "combat.player_damage": 0.8,
            "combat.player_kill": 4.0,
            "combat.shoot": 0.05,
            "player.self_death": -40.0,
        },
        "engineer": {
            "engineering.reactor": 1.5,
            "engineering.wire": 2.0,
            "engineering.craft": 1.5,
            "repair.hull": 0.5,
            "repair.device": 0.6,
            "player.self_death": -45.0,
        },
    },
}

LEGACY_ACTION_ALIASES = {
    "pilot_submarine": "pilot.controlled",
    "navigate_to_destination": "navigation.destination",
    "monster_killed": "combat.monster_kill",
    "damage_dealt_monsters": "combat.monster_damage",
    "damage_dealt_players": "combat.player_damage",
    "shoot_weapon": "combat.shoot",
    "repair_hull": "repair.hull",
    "repair_device": "repair.device",
    "craft_item": "engineering.craft",
    "wire_connection": "engineering.wire",
    "operate_reactor": "engineering.reactor",
    "heal_player": "medical.heal",
    "revive_player": "medical.revive",
    "self_death": "player.self_death",
}


def event_weight(mode: str, role: str, event_type: str) -> float | None:
    event_type = event_type.removeprefix("legacy.")
    canonical = LEGACY_ACTION_ALIASES.get(event_type, event_type)
    return EVENT_WEIGHTS.get(mode, {}).get(role, {}).get(canonical)


def calculate_event_delta(mode: str, role: str, event_type: str, raw_value: float, weights=None) -> tuple[float, str]:
    weight_map = weights if weights is not None else EVENT_WEIGHTS.get(mode, {})
    event_type = event_type.removeprefix("legacy.")
    canonical = LEGACY_ACTION_ALIASES.get(event_type, event_type)
    weight = weight_map.get(role, {}).get(canonical)
    if weight is None:
        return 0.0, "unmapped"
    return float(raw_value) * weight, "mapped"


def activity_factor(qualified_rounds: int) -> float:
    """Bounded confidence multiplier; playtime never replaces usefulness."""
    return 0.85 + 0.15 * min(max(qualified_rounds, 0) / 10.0, 1.0)


def rating_from_points(points: float, qualified_rounds: int) -> float:
    points = max(float(points), 0.0)
    if points <= 0:
        return 0.0
    # Keep the established readable compression while making the bounded
    # activity factor explicit and reproducible in explanations.
    return round(1.3 * (points ** 0.35) * activity_factor(qualified_rounds), 2)


def diminishing_factor(previous_count: int) -> float:
    """Soft cap repeated events without making the first useful action odd."""
    return max(0.25, 1.0 / math.sqrt(1.0 + max(previous_count, 0) / 24.0))
