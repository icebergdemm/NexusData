-- Migration: Add server moderation tables (MySQL / MariaDB)
-- server_online_players, anticheat_events, server_action_queue

CREATE TABLE IF NOT EXISTS `server_online_players` (
    `id`             INT          NOT NULL AUTO_INCREMENT,
    `player_steamid` VARCHAR(50)  NOT NULL,
    `player_name`    VARCHAR(255) NOT NULL,
    `character_name` VARCHAR(255) DEFAULT NULL,
    `ping`           INT          DEFAULT NULL,
    `join_time`      DATETIME     DEFAULT NULL,
    `updated_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_server_online_players_steamid` (`player_steamid`),
    KEY `ix_server_online_players_steamid` (`player_steamid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS `anticheat_events` (
    `id`             INT          NOT NULL AUTO_INCREMENT,
    `player_steamid` VARCHAR(50)  NOT NULL,
    `player_name`    VARCHAR(255) DEFAULT NULL,
    `player_id`      INT          DEFAULT NULL,
    `event_type`     VARCHAR(100) NOT NULL,
    `details`        JSON         DEFAULT NULL,
    `occurred_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `reviewed`       TINYINT      NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    KEY `ix_anticheat_events_steamid`   (`player_steamid`),
    KEY `ix_anticheat_events_event_type`(`event_type`),
    KEY `ix_anticheat_events_player_id` (`player_id`),
    CONSTRAINT `fk_ac_player_id` FOREIGN KEY (`player_id`) REFERENCES `players_data` (`player_id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS `server_action_queue` (
    `id`               INT           NOT NULL AUTO_INCREMENT,
    `action_type`      VARCHAR(20)   NOT NULL COMMENT 'kick | ban | message',
    `target_steamid`   VARCHAR(50)   NOT NULL,
    `reason`           VARCHAR(500)  DEFAULT NULL,
    `duration_minutes` INT           DEFAULT NULL,
    `message_text`     VARCHAR(1000) DEFAULT NULL,
    `admin_id`         INT           DEFAULT NULL,
    `created_at`       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `executed_at`      DATETIME      DEFAULT NULL,
    `status`           VARCHAR(20)   NOT NULL DEFAULT 'pending' COMMENT 'pending | executed | failed',
    `error_message`    VARCHAR(500)  DEFAULT NULL,
    PRIMARY KEY (`id`),
    KEY `ix_server_action_queue_steamid`(`target_steamid`),
    KEY `ix_server_action_queue_status` (`status`),
    CONSTRAINT `fk_saq_admin_id` FOREIGN KEY (`admin_id`) REFERENCES `admin_users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
