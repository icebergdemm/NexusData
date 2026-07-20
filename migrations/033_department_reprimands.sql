-- Migration 033: department reprimands (item 12) — head-only disciplinary
-- notes on a department member.
-- NOTE: SQLAlchemy's _apply_migrations()/create_all() already applies this
-- on startup from models.py; this file mirrors the schema for manual
-- application / historical record.

CREATE TABLE IF NOT EXISTS `department_reprimands` (
    `id`                    INT           NOT NULL AUTO_INCREMENT,
    `department_id`         INT           NOT NULL,
    `admin_id`               INT           NOT NULL,
    `reason`                 VARCHAR(1000) NOT NULL,
    `issued_by_admin_id`     INT           DEFAULT NULL,
    `created_at`             DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    KEY `ix_dept_reprimands_department_id` (`department_id`),
    KEY `ix_dept_reprimands_admin_id` (`admin_id`),
    CONSTRAINT `fk_dept_reprimands_department` FOREIGN KEY (`department_id`) REFERENCES `departments` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_dept_reprimands_admin` FOREIGN KEY (`admin_id`) REFERENCES `admin_users` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_dept_reprimands_issued_by` FOREIGN KEY (`issued_by_admin_id`) REFERENCES `admin_users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
