-- Migration 020: Cached Steam avatars + auto-link tracking for the
-- confirmed-Discord-ID -> ND-admin matching worker.
-- NOTE: database.py's _apply_migrations() also adds these columns automatically
-- on startup; this file mirrors it for manual application / historical record.

ALTER TABLE `players_data`
  ADD COLUMN IF NOT EXISTS `linked_admin_auto` TINYINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS `steam_avatar_url` VARCHAR(500) DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS `steam_avatar_fetched_at` DATETIME DEFAULT NULL;
