-- Migration 016: Live per-server state (submarine health, supply shuttle ETA)

CREATE TABLE IF NOT EXISTS `server_live_state` (
    `id`                          INT          NOT NULL AUTO_INCREMENT,
    `server_name`                 VARCHAR(50)  NOT NULL,
    `submarine_health`            INT          DEFAULT NULL COMMENT '0-100',
    `supply_shuttle_eta_seconds`  INT          DEFAULT NULL COMMENT 'as last reported',
    `shuttle_arrival_at`          DATETIME     DEFAULT NULL COMMENT 'computed absolute arrival time',
    `updated_at`                  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    UNIQUE KEY `ux_sls_server_name` (`server_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
