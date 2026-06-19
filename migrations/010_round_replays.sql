-- Migration 010: Round replays (.barodemo) + app settings

CREATE TABLE IF NOT EXISTS `round_replays` (
    `id`              INT           NOT NULL AUTO_INCREMENT,
    `server_name`     VARCHAR(100)  DEFAULT NULL,
    `submarine_name`  VARCHAR(255)  DEFAULT NULL,
    `level_name`      VARCHAR(255)  DEFAULT NULL,
    `filename`        VARCHAR(500)  NOT NULL,
    `stored_filename` VARCHAR(500)  NOT NULL,
    `file_size`       INT           DEFAULT NULL,
    `duration_ms`     INT           DEFAULT NULL,
    `has_voice`       TINYINT       DEFAULT 0,
    `recorded_at`     DATETIME      DEFAULT NULL,
    `uploaded_at`     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `notes`           VARCHAR(1000) DEFAULT NULL,
    PRIMARY KEY (`id`),
    KEY `ix_rr_server`   (`server_name`),
    KEY `ix_rr_uploaded` (`uploaded_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `app_settings` (
    `id`         INT          NOT NULL AUTO_INCREMENT,
    `key`        VARCHAR(100) NOT NULL,
    `value`      VARCHAR(2000) DEFAULT NULL,
    `updated_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_app_settings_key` (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
