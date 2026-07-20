-- Migration 038: replace the Tiptap/ProseMirror-JSON document editor with
-- ONLYOFFICE-edited real .docx/.xlsx files, and remove the database/wiki
-- module (no ONLYOFFICE equivalent).
--
-- DESTRUCTIVE — unlike every other migration file in this repo (which only
-- mirrors additive ALTER TABLE ADD COLUMN statements SQLAlchemy already
-- applied automatically on startup), this one contains real DROPs and must
-- be run MANUALLY when you're ready — the app does not execute this file,
-- and _apply_migrations() in database.py never drops anything itself.
-- All existing document/wiki/database content is UNRECOVERABLE after this
-- runs (confirmed acceptable — no export/backup was requested).
--
-- The new `file_type`/`storage_filename` columns on documents/
-- document_templates/document_revisions are already added automatically by
-- the app (see database.py's _apply_migrations()) — this file only needs to
-- drop the old columns/tables.

DROP TABLE IF EXISTS `database_views`;
DROP TABLE IF EXISTS `database_rows`;
DROP TABLE IF EXISTS `document_databases`;
DROP TABLE IF EXISTS `document_comments`;

ALTER TABLE `documents`
    DROP COLUMN `content`,
    DROP COLUMN `content_text`,
    DROP COLUMN `is_wiki`;

ALTER TABLE `document_revisions`
    DROP COLUMN `content`;

ALTER TABLE `document_templates`
    DROP COLUMN `content`;
