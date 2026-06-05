-- Migration 006: New server actions table (v2)
-- One record per action, two execution slots (one per server)

CREATE TABLE IF NOT EXISTS `server_actions` (
    `id`               INT           NOT NULL AUTO_INCREMENT,
    `action_type`      VARCHAR(32)   NOT NULL COMMENT 'kick | ban | unban | message | trap | dispatch',
    `target_steamid`   VARCHAR(50)   NOT NULL,
    `reason`           VARCHAR(500)  DEFAULT NULL,
    `duration_minutes` INT           DEFAULT NULL,
    `message_text`     VARCHAR(2000) DEFAULT NULL,
    `target_server`    VARCHAR(20)   DEFAULT NULL COMMENT 'ASPER1 | ASPER2 | NULL=both',
    `queued_by_admin_id` INT         DEFAULT NULL,
    `created_at`       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Per-server execution status: 0=pending, 1=done, 2=failed
    `status_asper1`    TINYINT       NOT NULL DEFAULT 0,
    `status_asper2`    TINYINT       NOT NULL DEFAULT 0,
    `error_asper1`     VARCHAR(500)  DEFAULT NULL,
    `error_asper2`     VARCHAR(500)  DEFAULT NULL,
    `executed_at_asper1` DATETIME    DEFAULT NULL,
    `executed_at_asper2` DATETIME    DEFAULT NULL,

    PRIMARY KEY (`id`),
    KEY `ix_sa_target`    (`target_steamid`),
    KEY `ix_sa_status1`   (`status_asper1`),
    KEY `ix_sa_status2`   (`status_asper2`),
    KEY `ix_sa_type`      (`action_type`),
    CONSTRAINT `fk_sa_admin` FOREIGN KEY (`queued_by_admin_id`) REFERENCES `admin_users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
