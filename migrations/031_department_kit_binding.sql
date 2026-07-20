-- Migration 031: Department access level <-> qual-kit binding (item 6) —
-- superuser binds a qual-kit to an access level; workers.task_sync_department_kits
-- grants/revokes it on players whose linked admin holds that level.
-- NOTE: SQLAlchemy's _apply_migrations()/create_all() already applies these
-- on startup from models.py; this file mirrors the schema for manual
-- application / historical record.

CREATE TABLE IF NOT EXISTS `department_kit_bindings` (
    `id`                    INT          NOT NULL AUTO_INCREMENT,
    `access_level_id`       INT          NOT NULL,
    `job_id`                VARCHAR(50)  NOT NULL,
    `kit_level`             INT          NOT NULL,
    `created_by_admin_id`   INT          DEFAULT NULL,
    `created_at`            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    UNIQUE KEY `ux_dept_kit_binding_level` (`access_level_id`),
    CONSTRAINT `fk_dept_kit_binding_level` FOREIGN KEY (`access_level_id`) REFERENCES `department_access_levels` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_dept_kit_binding_admin` FOREIGN KEY (`created_by_admin_id`) REFERENCES `admin_users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `department_kit_grants` (
    `id`            INT       NOT NULL AUTO_INCREMENT,
    `binding_id`    INT       NOT NULL,
    `player_id`     INT       NOT NULL,
    `granted_at`    DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    UNIQUE KEY `ux_dept_kit_grant` (`binding_id`, `player_id`),
    KEY `ix_dept_kit_grants_player_id` (`player_id`),
    CONSTRAINT `fk_dept_kit_grant_binding` FOREIGN KEY (`binding_id`) REFERENCES `department_kit_bindings` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_dept_kit_grant_player` FOREIGN KEY (`player_id`) REFERENCES `players_data` (`player_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
