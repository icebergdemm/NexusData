-- Durable staging/import state for locally generated voice profile corpora.
-- The imported clips remain independent from round_replays.

CREATE TABLE IF NOT EXISTS `voice_profile_imports` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `import_key` VARCHAR(120) NOT NULL,
    `status` VARCHAR(20) NOT NULL DEFAULT 'staged',
    `staging_path` VARCHAR(500) NOT NULL,
    `manifest_sha256` CHAR(64) NOT NULL,
    `total_profiles` INT NOT NULL DEFAULT 0,
    `total_clips` INT NOT NULL DEFAULT 0,
    `imported_profiles` INT NOT NULL DEFAULT 0,
    `imported_clips` INT NOT NULL DEFAULT 0,
    `error_message` VARCHAR(2000) NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `started_at` DATETIME NULL,
    `finished_at` DATETIME NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_voice_profile_import_key` (`import_key`),
    KEY `ix_voice_profile_import_status` (`status`),
    KEY `ix_voice_profile_import_created` (`created_at`)
);
