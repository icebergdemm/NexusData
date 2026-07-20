-- Migration 021: Link "end" round_events rows back to the "start" row that
-- opened the round, so past rounds can be listed/paginated with a single
-- indexed query instead of pairing start/end rows in application code.
-- NOTE: database.py's _apply_migrations() + _backfill_round_pairing() also
-- add this column and backfill existing rows automatically on startup; this
-- file mirrors the schema change for manual application / historical record.

ALTER TABLE `round_events`
  ADD COLUMN IF NOT EXISTS `paired_start_id` INT DEFAULT NULL;
