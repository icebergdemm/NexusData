-- Migration 011: Sub-projects categories system + soft-delete for versions

-- ═══════════════════════════════════════════════════════════════════════════
-- SUB-PROJECT CATEGORIES (flexible, manageable by superusers / assigned roles)
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS `sub_project_categories` (
    `id`                INT           NOT NULL AUTO_INCREMENT,
    `name`              VARCHAR(128)  NOT NULL,
    `slug`              VARCHAR(128)  NOT NULL,
    `description`       VARCHAR(500)  DEFAULT NULL,
    `color`             VARCHAR(16)   DEFAULT '#1a68de' COMMENT 'Hex color for UI badge',
    `icon`              VARCHAR(16)   DEFAULT '🚢' COMMENT 'Emoji icon',
    `sort_order`        INT           DEFAULT 0 COMMENT 'Display order (lower = first)',
    `parent_id`         INT           DEFAULT NULL COMMENT 'For nested categories (nullable = top-level)',
    `created_by_admin_id` INT         DEFAULT NULL,
    `created_at`        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_spc_slug` (`slug`),
    KEY `ix_spc_parent` (`parent_id`),
    KEY `ix_spc_sort` (`sort_order`),
    CONSTRAINT `fk_spc_parent` FOREIGN KEY (`parent_id`) REFERENCES `sub_project_categories` (`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_spc_admin` FOREIGN KEY (`created_by_admin_id`) REFERENCES `admin_users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Many-to-many: projects can belong to multiple categories
CREATE TABLE IF NOT EXISTS `sub_project_category_links` (
    `id`            INT NOT NULL AUTO_INCREMENT,
    `project_id`    INT NOT NULL,
    `category_id`   INT NOT NULL,
    `assigned_at`   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_spcl_project_cat` (`project_id`, `category_id`),
    KEY `ix_spcl_category` (`category_id`),
    CONSTRAINT `fk_spcl_project` FOREIGN KEY (`project_id`) REFERENCES `sub_projects` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_spcl_category` FOREIGN KEY (`category_id`) REFERENCES `sub_project_categories` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ═══════════════════════════════════════════════════════════════════════════
-- SOFT-DELETE FOR SUB-PROJECT VERSIONS
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE `sub_project_versions`
    ADD COLUMN IF NOT EXISTS `is_deleted`     TINYINT NOT NULL DEFAULT 0 COMMENT '0=active, 1=soft-deleted',
    ADD COLUMN IF NOT EXISTS `deleted_at`     DATETIME DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS `deleted_by_admin_id` INT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS `ix_spv_deleted` ON `sub_project_versions` (`is_deleted`);
