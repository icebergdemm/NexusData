-- Migration 022: Reusable document templates (starting-point content for new
-- documents), managed by any admin with documents/edit access.
-- NOTE: SQLAlchemy's Base.metadata.create_all() already creates this table on
-- startup from models.py; this file mirrors the schema for manual application
-- / historical record, consistent with the rest of migrations/.

CREATE TABLE IF NOT EXISTS `document_templates` (
    `id`                     INT          NOT NULL AUTO_INCREMENT,
    `name`                   VARCHAR(255) NOT NULL,
    `description`            VARCHAR(500) DEFAULT NULL,
    `icon`                   VARCHAR(16)  DEFAULT NULL,
    `content`                JSON         DEFAULT NULL,
    `created_by_admin_id`    INT          DEFAULT NULL,
    `created_at`             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    CONSTRAINT `fk_document_templates_admin` FOREIGN KEY (`created_by_admin_id`) REFERENCES `admin_users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
