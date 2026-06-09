-- Migration: Add indexes to admin_action_logs for faster audit page
-- Run: mysql -u root -p nexusdata < migrations/003_add_audit_indexes.sql

CREATE INDEX IF NOT EXISTS ix_admin_action_logs_created_at 
ON admin_action_logs (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_admin_action_logs_admin_id 
ON admin_action_logs (admin_id);
