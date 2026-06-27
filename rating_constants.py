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
    """
    if cumulative_points <= 0:
        return 0.0
    return round(K * (cumulative_points ** POWER), 2)
