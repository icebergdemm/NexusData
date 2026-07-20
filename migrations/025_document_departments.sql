-- Migration 025: Department-scoped documentation + nested document sub-pages.
-- NOTE: SQLAlchemy's _apply_migrations() already ALTERs these on startup from
-- models.py; this file mirrors the schema for manual application / historical record.

ALTER TABLE `documents`
    ADD COLUMN `department_id` INT NULL,
    ADD COLUMN `parent_document_id` INT NULL,
    ADD CONSTRAINT `fk_documents_department` FOREIGN KEY (`department_id`) REFERENCES `departments` (`id`) ON DELETE CASCADE,
    ADD CONSTRAINT `fk_documents_parent` FOREIGN KEY (`parent_document_id`) REFERENCES `documents` (`id`) ON DELETE SET NULL;
