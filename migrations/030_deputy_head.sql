-- Migration 030: Deputy head of department ("Зам главы") — same authority
-- as head (is_head_or_deputy_or_superuser), but grantable by an existing
-- head/deputy, not just a superuser. is_head itself stays superuser-only.
-- NOTE: SQLAlchemy's _apply_migrations()/create_all() already applies this
-- on startup from models.py; this file mirrors the schema for manual
-- application / historical record.

ALTER TABLE `department_members`
    ADD COLUMN `is_deputy_head` TINYINT NOT NULL DEFAULT 0;
