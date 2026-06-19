-- Migration 009: Soft-delete for admin users + fix FK constraints

-- Add is_deleted column for soft-delete
ALTER TABLE `admin_users`
  ADD COLUMN IF NOT EXISTS `is_deleted` TINYINT NOT NULL DEFAULT 0
    COMMENT '1 = soft-deleted, blocks login'
    AFTER `is_superuser`;

-- Fix FK constraint on discord_bot_actions to allow SET NULL on admin delete
ALTER TABLE `discord_bot_actions`
  DROP FOREIGN KEY `discord_bot_actions_ibfk_1`;
ALTER TABLE `discord_bot_actions`
  ADD CONSTRAINT `discord_bot_actions_ibfk_1`
  FOREIGN KEY (`queued_by_admin_id`) REFERENCES `admin_users` (`id`) ON DELETE SET NULL;
