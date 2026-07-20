-- Migration 023: Department workspace system ("Рабочая зона" — Отделы).
-- NOTE: SQLAlchemy's Base.metadata.create_all() already creates these tables
-- on startup from models.py; this file mirrors the schema for manual
-- application / historical record, consistent with the rest of migrations/.

ALTER TABLE `admin_users`
    ADD COLUMN `created_by_worker` TINYINT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS `departments` (
    `id`                        INT          NOT NULL AUTO_INCREMENT,
    `name`                      VARCHAR(255) NOT NULL,
    `slug`                      VARCHAR(100) NOT NULL,
    `description`               VARCHAR(2000) DEFAULT NULL,
    `icon`                      VARCHAR(16)  DEFAULT NULL,
    `color`                     VARCHAR(20)  DEFAULT NULL,
    `base_discord_role_id`      VARCHAR(50)  DEFAULT NULL,
    `base_discord_role_name`    VARCHAR(255) DEFAULT NULL,
    `enabled_modules`           JSON         NOT NULL,
    `pterodactyl_server_uuid`   VARCHAR(100) DEFAULT NULL,
    `created_by_admin_id`       INT          DEFAULT NULL,
    `created_at`                DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`                DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `is_archived`                TINYINT     NOT NULL DEFAULT 0,

    PRIMARY KEY (`id`),
    UNIQUE KEY `ux_departments_slug` (`slug`),
    CONSTRAINT `fk_departments_admin` FOREIGN KEY (`created_by_admin_id`) REFERENCES `admin_users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `department_access_levels` (
    `id`               INT          NOT NULL AUTO_INCREMENT,
    `department_id`    INT          NOT NULL,
    `key`              VARCHAR(64)  NOT NULL,
    `name`             VARCHAR(255) NOT NULL,
    `discord_role_id`  VARCHAR(50)  DEFAULT NULL,
    `kind`             VARCHAR(20)  NOT NULL DEFAULT 'qualification',
    `permissions`      JSON         NOT NULL,
    `created_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    UNIQUE KEY `ux_dept_access_level_key` (`department_id`, `key`),
    CONSTRAINT `fk_dept_access_levels_department` FOREIGN KEY (`department_id`) REFERENCES `departments` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `department_members` (
    `id`                  INT      NOT NULL AUTO_INCREMENT,
    `department_id`       INT      NOT NULL,
    `admin_id`            INT      NOT NULL,
    `held_level_keys`     JSON     NOT NULL,
    `is_head`             TINYINT  NOT NULL DEFAULT 0,
    `is_auto`             TINYINT  NOT NULL DEFAULT 0,
    `added_by_admin_id`   INT      DEFAULT NULL,
    `added_at`            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    UNIQUE KEY `ux_dept_member` (`department_id`, `admin_id`),
    CONSTRAINT `fk_dept_members_department` FOREIGN KEY (`department_id`) REFERENCES `departments` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_dept_members_admin` FOREIGN KEY (`admin_id`) REFERENCES `admin_users` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_dept_members_added_by` FOREIGN KEY (`added_by_admin_id`) REFERENCES `admin_users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
