-- Migration 012: Analytics - online snapshots and round statistics

-- ═══════════════════════════════════════════════════════════════════════════
-- ONLINE SNAPSHOTS (recorded every time the game server sends player list)
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS `online_snapshots` (
    `id`            BIGINT       NOT NULL AUTO_INCREMENT,
    `server_name`   VARCHAR(50)  NOT NULL COMMENT 'ASPER1 | ASPER2',
    `player_count`  INT          NOT NULL DEFAULT 0,
    `player_ids`    JSON         DEFAULT NULL COMMENT 'Array of player steamids in this snapshot',
    `recorded_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    KEY `ix_os_server_time` (`server_name`, `recorded_at`),
    KEY `ix_os_time` (`recorded_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ═══════════════════════════════════════════════════════════════════════════
-- Indexes for analytics queries on existing tables
-- ═══════════════════════════════════════════════════════════════════════════

-- Round events: faster aggregation by submarine, mission
CREATE INDEX IF NOT EXISTS `ix_re_sub_name` ON `round_events` (`submarine_name`);
CREATE INDEX IF NOT EXISTS `ix_re_event_time` ON `round_events` (`event_type`, `created_at`);

-- Player role stats: faster role popularity queries
CREATE INDEX IF NOT EXISTS `ix_prs_role_rounds` ON `player_role_stats` (`role`, `rounds_played`);
