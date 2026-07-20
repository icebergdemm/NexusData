-- Migration 024: Discord role IDs cached per synced member, used by
-- task_sync_department_roles to auto-provision department membership.
-- NOTE: SQLAlchemy's Base.metadata.create_all()/_apply_migrations() already
-- creates/alters this on startup from models.py; this file mirrors the
-- schema for manual application / historical record.

ALTER TABLE `discord_members`
    ADD COLUMN `role_ids` JSON NULL;
