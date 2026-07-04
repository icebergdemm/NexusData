-- Migration 014: Secret profiles, sub-project soft-delete, category permissions

-- Player "secret" flag (hides profile from non-superusers)
ALTER TABLE `players_data`
    ADD COLUMN IF NOT EXISTS `is_secret` TINYINT NOT NULL DEFAULT 0
        COMMENT '1 = profile hidden from non-superusers';

-- Sub-project soft-delete
ALTER TABLE `sub_projects`
    ADD COLUMN IF NOT EXISTS `is_deleted` TINYINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS `deleted_at` DATETIME DEFAULT NULL;

CREATE INDEX IF NOT EXISTS `ix_sp_deleted` ON `sub_projects` (`is_deleted`);

-- Category management permissions (stored as JSON list of AdminPrivilegeGroup IDs)
-- allowed_to_create: which groups can create categories
-- allowed_to_assign: which groups can assign categories to projects
ALTER TABLE `sub_project_categories`
    ADD COLUMN IF NOT EXISTS `allowed_to_create` JSON DEFAULT NULL
        COMMENT 'List of AdminPrivilegeGroup IDs allowed to create/manage this category',
    ADD COLUMN IF NOT EXISTS `allowed_to_assign` JSON DEFAULT NULL
        COMMENT 'List of AdminPrivilegeGroup IDs allowed to assign projects to this category';

-- Global settings for category permissions (stored in app_settings)
-- Key: 'sub_cat_create_groups' = JSON list of group IDs
-- Key: 'sub_cat_assign_groups' = JSON list of group IDs
-- Key: 'sub_cat_default_id' = int (default category ID for new projects)
