-- Migration 015: Personal admin notifications

CREATE TABLE IF NOT EXISTS `admin_notifications` (
    `id`            BIGINT       NOT NULL AUTO_INCREMENT,
    `admin_id`      INT          NOT NULL,
    `type`          VARCHAR(50)  NOT NULL COMMENT 'info | success | warning | error',
    `category`      VARCHAR(50)  NOT NULL DEFAULT 'general'
        COMMENT 'general | punishment | server_action | rating | system',
    `title`         VARCHAR(255) NOT NULL,
    `body`          VARCHAR(1000) DEFAULT NULL,
    `link`          VARCHAR(500)  DEFAULT NULL COMMENT 'Optional navigation URL',
    `icon`          VARCHAR(16)   DEFAULT NULL COMMENT 'Emoji icon',
    `is_read`       TINYINT      NOT NULL DEFAULT 0,
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `expires_at`    DATETIME     DEFAULT NULL COMMENT 'Auto-expire old notifications',

    PRIMARY KEY (`id`),
    KEY `ix_an_admin_read` (`admin_id`, `is_read`),
    KEY `ix_an_admin_created` (`admin_id`, `created_at`),
    KEY `ix_an_expires` (`expires_at`),
    CONSTRAINT `fk_an_admin` FOREIGN KEY (`admin_id`) REFERENCES `admin_users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
