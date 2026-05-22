# Implementation Plan: Role-Based Rating System

## Overview

Implement a per-role rating system spanning two components: (1) extend the C# game plugin (RoundManager_ND) to track and report negative events in the logout payload, and (2) build the Python/FastAPI rating engine in NexusData that processes logout events, computes logarithmic ratings with weight matrix, penalties, and time decay, and serves a retrieval API. Tasks are ordered so foundational models and configuration come first, followed by core logic, plugin changes, API wiring, and integration.

## Tasks

- [ ] 1. Set up backend rating engine module structure and data models
  - [ ] 1.1 Create database migration for `player_ratings` and `rating_history` tables
    - Create SQL migration file with `player_ratings` table (id, steam_id, role, cumulative_points, current_rating, last_activity_date, last_decay_applied_at, created_at, updated_at) with UNIQUE constraint on (steam_id, role)
    - Create `rating_history` table (id, steam_id, role, event_type, points_delta, cumulative_points_before, cumulative_points_after, rating_before, rating_after, source_server, session_trace_id, created_at)
    - Add indexes on steam_id and created_at
    - _Requirements: 1.4, 6.2_

  - [ ] 1.2 Create Pydantic data models and constants module
    - Create `rating_models.py` with `PlayerRating`, `RatingHistoryEntry`, `RoleRatingResponse`, `RatingResponse` Pydantic models
    - Create `rating_constants.py` with `K=10.0`, `S=100.0`, `DECAY_THRESHOLD_DAYS=14`, `DECAY_RATE=0.98`, full `WEIGHT_MATRIX` dict, and `PENALTY_MAP` dict
    - Define `ROLES` list: `["captain", "mechanic", "medic", "security", "engineer"]`
    - _Requirements: 1.1, 2.1, 2.2, 3.2_

  - [ ]* 1.3 Write property test for weight matrix completeness
    - **Property 1: Weight Matrix Application**
    - Verify all 13 action types × 5 roles have defined weights (explicit or default 0)
    - Verify primary role weights are strictly greater than non-primary role weights for the same action
    - **Validates: Requirements 2.1, 2.3**

- [ ] 2. Implement core rating computation logic
  - [ ] 2.1 Implement logarithmic rating formula and weighted sum computation
    - Create `rating_engine.py` with `compute_rating(cumulative_points: float) -> float` using `K * ln(1 + P / S)`
    - Implement `compute_session_weighted_sum(role: str, actions: list[dict]) -> float` that applies WEIGHT_MATRIX to each action
    - Unknown action types default to weight 0.0
    - _Requirements: 1.2, 1.3, 2.3, 2.4, 5.1, 5.2, 5.3_

  - [ ]* 2.2 Write property tests for logarithmic formula
    - **Property 2: Logarithmic Scale — Monotonicity and Diminishing Returns**
    - For any `0 <= a < b`, verify `compute_rating(a) < compute_rating(b)`
    - For any equal increment `d > 0`, verify marginal gain decreases as base increases
    - Verify rating grows without bound for arbitrarily large inputs
    - **Validates: Requirements 1.3, 5.1, 5.2, 5.3**

  - [ ]* 2.3 Write property test for unknown action default weight
    - **Property 8: Unknown Action Default Weight**
    - For any random action_type string not in WEIGHT_MATRIX, verify effective weight is 0.0
    - **Validates: Requirements 2.4**

  - [ ] 2.4 Implement negative event penalty application
    - Create `apply_penalties(steam_id: str, negative_events: list[dict], source_server: str, trace_id: str)` function
    - Look up PENALTY_MAP for (event_type, role) → penalty_per_occurrence
    - Subtract `penalty_per * count` from cumulative_points, clamp to 0.0
    - Recompute rating from new cumulative_points
    - Insert history record with event_type "penalty"
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [ ]* 2.5 Write property test for penalty correctness
    - **Property 4: Negative Event Penalty Correctness**
    - For any (event_type, role, count), verify penalty equals `PENALTY_MAP[event_type][role] * count`
    - Verify cumulative_points never goes below 0
    - Verify penalty only affects the specified role
    - **Validates: Requirements 3.1, 3.3, 3.4, 3.5, 3.6**

  - [ ] 2.6 Implement time decay logic
    - Create `apply_decay_if_needed(record: PlayerRating)` function
    - If `days_inactive <= DECAY_THRESHOLD_DAYS`, do nothing
    - Otherwise compute `cumulative_points * DECAY_RATE ^ days_since_last_decay`
    - Clamp to 0.0, recompute rating, update `last_decay_applied_at`
    - Insert history record with event_type "decay" if points changed
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [ ]* 2.7 Write property test for time decay
    - **Property 5: Time Decay Correctness**
    - For any player with `days_inactive > DECAY_THRESHOLD_DAYS`, verify decayed points = `cumulative_points * 0.98 ^ days`
    - For any player within threshold, verify no decay applied
    - Verify cumulative_points never goes below 0
    - **Validates: Requirements 4.1, 4.2, 4.3**

  - [ ]* 2.8 Write property test for rating non-negativity
    - **Property 3: Rating Non-Negativity Invariant**
    - For any sequence of session updates, penalties, and decay operations, verify cumulative_points >= 0 and current_rating >= 0
    - **Validates: Requirements 3.7, 4.4**

