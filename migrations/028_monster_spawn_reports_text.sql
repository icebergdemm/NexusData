-- MariaDB's JSON alias accepts legacy empty strings. Store the report envelope
-- as text because the API parses it defensively and must survive old rows.
UPDATE `monster_spawn_controls`
SET `round_reports` = '[]'
WHERE `round_reports` IS NULL OR `round_reports` = '';

ALTER TABLE `monster_spawn_controls`
    MODIFY COLUMN `round_reports` LONGTEXT NOT NULL;
