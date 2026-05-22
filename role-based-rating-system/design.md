# Design Document: Role-Based Rating System

## Overview

The role-based rating system computes per-role ratings for Barotrauma players across ASPER1/ASPER2 servers. The C# game plugin tracks actions and negative events during sessions, sending them to the NexusData Python/FastAPI backend at logout. The backend applies a weight matrix, logarithmic formula, time decay, and penalties to maintain a global rating per role per player. A retrieval API exposes ratings to the admin panel and player profiles.

## Architecture

```
┌──────────────────┐       POST /api/v1/events        ┌──────────────────────┐
│  Game_Plugin     │ ──────────────────────────────►   │  NexusData Backend   │
│  (C# Barotrauma) │   player_sessions.logout          │  (Python/FastAPI)    │
│                  │   + role_actions                   │                      │
│  Tracks:         │   + negative_events               │  Rating_Engine:      │
│  - role_actions  │                                   │  - Weight Matrix     │
│  - negative_evts │                                   │  - Log formula       │
└──────────────────┘                                   │  - Time decay        │
                                                       │  - Penalty system    │
┌──────────────────┐       GET /api/v1/ratings/{id}    │                      │
│  Admin Panel /   │ ◄─────────────────────────────    │  PostgreSQL DB       │
│  Frontend        │   { ratings per role }            └──────────────────────┘
└──────────────────┘
```

