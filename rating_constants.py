"""
Rating system constants: weight matrix, penalty map, formula parameters.
"""

# Logarithmic formula: Rating = K * ln(1 + P / S)
K = 10.0   # scale factor
S = 100.0  # shift factor

# Time decay
DECAY_THRESHOLD_DAYS = 14  # no decay within this period
DECAY_RATE = 0.98          # 2% reduction per day beyond threshold

# All valid roles
ROLES = ["captain", "mechanic", "medic", "security", "engineer"]

# Weight matrix: role -> {action_type -> weight}
# Bold = primary role. Non-primary roles get reduced weight or 0.
WEIGHT_MATRIX = {
    "captain": {
        "pilot_submarine": 2.0,
        "repair_hull": 0.3,
        "extinguish_fire": 0.3,
    },
    "mechanic": {
        "repair_hull": 2.0,
        "repair_device": 2.0,
        "extinguish_fire": 2.5,
        "craft_item": 0.5,
        "deconstruct_item": 0.3,
        "wire_connection": 0.5,
        "operate_reactor": 0.3,
    },
    "medic": {
        "heal_player": 3.0,
        "revive_player": 5.0,
        "craft_item": 0.3,
    },
    "security": {
        "shoot_weapon": 1.5,
        "damage_dealt_monsters": 2.0,
        "damage_dealt_players": 0.5,
        "extinguish_fire": 0.3,
    },
    "engineer": {
        "wire_connection": 2.0,
        "operate_reactor": 2.0,
        "craft_item": 1.5,
        "deconstruct_item": 1.0,
        "repair_hull": 0.6,
        "repair_device": 0.6,
        "extinguish_fire": 0.5,
    },
}

# Penalty map: event_type -> {role -> penalty_points}
# Points are subtracted from cumulative_points
PENALTY_MAP = {
    "player_death": {
        "captain": 50,
        "mechanic": 50,
        "medic": 50,
        "security": 30,
        "engineer": 50,
    },
    "ship_loss": {
        "captain": 200,
    },
    "crew_death": {
        "medic": 100,
    },
    "friendly_fire": {
        "security": 150,
    },
}
