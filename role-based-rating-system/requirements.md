# Requirements Document

## Introduction

Role-based rating system for the Barotrauma game servers (ASPER1, ASPER2). The NexusData backend (Python/FastAPI) calculates a per-role rating for each player based on raw `role_actions` and negative events received from the game server plugin at player logout. The rating uses a logarithmic scale with time-decay and penalties for negative events. Each role (captain, mechanic, medic, security, engineer) has an independent rating. The rating is global across all game servers.

## Glossary

- **Rating_Engine**: The NexusData backend component that receives event payloads and computes per-role ratings for players.
- **Game_Plugin**: The C# Barotrauma server plugin (RoundManager_ND) that tracks player actions during a session and sends them to NexusData at logout.
- **Role_Rating**: A numeric score representing a player's proficiency in a specific role, computed using weighted actions, penalties, and time-decay on a logarithmic scale.
- **Weight_Matrix**: A configuration mapping each action type to a numeric weight that varies by the role performing the action.
- **Time_Decay**: A mechanism that reduces a player's Role_Rating when the player has not played that role for a configurable number of days.
- **Negative_Event**: An in-game occurrence (death, ship loss, crew death, friendly fire) that reduces the player's Role_Rating.
- **Logout_Payload**: The JSON event sent via `POST /api/v1/events` with type `player_sessions.logout` containing role_actions, role_stats, and negative events for the session.
- **Logarithmic_Scale**: A rating progression where each subsequent point requires exponentially more effort, implemented as a logarithmic function with no hard cap.
- **Role**: One of five player job categories: captain, mechanic, medic, security, engineer.

## Requirements

### Requirement 1: Rating Calculation on Logout

**User Story:** As a server administrator, I want the backend to calculate player ratings from raw session data, so that the game plugin remains simple and rating logic is centralized.

#### Acceptance Criteria

1. WHEN the Rating_Engine receives a Logout_Payload containing role_actions, THE Rating_Engine SHALL compute an updated Role_Rating for each role that has at least one action in the payload.
2. THE Rating_Engine SHALL apply the Weight_Matrix to multiply each action's value by the weight corresponding to the action type and the role that performed the action.
3. WHEN the Rating_Engine computes a Role_Rating update, THE Rating_Engine SHALL apply a logarithmic function to the weighted action sum so that each subsequent rating point requires more cumulative effort than the previous one.
4. THE Rating_Engine SHALL store the updated Role_Rating persistently, associated with the player's SteamID64 and the specific role.
5. THE Rating_Engine SHALL treat each role's rating independently, without cross-role correlation.

### Requirement 2: Weight Matrix Configuration

**User Story:** As a server administrator, I want action weights to differ by role, so that role-relevant actions contribute more to that role's rating.

#### Acceptance Criteria

1. THE Rating_Engine SHALL maintain a Weight_Matrix that assigns a numeric weight to each combination of action type and role.
2. THE Rating_Engine SHALL support the following action types in the Weight_Matrix: heal_player, revive_player, repair_hull, repair_device, extinguish_fire, craft_item, deconstruct_item, wire_connection, pilot_submarine, operate_reactor, shoot_weapon, damage_dealt_monsters, damage_dealt_players.
3. WHEN an action type is performed by the role for which the action is primary (e.g., heal_player for medic), THE Rating_Engine SHALL apply a higher weight than when the same action is performed by a non-primary role.
4. IF an action type is not present in the Weight_Matrix for a given role, THEN THE Rating_Engine SHALL apply a default minimum weight of zero to that action.

### Requirement 3: Negative Event Penalties

**User Story:** As a server administrator, I want negative in-game events to reduce a player's rating, so that poor performance or harmful actions are reflected in the score.

#### Acceptance Criteria

