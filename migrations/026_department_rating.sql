-- Migration 026: Department internal rating ledger (item 7).
-- NOTE: SQLAlchemy's Base.metadata.create_all() already creates this table on
-- startup from models.py; this file mirrors the schema for manual application
-- / historical record.

CREATE TABLE IF NOT EXISTS `department_rating_entries` (
    `id`                    INT          NOT NULL AUTO_INCREMENT,
    `department_id`         INT          NOT NULL,
    `admin_id`               INT          NOT NULL,
    `delta`                  INT          NOT NULL,
    `reason`                 VARCHAR(500) DEFAULT NULL,
    `category`               VARCHAR(100) DEFAULT NULL,
    `created_by_admin_id`    INT          DEFAULT NULL,
    `created_at`             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    KEY `ix_department_rating_entries_department_id` (`department_id`),
    KEY `ix_department_rating_entries_admin_id` (`admin_id`),
    CONSTRAINT `fk_dept_rating_department` FOREIGN KEY (`department_id`) REFERENCES `departments` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_dept_rating_admin` FOREIGN KEY (`admin_id`) REFERENCES `admin_users` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_dept_rating_created_by` FOREIGN KEY (`created_by_admin_id`) REFERENCES `admin_users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
