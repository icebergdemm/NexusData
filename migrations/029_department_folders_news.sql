-- Migration 029: Department document folders + per-folder ACLs (item 5),
-- and the mandatory "Новости" module (item 6).
-- NOTE: SQLAlchemy's _apply_migrations()/create_all() already applies these
-- on startup from models.py; this file mirrors the schema for manual
-- application / historical record.

CREATE TABLE IF NOT EXISTS `department_document_folders` (
    `id`                    INT          NOT NULL AUTO_INCREMENT,
    `department_id`          INT          NOT NULL,
    `name`                   VARCHAR(255) NOT NULL,
    `parent_id`              INT          DEFAULT NULL,
    `created_by_admin_id`    INT          DEFAULT NULL,
    `created_at`             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    KEY `ix_dept_doc_folders_department_id` (`department_id`),
    KEY `ix_dept_doc_folders_parent_id` (`parent_id`),
    CONSTRAINT `fk_dept_doc_folders_department` FOREIGN KEY (`department_id`) REFERENCES `departments` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_dept_doc_folders_parent` FOREIGN KEY (`parent_id`) REFERENCES `department_document_folders` (`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_dept_doc_folders_admin` FOREIGN KEY (`created_by_admin_id`) REFERENCES `admin_users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `department_folder_permissions` (
    `id`                 INT          NOT NULL AUTO_INCREMENT,
    `folder_id`           INT          NOT NULL,
    `grantee_type`        VARCHAR(20)  NOT NULL,
    `access_level_key`    VARCHAR(64)  DEFAULT NULL,
    `member_admin_id`     INT          DEFAULT NULL,
    `level`               VARCHAR(10)  NOT NULL DEFAULT 'view',
    `created_at`          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    KEY `ix_dept_folder_perms_folder_id` (`folder_id`),
    CONSTRAINT `fk_dept_folder_perms_folder` FOREIGN KEY (`folder_id`) REFERENCES `department_document_folders` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_dept_folder_perms_admin` FOREIGN KEY (`member_admin_id`) REFERENCES `admin_users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE `documents`
    ADD COLUMN `department_folder_id` INT NULL,
    ADD COLUMN `is_news` TINYINT NOT NULL DEFAULT 0,
    ADD CONSTRAINT `fk_documents_department_folder` FOREIGN KEY (`department_folder_id`) REFERENCES `department_document_folders` (`id`) ON DELETE SET NULL;

ALTER TABLE `departments`
    ADD COLUMN `news_channel_id` VARCHAR(50) NULL;