- [ ] 3. Checkpoint - Core rating logic
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 4. Implement logout event handler and database operations
  - [ ] 4.1 Implement database access layer for player_ratings
    - Create `rating_repository.py` with `get_or_create_rating(steam_id, role)` using SELECT FOR UPDATE for row-level locking
    - Implement `save_rating(record)` to persist updated cumulative_points, current_rating, last_activity_date, last_decay_applied_at
    - Implement `insert_history(...)` to write to rating_history table
    - Implement `get_ratings_for_player(steam_id)` to fetch all 5 role records
    - _Requirements: 1.4, 6.1, 6.2_

  - [ ] 4.2 Implement `handle_logout_event` orchestrator function
    - Create `async def handle_logout_event(event: dict) -> None` in `rating_engine.py`
    - Group role_actions by role
    - For each role: apply lazy decay, compute session weighted sum, update cumulative_points, recompute rating, set last_activity_date, insert history
    - Apply negative event penalties (handle missing `negative_events` field for backward compatibility with v1 payloads)
    - Wrap in database transaction with retry (up to 3 retries, 500ms delay)
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 3.1, 6.1_

  - [ ]* 4.3 Write property test for role independence
    - **Property 6: Role Independence**
    - For any logout payload updating role X, verify all other roles' cumulative_points and current_rating remain unchanged
    - **Validates: Requirements 1.5, 4.2**

  - [ ]* 4.4 Write property test for multi-server unification
    - **Property 10: Multi-Server Rating Unification**
    - For two payloads from different servers (ASPER1, ASPER2) for same steam_id and role, verify both update the same record and final cumulative_points reflects combined contributions
    - **Validates: Requirements 6.1, 6.2**

- [ ] 5. Implement rating retrieval API endpoint
  - [ ] 5.1 Create GET /api/v1/ratings/{steam_id} endpoint
    - Create `rating_router.py` with FastAPI router
    - Implement `get_ratings(steam_id: str) -> RatingResponse`
    - Apply lazy time decay on read for each role
    - Return all 5 roles with rating, cumulative_points, last_activity_date (0.0 and null for roles with no data)
    - Validate steam_id format, return 400 for invalid
    - _Requirements: 8.1, 8.2, 8.3_

  - [ ]* 5.2 Write property test for rating storage round-trip
    - **Property 7: Rating Storage Round-Trip**
    - After a rating update is persisted, verify GET endpoint returns correct current_rating, cumulative_points, and last_activity_date
    - For roles with no data, verify rating is 0.0 with null last_activity_date
    - **Validates: Requirements 1.4, 8.1, 8.2, 8.3**

- [ ] 6. Checkpoint - Backend complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Extend C# game plugin for negative event tracking
  - [ ] 7.1 Add NegativeEventEntry model and session tracking fields
    - Add `NegativeEventEntry` class with `EventType`, `Role`, `Count` properties to RoundManager_ND.cs
    - Add `List<NegativeEventEntry> NegativeEvents` property to `PlayerSessionData`
    - Add `TrackNegativeEvent(PlayerSessionData session, string eventType, string role)` helper method with thread-safe locking
    - _Requirements: 7.6_

  - [ ] 7.2 Implement player_death event detection
    - Hook into character death event (Character.Kill or equivalent death callback)
    - When the killed character belongs to the session player, call `TrackNegativeEvent(session, "player_death", session.CurrentRole)`
    - _Requirements: 7.1_

  - [ ] 7.3 Implement ship_loss event detection
    - Hook into round end / mission failure / submarine destruction event
    - When submarine is lost, find the player with `CurrentRole == "captain"` and call `TrackNegativeEvent(session, "ship_loss", "captain")`
    - _Requirements: 7.2_

  - [ ] 7.4 Implement crew_death event detection
    - Hook into allied NPC/player death events
    - When a non-enemy crew member dies, find the player with `CurrentRole == "medic"` and call `TrackNegativeEvent(session, "crew_death", "medic")`
    - _Requirements: 7.3_

  - [ ] 7.5 Implement friendly_fire event detection
    - When `damage_dealt_players` is recorded and target is on the same team, check if attacker's `CurrentRole == "security"`
    - If so, call `TrackNegativeEvent(session, "friendly_fire", "security")`
    - _Requirements: 7.4_

  - [ ] 7.6 Modify SendLogoutEventAsync to include negative_events in payload
    - Bump payload version from 1 to 2
    - Build `negative_events` array from `session.NegativeEvents`
    - Include in logout payload alongside existing role_actions and role_stats
    - _Requirements: 7.5, 7.6_

  - [ ]* 7.7 Write property test for negative events payload completeness
    - **Property 9: Negative Events Payload Completeness**
    - For any set of tracked negative events during a session, verify the logout payload's negative_events array contains an entry for each distinct (event_type, role) pair with correct accumulated count
    - **Validates: Requirements 7.5, 7.6**

- [ ] 8. Wire event dispatcher to rating engine
  - [ ] 8.1 Register rating engine handler in existing /api/v1/events dispatcher
    - In the existing event dispatcher, add routing for `type == "player_sessions.logout"` to call `handle_logout_event`
    - Ensure backward compatibility: v1 payloads (without negative_events) are handled gracefully
    - Register the rating router in the FastAPI app
    - _Requirements: 1.1, 6.1_

- [ ] 9. Final checkpoint - Full integration
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The C# plugin tasks (7.x) and Python backend tasks (1.x–5.x) can be developed in parallel
- Backend tasks assume NexusData project structure already exists with FastAPI and PostgreSQL configured

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1", "7.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "2.6", "7.2", "7.3", "7.4", "7.5"] },
    { "id": 3, "tasks": ["2.5", "2.7", "2.8", "7.6"] },
    { "id": 4, "tasks": ["4.1", "7.7"] },
    { "id": 5, "tasks": ["4.2"] },
    { "id": 6, "tasks": ["4.3", "4.4", "5.1"] },
    { "id": 7, "tasks": ["5.2", "8.1"] }
  ]
}
```
