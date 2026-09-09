-- Replay-independent Opus voice archive.  source_replay_id is deliberately
-- not a foreign key: replay retention must not delete selected voice clips.
CREATE TABLE IF NOT EXISTS `voice_archive_clips` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `archive_key` VARCHAR(64) NOT NULL,
    `steam_id` VARCHAR(50) NOT NULL,
    `source_replay_id` INT NOT NULL,
    `source_replay_filename` VARCHAR(500) NOT NULL,
    `server_name` VARCHAR(100) NULL,
    `recorded_at` DATETIME NULL,
    `uploaded_at` DATETIME NULL,
    `speaker_id` INT NOT NULL,
    `start_ms` INT NOT NULL,
    `end_ms` INT NOT NULL,
    `packet_count` INT NOT NULL DEFAULT 0,
    `storage_filename` VARCHAR(500) NOT NULL,
    `storage_size` INT NOT NULL DEFAULT 0,
    `archive_version` INT NOT NULL DEFAULT 0,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_voice_archive_clip_key` (`archive_key`),
    UNIQUE KEY `uq_voice_archive_clip_file` (`storage_filename`),
    KEY `ix_voice_archive_clips_steam_id` (`steam_id`),
    KEY `ix_voice_archive_clips_steam_time` (`steam_id`, `recorded_at`, `start_ms`),
    KEY `ix_voice_archive_clips_source` (`source_replay_id`, `speaker_id`),
    KEY `ix_voice_archive_clips_version` (`archive_version`)
);

CREATE TABLE IF NOT EXISTS `voice_archive_selections` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `steam_id` VARCHAR(50) NOT NULL,
    `slot` INT NOT NULL,
    `band` VARCHAR(16) NOT NULL,
    `clip_id` INT NOT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_voice_archive_selection_slot` (`steam_id`, `slot`),
    UNIQUE KEY `uq_voice_archive_selection_clip` (`steam_id`, `clip_id`),
    KEY `ix_voice_archive_selections_steam_slot` (`steam_id`, `slot`),
    KEY `ix_voice_archive_selections_clip_id` (`clip_id`),
    CONSTRAINT `fk_voice_archive_selection_clip`
        FOREIGN KEY (`clip_id`) REFERENCES `voice_archive_clips` (`id`) ON DELETE CASCADE
);