1. WHEN the Rating_Engine receives a Logout_Payload containing negative events, THE Rating_Engine SHALL subtract a penalty value from the corresponding Role_Rating.
2. THE Rating_Engine SHALL recognize the following negative event types: player_death, ship_loss, crew_death, friendly_fire.
3. WHEN a player_death event is received, THE Rating_Engine SHALL apply a penalty to the Role_Rating of the role the player was playing at the time of death.
4. WHEN a ship_loss event is received, THE Rating_Engine SHALL apply a penalty to the captain Role_Rating of the responsible player.
5. WHEN a crew_death event is received, THE Rating_Engine SHALL apply a penalty to the medic Role_Rating of the responsible player.
6. WHEN a friendly_fire event is received, THE Rating_Engine SHALL apply a penalty to the security Role_Rating of the responsible player.
7. THE Rating_Engine SHALL ensure that a Role_Rating does not decrease below zero after penalty application.

### Requirement 4: Time Decay

**User Story:** As a server administrator, I want inactive players' ratings to decay over time, so that ratings reflect current skill rather than historical activity.

#### Acceptance Criteria

1. WHILE a player has not logged in with a specific role for a configurable number of days (decay threshold), THE Rating_Engine SHALL reduce that role's Role_Rating by a configurable decay factor.
2. THE Rating_Engine SHALL apply Time_Decay independently per role, based on the last session date for that specific role.
3. THE Rating_Engine SHALL not apply Time_Decay to a role if the player has played that role within the decay threshold period.
4. THE Rating_Engine SHALL ensure that Time_Decay does not reduce a Role_Rating below zero.

### Requirement 5: Logarithmic Scale Without Hard Cap

**User Story:** As a server administrator, I want rating growth to slow logarithmically without a ceiling, so that high-rated players must demonstrate sustained effort while still being able to progress.

#### Acceptance Criteria

1. THE Rating_Engine SHALL use a logarithmic function to convert cumulative weighted action points into a Role_Rating value.
2. THE Rating_Engine SHALL not impose a hard maximum cap on the Role_Rating value.
3. WHEN a player accumulates additional weighted action points, THE Rating_Engine SHALL increase the Role_Rating by a diminishing amount proportional to the logarithm of total accumulated points.

### Requirement 6: Multi-Server Global Rating

**User Story:** As a player, I want my rating to be the same regardless of which server I play on, so that my progress is not fragmented.

#### Acceptance Criteria

1. WHEN the Rating_Engine receives a Logout_Payload from any game server (ASPER1 or ASPER2), THE Rating_Engine SHALL update the same global Role_Rating record for the player.
2. THE Rating_Engine SHALL identify players by SteamID64 to ensure rating data is unified across servers.

### Requirement 7: Plugin Negative Event Tracking

**User Story:** As a server administrator, I want the game plugin to track and report negative events, so that the backend has the data needed to apply penalties.

#### Acceptance Criteria

1. THE Game_Plugin SHALL track player_death events (when the player's character dies) during a session.
2. THE Game_Plugin SHALL track ship_loss events (when the submarine is destroyed or the mission fails due to hull breach) and associate the event with the captain.
3. THE Game_Plugin SHALL track crew_death events (when a crew member dies) and associate the event with the medic on duty.
4. THE Game_Plugin SHALL track friendly_fire events (when a player deals damage to another player) and associate the event with the security role player who dealt the damage.
5. WHEN a player logs out, THE Game_Plugin SHALL include all tracked negative events for that session in the Logout_Payload sent to `POST /api/v1/events`.
6. THE Game_Plugin SHALL send negative events in a structured array field named `negative_events` within the Logout_Payload, with each entry containing the event type, the associated role, and a count.

### Requirement 8: Rating Retrieval API

**User Story:** As a frontend developer, I want an API endpoint to retrieve a player's ratings, so that the admin panel and player profiles can display rating information.

#### Acceptance Criteria

1. WHEN a GET request is made to the rating retrieval endpoint with a player's SteamID64, THE Rating_Engine SHALL return the current Role_Rating for each of the player's five roles.
2. THE Rating_Engine SHALL include the last activity date for each role in the rating response.
3. IF a player has no rating data for a role, THEN THE Rating_Engine SHALL return a Role_Rating of zero for that role.