The system spans two components:
1. **Game_Plugin (C#)** — Extends the existing RoundManager_ND plugin to track negative events and include them in the logout payload.
2. **Rating_Engine (Python/FastAPI)** — New module in NexusData that processes logout events, computes ratings, and serves the retrieval API.

## Components and Interfaces

### Component 1: Rating Engine (Backend)

**Location:** NexusData backend — new module `rating_engine.py`

**Responsibilities:**
- Process `player_sessions.logout` events containing `role_actions` and `negative_events`
- Apply weight matrix to compute weighted session points
- Update cumulative points and recompute logarithmic rating
- Apply negative event penalties
- Apply lazy time decay on read/update
- Serve rating retrieval API

**Interface — Event Handler:**
```python
async def handle_logout_event(event: dict) -> None:
    """Called by the existing /api/v1/events dispatcher when type == 'player_sessions.logout'"""
```

**Interface — API Endpoint:**
```python
@router.get("/api/v1/ratings/{steam_id}")
async def get_ratings(steam_id: str) -> RatingResponse:
    """Returns current ratings for all 5 roles"""
```

### Component 2: Game Plugin Extension (C#)

**Location:** `RoundManager_ND.cs` — additions to existing `PlayerSessionData` and `SendLogoutEventAsync`

**Responsibilities:**
- Track player_death, ship_loss, crew_death, friendly_fire events during session
- Include `negative_events` array in the logout payload (version 2)

**Interface — Logout Payload (extended):**
```json
{
  "type": "player_sessions.logout",
  "version": 2,
  "source": "ASPER1",
  "payload": {
    "player_steamid": "76561198000000000",
    "role_actions": [...],
    "role_stats": [...],
    "negative_events": [
      {"event_type": "player_death", "role": "mechanic", "count": 1},
      {"event_type": "friendly_fire", "role": "security", "count": 2}
    ]
  }
}
```

### Full Weight Matrix

Each action has a **primary role** where it contributes the most. Non-primary roles receive a reduced weight (10–30% of primary). Actions irrelevant to a role receive weight 0.

| Action Type | Captain | Mechanic | Medic | Security | Engineer |
|---|---|---|---|---|---|
| heal_player | 0.0 | 0.0 | **3.0** | 0.0 | 0.0 |
| revive_player | 0.0 | 0.0 | **5.0** | 0.0 | 0.0 |
| repair_hull | 0.3 | **2.0** | 0.0 | 0.0 | 0.6 |
| repair_device | 0.0 | **2.0** | 0.0 | 0.0 | 0.6 |
| extinguish_fire | 0.3 | **2.5** | 0.0 | 0.3 | 0.5 |
| craft_item | 0.0 | 0.5 | 0.3 | 0.0 | **1.5** |
| deconstruct_item | 0.0 | 0.3 | 0.0 | 0.0 | **1.0** |
| wire_connection | 0.0 | 0.5 | 0.0 | 0.0 | **2.0** |
| pilot_submarine | **2.0** | 0.0 | 0.0 | 0.0 | 0.0 |
| operate_reactor | 0.0 | 0.3 | 0.0 | 0.0 | **2.0** |
| shoot_weapon | 0.0 | 0.0 | 0.0 | **1.5** | 0.0 |
| damage_dealt_monsters | 0.0 | 0.0 | 0.0 | **2.0** | 0.0 |
| damage_dealt_players | 0.0 | 0.0 | 0.0 | **0.5** | 0.0 |

**Bold** = primary role weight. All values are per unit of the action's `value` field.

**Weight Justification:**
- **Captain**: Only piloting matters. Captains don't repair or fight — they navigate.
- **Mechanic**: Hull/device repair and firefighting are core. Slight credit for reactor awareness.
- **Medic**: Healing and reviving are exclusive. No cross-role bleed.
- **Security**: Combat actions (shooting, monster damage). Friendly fire tracked separately as negative event.
- **Engineer**: Wiring, reactor operation, crafting, deconstruction. Some repair overlap with mechanic at reduced weight.

### Logarithmic Rating Formula

```
Rating(P) = K * ln(1 + P / S)
```

Where:
- `P` = cumulative weighted action points (lifetime total for this role)
- `K` = 10.0 (scale factor — controls how many "rating points" per log unit)
- `S` = 100.0 (shift factor — controls how quickly early points accumulate)
- `ln` = natural logarithm

**Properties:**
- `Rating(0) = 0` (new player starts at 0)
- Monotonically increasing (more effort = higher rating, always)
- No hard cap (ln grows without bound)
- Diminishing returns: going from 0→100 points gives +6.93 rating, but 100→200 gives only +3.47

**Rating Update on Logout:**

The engine maintains `cumulative_points` and updates incrementally:
```
new_cumulative = old_cumulative + session_weighted_sum
new_rating = K * ln(1 + new_cumulative / S)
```

**Example Progression:**

| Cumulative Points | Rating |
|---|---|
| 0 | 0.00 |
| 50 | 4.05 |
| 100 | 6.93 |
| 500 | 17.92 |
| 1000 | 23.03 |
| 5000 | 39.12 |
| 10000 | 46.05 |

### Time Decay

**Formula:**
```
decayed_points = cumulative_points * decay_rate ^ days_since_last_decay
```

**Parameters:**
- `DECAY_THRESHOLD_DAYS` = 14 (no decay within 14 days of last activity)
- `DECAY_RATE` = 0.98 (2% reduction per day beyond threshold)

**Application:** Decay is applied **lazily** — computed on read (rating retrieval) or before a new rating update. The `cumulative_points` column is updated to `decayed_points` when decay is applied.

| Days Inactive (beyond threshold) | Points Retained |
|---|---|
| 0 (within threshold) | 100% |
| 7 | 86.8% |
| 14 | 75.4% |
| 30 | 54.5% |
| 60 | 29.8% |
| 90 | 16.2% |

### Negative Event Penalties

**Penalty Values (points deducted from cumulative_points):**

| Event Type | Captain | Mechanic | Medic | Security | Engineer |
|---|---|---|---|---|---|
| player_death | -50 | -50 | -50 | -30 | -50 |
| ship_loss | -200 | — | — | — | — |
| crew_death | — | — | -100 | — | — |
| friendly_fire | — | — | — | -150 | — |

"—" means the event does not apply to that role.

**Penalty Justification:**
- **player_death**: Universal penalty applied to the role being played. Security gets reduced (-30) because dying in combat is expected.
- **ship_loss**: Severe captain-only penalty. Losing the submarine is the captain's primary failure mode.
- **crew_death**: Medic-specific. A crew member dying reflects failure to heal/revive.
- **friendly_fire**: Security-specific. Shooting teammates is a critical failure for the combat role.

**Penalty Application:**
```
new_cumulative = max(0, old_cumulative - penalty_points)
new_rating = K * ln(1 + new_cumulative / S)
```

## Data Models

### Database Schema

```sql
CREATE TABLE player_ratings (
    id SERIAL PRIMARY KEY,
    steam_id VARCHAR(20) NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('captain','mechanic','medic','security','engineer')),
    cumulative_points FLOAT NOT NULL DEFAULT 0.0,
    current_rating FLOAT NOT NULL DEFAULT 0.0,
    last_activity_date TIMESTAMP WITH TIME ZONE,
    last_decay_applied_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (steam_id, role)
);

CREATE INDEX idx_player_ratings_steam_id ON player_ratings(steam_id);

CREATE TABLE rating_history (
    id SERIAL PRIMARY KEY,
    steam_id VARCHAR(20) NOT NULL,
    role VARCHAR(20) NOT NULL,
    event_type VARCHAR(30) NOT NULL,  -- 'session_update', 'penalty', 'decay'
    points_delta FLOAT NOT NULL,
    cumulative_points_before FLOAT NOT NULL,
    cumulative_points_after FLOAT NOT NULL,
    rating_before FLOAT NOT NULL,
    rating_after FLOAT NOT NULL,
    source_server VARCHAR(20),
    session_trace_id VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_rating_history_steam_id ON rating_history(steam_id);
CREATE INDEX idx_rating_history_created_at ON rating_history(created_at);
```

### Python Data Models

```python
from pydantic import BaseModel
from datetime import datetime

class PlayerRating(BaseModel):
    steam_id: str
    role: str
    cumulative_points: float = 0.0
    current_rating: float = 0.0
    last_activity_date: datetime | None = None
    last_decay_applied_at: datetime | None = None

class RatingHistoryEntry(BaseModel):
    steam_id: str
    role: str
    event_type: str  # 'session_update' | 'penalty' | 'decay'
    points_delta: float
    cumulative_points_before: float
    cumulative_points_after: float
    rating_before: float
    rating_after: float
    source_server: str | None = None
    session_trace_id: str | None = None

class RoleRatingResponse(BaseModel):
    rating: float
    cumulative_points: float
    last_activity_date: str | None

class RatingResponse(BaseModel):
    steam_id: str
    ratings: dict[str, RoleRatingResponse]
```

### C# Data Model (Plugin Addition)

```csharp
public class NegativeEventEntry
{
    public string EventType { get; set; }  // "player_death", "ship_loss", "crew_death", "friendly_fire"
    public string Role { get; set; }       // role the event is attributed to
    public int Count { get; set; }         // occurrences this session
}

// Added to PlayerSessionData:
public List<NegativeEventEntry> NegativeEvents { get; set; } = new List<NegativeEventEntry>();
```

## Error Handling

| Scenario | Behavior |
|---|---|
| Unknown action_type in payload | Weight defaults to 0.0, action ignored for rating |
| Unknown negative_event type | Ignored, no penalty applied |
| Payload missing `negative_events` field | Treated as empty array (backward compatible with v1) |
| Database write failure | Retry up to 3 times with 500ms delay, log error |
| Decay produces sub-zero points | Clamped to 0.0 |
| Penalty produces sub-zero points | Clamped to 0.0 |
| Invalid steam_id format | Return 400 Bad Request |
| Concurrent updates to same player | Use database row-level locking (SELECT FOR UPDATE) |

## Rating Update Algorithm

### Full Pseudocode (Python)

```python
import math
from collections import defaultdict
from datetime import datetime, timezone

K = 10.0
S = 100.0
DECAY_THRESHOLD_DAYS = 14
DECAY_RATE = 0.98

WEIGHT_MATRIX = {
    "captain":  {"pilot_submarine": 2.0, "repair_hull": 0.3, "extinguish_fire": 0.3},
    "mechanic": {"repair_hull": 2.0, "repair_device": 2.0, "extinguish_fire": 2.5,
                 "craft_item": 0.5, "deconstruct_item": 0.3, "wire_connection": 0.5, "operate_reactor": 0.3},
    "medic":    {"heal_player": 3.0, "revive_player": 5.0, "craft_item": 0.3},
    "security": {"shoot_weapon": 1.5, "damage_dealt_monsters": 2.0, "damage_dealt_players": 0.5,
                 "extinguish_fire": 0.3},
    "engineer": {"wire_connection": 2.0, "operate_reactor": 2.0, "craft_item": 1.5,
                 "deconstruct_item": 1.0, "repair_hull": 0.6, "repair_device": 0.6, "extinguish_fire": 0.5},
}

PENALTY_MAP = {
    "player_death":  {"captain": 50, "mechanic": 50, "medic": 50, "security": 30, "engineer": 50},
    "ship_loss":     {"captain": 200},
    "crew_death":    {"medic": 100},
    "friendly_fire": {"security": 150},
}

def compute_rating(cumulative_points: float) -> float:
    return K * math.log(1 + cumulative_points / S)

def process_logout_event(event: dict):
    payload = event["payload"]
    steam_id = payload["player_steamid"]
    role_actions = payload["role_actions"]
    negative_events = payload.get("negative_events", [])
    source_server = event["source"]
    trace_id = event["trace_id"]

    # Step 1: Group actions by role
    actions_by_role = defaultdict(list)
    for action in role_actions:
        actions_by_role[action["role"]].append(action)

    # Step 2: For each role with actions, compute weighted sum and update
    for role, actions in actions_by_role.items():
        record = get_or_create_rating(steam_id, role)  # DB fetch with row lock

        # Step 2a: Apply lazy time decay before update
        apply_decay_if_needed(record)

        old_cumulative = record.cumulative_points
        old_rating = record.current_rating

        # Step 2b: Compute session weighted sum
        session_points = 0.0
        for action in actions:
            weight = WEIGHT_MATRIX.get(role, {}).get(action["action_type"], 0.0)
            session_points += action["value"] * weight

        # Step 2c: Update cumulative and recompute rating
        record.cumulative_points += session_points
        record.current_rating = compute_rating(record.cumulative_points)
        record.last_activity_date = datetime.now(timezone.utc)
        record.last_decay_applied_at = datetime.now(timezone.utc)

        # Step 2d: Log history
        insert_history(steam_id, role, "session_update", session_points,
                       old_cumulative, record.cumulative_points,
                       old_rating, record.current_rating, source_server, trace_id)

    # Step 3: Apply negative event penalties
    for neg in negative_events:
        event_type = neg["event_type"]
        event_role = neg["role"]
        count = neg["count"]

        penalty_per = PENALTY_MAP.get(event_type, {}).get(event_role, 0)
        if penalty_per == 0:
            continue

        total_penalty = penalty_per * count
        record = get_or_create_rating(steam_id, event_role)

        old_cumulative = record.cumulative_points
        old_rating = record.current_rating

        record.cumulative_points = max(0.0, record.cumulative_points - total_penalty)
        record.current_rating = compute_rating(record.cumulative_points)

        insert_history(steam_id, event_role, "penalty", -total_penalty,
                       old_cumulative, record.cumulative_points,
                       old_rating, record.current_rating, source_server, trace_id)

    # Step 4: Commit transaction
    commit()

def apply_decay_if_needed(record):
    """Apply time decay lazily before any update or read."""
    if record.last_activity_date is None:
        return

    days_inactive = (datetime.now(timezone.utc) - record.last_activity_date).days
    if days_inactive <= DECAY_THRESHOLD_DAYS:
        return

    # Calculate days to decay since last application
    if record.last_decay_applied_at:
        days_since_last_decay = (datetime.now(timezone.utc) - record.last_decay_applied_at).days
        if days_since_last_decay == 0:
            return
        decay_days = days_since_last_decay
    else:
        decay_days = days_inactive - DECAY_THRESHOLD_DAYS

    old_cumulative = record.cumulative_points
    record.cumulative_points = max(0.0, record.cumulative_points * (DECAY_RATE ** decay_days))
    record.current_rating = compute_rating(record.cumulative_points)
    record.last_decay_applied_at = datetime.now(timezone.utc)

    if old_cumulative != record.cumulative_points:
        insert_history(record.steam_id, record.role, "decay",
                       record.cumulative_points - old_cumulative,
                       old_cumulative, record.cumulative_points,
                       compute_rating(old_cumulative), record.current_rating, None, None)

def get_player_ratings(steam_id: str) -> dict:
    """GET /api/v1/ratings/{steam_id} handler"""
    roles = ["captain", "mechanic", "medic", "security", "engineer"]
    result = {}

    for role in roles:
        record = get_rating_or_none(steam_id, role)
        if record is None:
            result[role] = {"rating": 0.0, "cumulative_points": 0.0, "last_activity_date": None}
        else:
            apply_decay_if_needed(record)
            save_record(record)
            result[role] = {
                "rating": round(record.current_rating, 2),
                "cumulative_points": round(record.cumulative_points, 2),
                "last_activity_date": record.last_activity_date.isoformat()
                    if record.last_activity_date else None
            }

    return {"steam_id": steam_id, "ratings": result}
```

## Plugin Changes (C# — RoundManager_ND)

### New Event Detection Hooks

1. **player_death**: Hook into `Character.Kill` (already partially hooked). When the killed character belongs to the session player, record `player_death` with `Role = session.CurrentRole`.

2. **ship_loss**: Hook into round end / mission failure. When `Submarine.MainSub` hull integrity reaches 0 or mission fails due to hull breach, record `ship_loss` with `Role = "captain"` for the player whose `CurrentRole == "captain"`.

3. **crew_death**: Hook into `Character.Kill` for allied NPCs/players. When a non-enemy crew member dies, record `crew_death` with `Role = "medic"` for the player whose `CurrentRole == "medic"`.

4. **friendly_fire**: When `damage_dealt_players` is recorded and the target is on the same team, also record `friendly_fire` with `Role = "security"` for the attacker (if attacker's `CurrentRole == "security"`).

### Modified Logout Payload (version 2)

```csharp
// Build negative events list from session
var negativeEventsList = new List<object>();
if (session.NegativeEvents != null)
{
    foreach (var ne in session.NegativeEvents)
    {
        negativeEventsList.Add(new
        {
            event_type = ne.EventType,
            role = ne.Role,
            count = ne.Count
        });
    }
}

var logoutPayload = new
{
    type = "player_sessions.logout",
    version = 2,  // bumped from 1
    source = SERVER_NAME,
    trace_id = $"logout-{steamid64}-{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}",
    payload = new
    {
        player_steamid = steamid64,
        logout_time = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ"),
        play_time_this_session = sessionDuration,
        stats = new { /* existing stats unchanged */ },
        kills = kills,
        role_actions = roleActionsList,
        role_stats = roleStatsList,
        negative_events = negativeEventsList  // NEW
    }
};
```

### Helper Method for Tracking Negative Events

```csharp
private void TrackNegativeEvent(PlayerSessionData session, string eventType, string role)
{
    lock (session.SessionLock)
    {
        var existing = session.NegativeEvents
            .FirstOrDefault(e => e.EventType == eventType && e.Role == role);
        if (existing != null)
            existing.Count++;
        else
            session.NegativeEvents.Add(new NegativeEventEntry
            {
                EventType = eventType,
                Role = role,
                Count = 1
            });
    }
}
```

## Testing Strategy

### Unit Tests (Example-Based)
- Verify specific penalty mappings: ship_loss → captain, crew_death → medic, friendly_fire → security
- Verify weight matrix completeness: all 13 actions × 5 roles have defined weights
- Verify all 4 negative event types are recognized

### Property-Based Tests
- Weight matrix application correctness
- Logarithmic formula monotonicity and diminishing returns
- Rating non-negativity invariant
- Penalty application correctness
- Time decay correctness
- Role independence
- Rating storage round-trip
- Unknown action default weight
- Negative events payload completeness
- Multi-server unification

### Integration Tests
- End-to-end: plugin sends logout → backend computes rating → API returns correct value
- Backward compatibility: v1 payloads (no negative_events) still process correctly

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Weight Matrix Application

*For any* action with a given (role, action_type, value) triple, the weighted contribution SHALL equal `value * WEIGHT_MATRIX[role][action_type]`, and for any primary role-action pair, the weight SHALL be strictly greater than the weight for the same action performed by any non-primary role.

**Validates: Requirements 1.2, 2.3**

### Property 2: Logarithmic Scale — Monotonicity and Diminishing Returns

*For any* two cumulative point values `a` and `b` where `0 <= a < b`, `compute_rating(a) < compute_rating(b)` (monotonically increasing), and for any equal increment `d > 0`, `compute_rating(a + d) - compute_rating(a) > compute_rating(b + d) - compute_rating(b)` (diminishing marginal returns). Furthermore, for any arbitrarily large value of cumulative points, the rating SHALL continue to increase without bound.

**Validates: Requirements 1.3, 5.1, 5.2, 5.3**

### Property 3: Rating Non-Negativity Invariant

*For any* sequence of operations (session updates, penalties, time decay) applied to a player's role rating, the resulting `cumulative_points` and `current_rating` SHALL always be greater than or equal to zero.

**Validates: Requirements 3.7, 4.4**

### Property 4: Negative Event Penalty Correctness

*For any* negative event with a known (event_type, role, count) triple, the penalty applied SHALL equal `PENALTY_MAP[event_type][role] * count` subtracted from `cumulative_points`, and the rating SHALL be recomputed from the new cumulative points using the logarithmic formula. The penalty SHALL only affect the specified role.

**Validates: Requirements 3.1, 3.3, 3.4, 3.5, 3.6**

### Property 5: Time Decay Correctness

*For any* player role with `days_inactive > DECAY_THRESHOLD_DAYS`, the decayed cumulative points SHALL equal `cumulative_points * DECAY_RATE ^ days_since_last_decay`. *For any* player role with `days_inactive <= DECAY_THRESHOLD_DAYS`, no decay SHALL be applied and cumulative points SHALL remain unchanged.

**Validates: Requirements 4.1, 4.2, 4.3**

### Property 6: Role Independence

*For any* logout payload that updates role X, the `cumulative_points` and `current_rating` of all other roles (Y ≠ X) SHALL remain unchanged after processing. Similarly, time decay applied to role X SHALL not affect any other role's data.

**Validates: Requirements 1.5, 4.2**

### Property 7: Rating Storage Round-Trip

*For any* player and role, after a rating update is persisted, a subsequent retrieval via `GET /api/v1/ratings/{steam_id}` SHALL return the correct `current_rating`, `cumulative_points`, and `last_activity_date` for that role. If no data exists for a role, the retrieval SHALL return rating 0.0 with null last_activity_date.

**Validates: Requirements 1.4, 8.1, 8.2, 8.3**

### Property 8: Unknown Action Default Weight

*For any* action_type string that is not present in the Weight_Matrix for a given role, the effective weight SHALL be 0.0, resulting in zero contribution to cumulative points regardless of the action's value.

**Validates: Requirements 2.4**

### Property 9: Negative Events Payload Completeness

*For any* set of negative events tracked during a plugin session, the logout payload's `negative_events` array SHALL contain an entry for each distinct (event_type, role) pair with the correct accumulated `count`.

**Validates: Requirements 7.5, 7.6**

### Property 10: Multi-Server Rating Unification

*For any* two logout payloads from different source servers (ASPER1, ASPER2) for the same SteamID64 and role, both SHALL update the same `player_ratings` record, and the final `cumulative_points` SHALL reflect the combined contributions from both sessions.

**Validates: Requirements 6.1, 6.2**
