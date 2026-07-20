-- Migration 032: manual replay<->round link (item 9) — escape hatch for
-- when the header-timestamp/server_name auto-matcher can't find a round.
-- NOTE: SQLAlchemy's _apply_migrations()/create_all() already applies this
-- on startup from models.py; this file mirrors the schema for manual
-- application / historical record.

ALTER TABLE `round_replays`
    ADD COLUMN `manual_round_event_id` INT NULL,
    ADD CONSTRAINT `fk_round_replays_manual_round` FOREIGN KEY (`manual_round_event_id`) REFERENCES `round_events` (`id`) ON DELETE SET NULL;
