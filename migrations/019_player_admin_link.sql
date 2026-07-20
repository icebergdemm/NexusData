-- Migration 019: Superuser-managed link between a player character and an ND
-- admin account, plus a forced display name shown everywhere in ratings and
-- punishments regardless of how the underlying data was created (game-server
-- API or the admin panel) since it's resolved at display time.
-- NOTE: database.py's _apply_migrations() also adds these columns automatically
-- on startup; this file mirrors it for manual application / historical record.

ALTER TABLE `players_data`
  ADD COLUMN IF NOT EXISTS `linked_admin_id` INT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS `forced_display_name` VARCHAR(255) DEFAULT NULL;
