-- Migration 018: Snapshot whether the issuing admin was a superuser at the
-- time a punishment was issued (for the gold-star badge in the admin panel).
-- NOTE: database.py's _apply_migrations() also adds this column automatically
-- on startup; this file mirrors it for manual application / historical record.

ALTER TABLE `player_punishments`
  ADD COLUMN IF NOT EXISTS `issued_by_was_superuser` TINYINT NOT NULL DEFAULT 0
  COMMENT 'Snapshot of admin_users.is_superuser at issue time';
