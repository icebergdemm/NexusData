-- Migration 028: Global permission rework — hybrid site-wide grants per
-- department access level (superuser-only), and custom per-page actions
-- replacing the fixed view/edit/delete triad for several pages.
-- NOTE: SQLAlchemy's _apply_migrations() already ALTERs this on startup from
-- models.py; this file mirrors the schema for manual application / historical
-- record. The action-split backfill (database.py: _backfill_permission_splits)
-- is pure Python/JSON logic against admin_privilege_groups.permissions and
-- admin_users.individual_permissions — nothing to mirror here in SQL.

ALTER TABLE `department_access_levels`
    ADD COLUMN `site_permissions` JSON NULL;
