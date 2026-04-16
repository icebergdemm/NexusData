-- Migration: Add ServerPlayerPrivilege table
-- This table stores server-side privileges for regular players (by SteamID)

CREATE TABLE IF NOT EXISTS server_player_privileges (
    id INT AUTO_INCREMENT PRIMARY KEY,
    steamid VARCHAR(50) NOT NULL UNIQUE,
    player_id INT NULL,
    privileges JSON DEFAULT '[]',
    reason VARCHAR(500) NULL,
    granted_by_admin_id INT NULL,
    granted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NULL,
    
    INDEX idx_steamid (steamid),
    INDEX idx_player_id (player_id),
    INDEX idx_granted_at (granted_at),
    
    CONSTRAINT fk_player_id FOREIGN KEY (player_id) REFERENCES players_data(player_id) ON DELETE SET NULL,
    CONSTRAINT fk_granted_by_admin_id FOREIGN KEY (granted_by_admin_id) REFERENCES admin_users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
