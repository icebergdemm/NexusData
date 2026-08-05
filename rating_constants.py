"""
Rating system constants and formula.

Rating formula (v3 — power compression):
  Rating = K * (cumulative_points ^ POWER)

Why power compression instead of logarithm:
  - Old log formula (K*ln(1+P/S)) compressed too hard at the top:
    everyone with 20k-200k points landed in 72-77 range — impossible to rank.
  - Power function with POWER=0.35 keeps a readable spread at the top
    (rank #1 ≈ 100, rank #2 ≈ 90, ...) while still taming volume:
    doubling points only adds ~27% rating (2^0.35 = 1.27).
  - No dependency on rounds_played (which the game server doesn't reliably send).

Calibration target (real data):
  captain  avg 20k pts -> 45,  max 210k -> 100
  security avg 11k pts -> 36,  max 142k -> 90
  mechanic avg 1.7k    -> 19,  max 56k  -> 68
"""

import math

# ── Formula parameters ───────────────────────────────────────────────────────
K = 1.3          # scale factor
POWER = 0.35     # exponent < 1 → compresses high values, tames grinding

# Backward-compatibility aliases
S = 100.0        # legacy, unused by v3 formula
ROUNDS_CAP = 60  # legacy, unused by v3 formula

# Time decay: applied weekly if no activity
DECAY_THRESHOLD_DAYS = 7        # start extra decay after 1 week inactive
DECAY_RATE_PER_WEEK = 0.93      # 7% per week of inactivity
DECAY_RATE = DECAY_RATE_PER_WEEK

# Daily erosion: a small constant decay applied EVERY day to everyone.
# Keeps the leaderboard "alive" — you must keep playing to hold your spot.
DAILY_EROSION = 0.0015          # -0.15% per day baseline (~ -1% per week)
# Extra erosion per day once a player is inactive beyond the threshold
INACTIVE_EXTRA_EROSION = 0.01   # additional -1% per day when inactive
# Floor: decay_factor never drops below this (so old players aren't erased)
DECAY_FACTOR_FLOOR = 0.30

# All valid roles
ROLES = ["captain", "mechanic", "medic", "security", "engineer"]

# Weight matrix: role -> {action_type -> weight}
# NOTE: values are accumulated magnitudes (damage, meters), so weights are
# applied to raw value. Tuned so each role's primary actions dominate.
# Negative weights = penalties (self-death, teamkills) that REDUCE the score.
WEIGHT_MATRIX = {
    "captain": {
        "pilot_submarine":        1.0,
        "navigate_to_destination": 5.0,
        "monster_killed":         3.0,
        "repair_hull":            0.3,
        "extinguish_fire":        0.4,
        "operate_reactor":        0.3,
        # penalties
        "self_death":            -80.0,
        "player_killed":        -150.0,   # teamkill
    },
    "mechanic": {
        "repair_hull":            1.5,
        "repair_device":          2.0,
        "extinguish_fire":        2.5,
        "craft_item":             0.5,
        "deconstruct_item":       0.4,
        "wire_connection":        0.6,
        "operate_reactor":        0.4,
        # penalties
        "self_death":            -60.0,
        "player_killed":        -150.0,
    },
    "medic": {
        "heal_player":            1.5,
        "revive_player":          8.0,
        "craft_item":             0.4,
        # penalties
        "self_death":            -60.0,
        "player_killed":        -200.0,   # medic killing teammates = severe
    },
    "security": {
        "damage_dealt_monsters":  0.8,
        "monster_killed":         4.0,
        "shoot_weapon":           0.5,
        "damage_dealt_players":   0.2,
        "extinguish_fire":        0.3,
        # penalties
        "self_death":            -50.0,
        "player_killed":        -100.0,   # security has more leeway (combat role)
    },
    "engineer": {
        "operate_reactor":        1.5,
        "wire_connection":        2.0,
        "craft_item":             1.5,
        "deconstruct_item":       1.0,
        "repair_hull":            0.5,
        "repair_device":          0.6,
        "extinguish_fire":        0.5,
        # penalties
        "self_death":            -60.0,
        "player_killed":        -150.0,
    },
}

