-- Migration 017: Shared document editor (Google-Docs-like) — folders, documents,
-- per-document sharing permissions, version history, inline comments, favorites.
-- NOTE: SQLAlchemy's Base.metadata.create_all() already creates these tables on
-- startup from models.py; this file mirrors the schema for manual application /
-- historical record, consistent with the rest of migrations/.

CREATE TABLE IF NOT EXISTS `document_folders` (
    `id`              INT          NOT NULL AUTO_INCREMENT,
    `name`            VARCHAR(255) NOT NULL,
    `parent_id`       INT          DEFAULT NULL,
    `owner_admin_id`  INT          NOT NULL,
    `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    KEY `ix_document_folders_parent_id` (`parent_id`),
    KEY `ix_document_folders_owner_admin_id` (`owner_admin_id`),
    CONSTRAINT `fk_document_folders_parent` FOREIGN KEY (`parent_id`) REFERENCES `document_folders` (`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_document_folders_owner` FOREIGN KEY (`owner_admin_id`) REFERENCES `admin_users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `documents` (
    `id`                        INT          NOT NULL AUTO_INCREMENT,
    `title`                     VARCHAR(255) NOT NULL DEFAULT 'Без названия',
    `icon`                      VARCHAR(16)  DEFAULT NULL,
    `content`                   JSON         DEFAULT NULL,
    `content_text`              LONGTEXT,
    `folder_id`                 INT          DEFAULT NULL,
    `owner_admin_id`            INT          NOT NULL,
    `last_edited_by_admin_id`   INT          DEFAULT NULL,
    `version`                   INT          NOT NULL DEFAULT 1,
    `is_archived`               TINYINT(1)   NOT NULL DEFAULT 0,
    `is_deleted`                TINYINT(1)   NOT NULL DEFAULT 0,
    `deleted_at`                DATETIME     DEFAULT NULL,
    `created_at`                DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`                DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    KEY `ix_documents_folder_id` (`folder_id`),
    KEY `ix_documents_owner_admin_id` (`owner_admin_id`),
    KEY `ix_documents_is_deleted` (`is_deleted`),
    KEY `ix_documents_created_at` (`created_at`),
    KEY `ix_documents_updated_at` (`updated_at`),
    CONSTRAINT `fk_documents_folder` FOREIGN KEY (`folder_id`) REFERENCES `document_folders` (`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_documents_owner` FOREIGN KEY (`owner_admin_id`) REFERENCES `admin_users` (`id`),
    CONSTRAINT `fk_documents_last_editor` FOREIGN KEY (`last_edited_by_admin_id`) REFERENCES `admin_users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `document_permissions` (
    `id`                   INT          NOT NULL AUTO_INCREMENT,
    `document_id`          INT          NOT NULL,
    `grantee_type`         VARCHAR(20)  NOT NULL COMMENT 'admin|privilege_group|everyone',
    `grantee_admin_id`     INT          DEFAULT NULL,
    `grantee_group_id`     INT          DEFAULT NULL,
    `level`                VARCHAR(10)  NOT NULL DEFAULT 'view' COMMENT 'view|comment|edit',
    `granted_by_admin_id`  INT          DEFAULT NULL,
    `created_at`           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    KEY `ix_document_permissions_document_id` (`document_id`),
    CONSTRAINT `fk_document_permissions_document` FOREIGN KEY (`document_id`) REFERENCES `documents` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_document_permissions_admin` FOREIGN KEY (`grantee_admin_id`) REFERENCES `admin_users` (`id`),
    CONSTRAINT `fk_document_permissions_group` FOREIGN KEY (`grantee_group_id`) REFERENCES `admin_privilege_groups` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `document_revisions` (
    `id`                  INT          NOT NULL AUTO_INCREMENT,
    `document_id`         INT          NOT NULL,
    `title`               VARCHAR(255) DEFAULT NULL,
    `content`             JSON         DEFAULT NULL,
    `version`             INT          NOT NULL,
    `saved_by_admin_id`   INT          DEFAULT NULL,
    `created_at`          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    KEY `ix_document_revisions_document_id` (`document_id`),
    KEY `ix_document_revisions_created_at` (`created_at`),
    CONSTRAINT `fk_document_revisions_document` FOREIGN KEY (`document_id`) REFERENCES `documents` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_document_revisions_admin` FOREIGN KEY (`saved_by_admin_id`) REFERENCES `admin_users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `document_comments` (
    `id`                 INT          NOT NULL AUTO_INCREMENT,
    `document_id`        INT          NOT NULL,
    `parent_id`          INT          DEFAULT NULL,
    `author_admin_id`    INT          NOT NULL,
    `body`               VARCHAR(2000) NOT NULL,
    `anchor_text`        VARCHAR(500) DEFAULT NULL,
    `mark_id`            VARCHAR(64)  DEFAULT NULL,
    `resolved`           TINYINT(1)   NOT NULL DEFAULT 0,
    `created_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    KEY `ix_document_comments_document_id` (`document_id`),
    KEY `ix_document_comments_parent_id` (`parent_id`),
    KEY `ix_document_comments_mark_id` (`mark_id`),
    CONSTRAINT `fk_document_comments_document` FOREIGN KEY (`document_id`) REFERENCES `documents` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_document_comments_parent` FOREIGN KEY (`parent_id`) REFERENCES `document_comments` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_document_comments_author` FOREIGN KEY (`author_admin_id`) REFERENCES `admin_users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `document_stars` (
    `id`             INT      NOT NULL AUTO_INCREMENT,
    `document_id`    INT      NOT NULL,
    `admin_id`       INT      NOT NULL,
    `created_at`     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    UNIQUE KEY `ux_document_star` (`document_id`, `admin_id`),
    CONSTRAINT `fk_document_stars_document` FOREIGN KEY (`document_id`) REFERENCES `documents` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_document_stars_admin` FOREIGN KEY (`admin_id`) REFERENCES `admin_users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
