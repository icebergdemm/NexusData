-- Migration 004: Server Privilege Groups
-- Run in phpMyAdmin on your nexusdata database

-- Table: server_privilege_groups
CREATE TABLE IF NOT EXISTS `server_privilege_groups` (
    `id`                INT          NOT NULL AUTO_INCREMENT,
    `name`              VARCHAR(100) NOT NULL,
    `description`       VARCHAR(500) DEFAULT NULL,
    `group_key`         VARCHAR(64)  NOT NULL COMMENT 'Буквенный ID (e.g. moderator, event_manager)',
    `commands`          JSON         NOT NULL DEFAULT (JSON_ARRAY()),
    `allowed_action_ids` JSON        NOT NULL DEFAULT (JSON_ARRAY()),
    `created_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_spg_name` (`name`),
    UNIQUE KEY `uq_spg_key` (`group_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add server_group_id FK to server_player_privileges (if column does not exist)
ALTER TABLE `server_player_privileges`
    ADD COLUMN IF NOT EXISTS `server_group_id` INT DEFAULT NULL
        COMMENT 'FK to server_privilege_groups',
    ADD CONSTRAINT `fk_spp_server_group`
        FOREIGN KEY (`server_group_id`)
        REFERENCES `server_privilege_groups` (`id`)
        ON DELETE SET NULL;

-- Seed default groups (optional, matches privilege_groups.json)
INSERT IGNORE INTO `server_privilege_groups` (`name`, `group_key`, `description`, `commands`, `allowed_action_ids`) VALUES
('Default',       'default',       'Базовый набор (пустой)',                          '[]',              '[]'),
('Moderator',     'moderator',     'Набор для модераторов: мут, кик',                 '["/mute","/kick"]','[100, 101]'),
('Event Manager', 'event_manager', 'Разрешения для управления мероприятиями',          '["/teleport","/giveitem"]','[200, 201, 202]');