# Penalty map: event_type -> {role -> penalty_points}
PENALTY_MAP = {
    "player_death": {
        "captain":  50,
        "mechanic": 50,
        "medic":    50,
        "security": 30,
        "engineer": 50,
    },
    "ship_loss": {
        "captain":  200,
    },
    "crew_death": {
        "medic":    100,
    },
    "friendly_fire": {
        "security": 150,
    },
}


def compute_rating(cumulative_points: float, rounds_played: int = 0) -> float:
    """
    Power-compressed rating: K * points^POWER (unbounded — grows infinitely).

    Growth slows down at the top (concave curve), but never stops:
    a dedicated player can always climb higher with more points.

    The rounds_played arg is accepted for backward-compatibility but ignored.
    Reads K/POWER as module globals (not closed-over at import time) so a
    superuser's edit via load_overrides_from_db takes effect immediately,
    without a process restart.
    """
    if cumulative_points <= 0:
        return 0.0
    return round(K * (cumulative_points ** POWER), 2)


# ── Superuser-editable overrides (persisted in AppSetting) ──────────────────
# The values above are the shipped defaults. A superuser can override any of
# them from the Рейтинг page; overrides are stored as one JSON blob under
# AppSetting.key == "rating_config" and applied on top of these module
# globals in place, so every reader — compute_rating (global lookup at call
# time) and rating_engine.py (which imports this module, not bound names —
# see its `import rating_constants as rc`) — picks up an edit immediately
# without a process restart.
_APP_SETTING_KEY = "rating_config"

# Snapshot of the shipped defaults, taken before any override is applied —
# used by the admin API to show "current vs default" and to support "reset
# to defaults".
DEFAULT_CONFIG = {
    "K": K,
    "POWER": POWER,
    "DECAY_THRESHOLD_DAYS": DECAY_THRESHOLD_DAYS,
    "DECAY_RATE_PER_WEEK": DECAY_RATE_PER_WEEK,
    "DAILY_EROSION": DAILY_EROSION,
    "INACTIVE_EXTRA_EROSION": INACTIVE_EXTRA_EROSION,
    "DECAY_FACTOR_FLOOR": DECAY_FACTOR_FLOOR,
    "WEIGHT_MATRIX": {role: dict(weights) for role, weights in WEIGHT_MATRIX.items()},
    "PENALTY_MAP": {event: dict(roles) for event, roles in PENALTY_MAP.items()},
}

_SCALAR_KEYS = (
    "K", "POWER", "DECAY_THRESHOLD_DAYS", "DECAY_RATE_PER_WEEK",
    "DAILY_EROSION", "INACTIVE_EXTRA_EROSION", "DECAY_FACTOR_FLOOR",
)


def get_current_config() -> dict:
    """Snapshot of the config as currently in effect (defaults + any applied
    override), in the same shape as DEFAULT_CONFIG."""
    return {
        "K": K,
        "POWER": POWER,
        "DECAY_THRESHOLD_DAYS": DECAY_THRESHOLD_DAYS,
        "DECAY_RATE_PER_WEEK": DECAY_RATE_PER_WEEK,
        "DAILY_EROSION": DAILY_EROSION,
        "INACTIVE_EXTRA_EROSION": INACTIVE_EXTRA_EROSION,
        "DECAY_FACTOR_FLOOR": DECAY_FACTOR_FLOOR,
        "WEIGHT_MATRIX": {role: dict(weights) for role, weights in WEIGHT_MATRIX.items()},
        "PENALTY_MAP": {event: dict(roles) for event, roles in PENALTY_MAP.items()},
    }


