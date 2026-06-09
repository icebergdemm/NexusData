-- Migration 007: Multi-server privileges + player nicknames in privilege list

-- Add server_scope to server_player_privileges
-- NULL = applies to all servers, 'ASPER1' = only ASPER1, 'ASPER2' = only ASPER2
ALTER TABLE `server_player_privileges`
  ADD COLUMN IF NOT EXISTS `server_scope` VARCHAR(20) DEFAULT NULL
    COMMENT 'NULL=all servers | ASPER1 | ASPER2'
    AFTER `expires_at`;

-- Index for quick lookup by server scope
CREATE INDEX IF NOT EXISTS `ix_spp_server_scope` ON `server_player_privileges` (`server_scope`);
