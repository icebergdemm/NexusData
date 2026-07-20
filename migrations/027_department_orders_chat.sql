-- Migration 027: Department order-doc/-template flags + Discord orders channel
-- (item 8), and multi-channel department chat (item 6).
-- NOTE: SQLAlchemy's _apply_migrations()/create_all() already applies these
-- on startup from models.py; this file mirrors the schema for manual
-- application / historical record.

ALTER TABLE `departments`
    ADD COLUMN `orders_channel_id` VARCHAR(50) NULL;

ALTER TABLE `documents`
    ADD COLUMN `is_order` TINYINT NOT NULL DEFAULT 0;

ALTER TABLE `document_templates`
    ADD COLUMN `is_order_template` TINYINT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS `department_chat_channels` (
    `id`                 INT          NOT NULL AUTO_INCREMENT,
    `department_id`       INT          NOT NULL,
    `discord_channel_id`  VARCHAR(50)  NOT NULL,
    `label`               VARCHAR(255) DEFAULT NULL,
    `created_at`          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    KEY `ix_dept_chat_channels_department_id` (`department_id`),
    CONSTRAINT `fk_dept_chat_channels_department` FOREIGN KEY (`department_id`) REFERENCES `departments` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `department_chat_messages` (
    `id`                    INT          NOT NULL AUTO_INCREMENT,
    `channel_id`             INT          NOT NULL,
    `discord_message_id`     VARCHAR(50)  DEFAULT NULL,
    `author_name`            VARCHAR(255) DEFAULT NULL,
    `author_avatar_url`      VARCHAR(500) DEFAULT NULL,
    `content`                VARCHAR(4000) DEFAULT NULL,
    `sent_by_admin_id`       INT          DEFAULT NULL,
    `created_at`             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    KEY `ix_dept_chat_messages_channel_id` (`channel_id`),
    KEY `ix_dept_chat_messages_discord_message_id` (`discord_message_id`),
    KEY `ix_dept_chat_messages_created_at` (`created_at`),
    CONSTRAINT `fk_dept_chat_messages_channel` FOREIGN KEY (`channel_id`) REFERENCES `department_chat_channels` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_dept_chat_messages_admin` FOREIGN KEY (`sent_by_admin_id`) REFERENCES `admin_users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