def _apply_config(cfg: dict) -> None:
    """Mutate this module's globals from a (validated) config dict. Matrices
    are replaced wholesale rather than merged — the admin UI always submits
    the full matrix it loaded, edited."""
    g = globals()
    for key in _SCALAR_KEYS:
        if key in cfg:
            g[key] = float(cfg[key])
    g["DECAY_RATE"] = g["DECAY_RATE_PER_WEEK"]  # alias kept in sync
    if "WEIGHT_MATRIX" in cfg:
        g["WEIGHT_MATRIX"] = {
            role: {action: float(w) for action, w in weights.items()}
            for role, weights in cfg["WEIGHT_MATRIX"].items()
        }
    if "PENALTY_MAP" in cfg:
        g["PENALTY_MAP"] = {
            event: {role: float(p) for role, p in roles.items()}
            for event, roles in cfg["PENALTY_MAP"].items()
        }


def load_overrides_from_db(db) -> bool:
    """Load the persisted override (if any) from AppSetting and apply it.
    Call once at process startup (main.py) so a saved override survives a
    restart; the admin API also calls this right after saving. Returns True
    if an override was found and applied."""
    import json
    from models import AppSetting

    row = db.query(AppSetting).filter(AppSetting.key == _APP_SETTING_KEY).first()
    if not row or not row.value:
        return False
    try:
        cfg = json.loads(row.value)
    except (TypeError, ValueError):
        return False
    _apply_config(cfg)
    return True


def save_overrides_to_db(db, cfg: dict) -> dict:
    """Validate, persist, and immediately apply a superuser-submitted config.
    Returns the effective config after applying. Raises ValueError on bad
    input (missing role/action keys, non-numeric values, etc.) — callers
    should turn that into an HTTP 400."""
    import json
    from models import AppSetting

    validated: dict = {}
    for key in _SCALAR_KEYS:
        if key in cfg:
            try:
                validated[key] = float(cfg[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} должен быть числом")

    if "WEIGHT_MATRIX" in cfg:
        wm = cfg["WEIGHT_MATRIX"]
        if not isinstance(wm, dict) or set(wm.keys()) - set(ROLES):
            raise ValueError("WEIGHT_MATRIX: неизвестная роль")
        validated_wm = {}
        for role, weights in wm.items():
            if not isinstance(weights, dict):
                raise ValueError(f"WEIGHT_MATRIX[{role}] должен быть объектом")
            validated_role = {}
            for action, w in weights.items():
                try:
                    validated_role[action] = float(w)
                except (TypeError, ValueError):
                    raise ValueError(f"WEIGHT_MATRIX[{role}][{action}] должен быть числом")
            validated_wm[role] = validated_role
        validated["WEIGHT_MATRIX"] = validated_wm

    if "PENALTY_MAP" in cfg:
        pm = cfg["PENALTY_MAP"]
        if not isinstance(pm, dict):
            raise ValueError("PENALTY_MAP должен быть объектом")
        validated_pm = {}
        for event, roles in pm.items():
            if not isinstance(roles, dict) or set(roles.keys()) - set(ROLES):
                raise ValueError(f"PENALTY_MAP[{event}]: неизвестная роль")
            validated_pm[event] = {role: float(p) for role, p in roles.items()}
        validated["PENALTY_MAP"] = validated_pm

    _apply_config(validated)

    # Persist the FULL effective config (not just the delta the caller sent)
    # so a future load_overrides_from_db restores everything, including
    # fields left untouched by this particular save.
    full = get_current_config()
    row = db.query(AppSetting).filter(AppSetting.key == _APP_SETTING_KEY).first()
    if row:
        row.value = json.dumps(full)
    else:
        db.add(AppSetting(key=_APP_SETTING_KEY, value=json.dumps(full)))
    db.commit()
    return full


def reset_overrides_to_defaults(db) -> dict:
    """Drop the persisted override and restore the shipped defaults."""
    from models import AppSetting

    _apply_config(DEFAULT_CONFIG)
    row = db.query(AppSetting).filter(AppSetting.key == _APP_SETTING_KEY).first()
    if row:
        db.delete(row)
        db.commit()
    return get_current_config()
