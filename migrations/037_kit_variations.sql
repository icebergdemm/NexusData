-- Migration 037: kit variations + special kits (item 15) — additive only.
-- `items` (variation 1) is untouched, so an old game-server client that only
-- reads job_id/kit_level/items keeps working without any changes.
-- NOTE: SQLAlchemy's _apply_migrations()/create_all() already applies this
-- on startup from models.py; this file mirrors the schema for manual
-- application / historical record.

ALTER TABLE `kits`
    ADD COLUMN `variation_2_items` JSON DEFAULT NULL,
    ADD COLUMN `variation_3_items` JSON DEFAULT NULL,
    ADD COLUMN `is_special`        TINYINT NOT NULL DEFAULT 0,
    ADD COLUMN `special_variation` INT DEFAULT NULL;
