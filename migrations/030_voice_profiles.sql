-- Replay-independent voice profile corpus.
-- voice_profile_clips intentionally does not foreign-key source_replay_id:
-- replay retention must not delete already materialized player clips.

CREATE TABLE IF NOT EXISTS `voice_profile_jobs` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `mode` VARCHAR(20) NOT NULL DEFAULT 'backfill',
    `status` VARCHAR(20) NOT NULL DEFAULT 'queued',
    `replay_ids` JSON NULL,
    `current_replay_id` INT NULL,
    `total_replays` INT NOT NULL DEFAULT 0,
    `processed_replays` INT NOT NULL DEFAULT 0,
    `mapped_speakers` INT NOT NULL DEFAULT 0,
    `unresolved_speakers` INT NOT NULL DEFAULT 0,
    `materialized_clips` INT NOT NULL DEFAULT 0,
    `attempt_count` INT NOT NULL DEFAULT 0,
    `error_message` VARCHAR(2000) NULL,
    `heartbeat_at` DATETIME NULL,
    `started_at` DATETIME NULL,
    `finished_at` DATETIME NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `ix_voice_profile_jobs_status` (`status`),
    KEY `ix_voice_profile_jobs_current_replay` (`current_replay_id`),
    KEY `ix_voice_profile_jobs_heartbeat` (`heartbeat_at`),
    KEY `ix_voice_profile_jobs_created` (`created_at`)
);

CREATE TABLE IF NOT EXISTS `replay_voice_speakers` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `replay_id` INT NOT NULL,
    `speaker_id` INT NOT NULL,
    `names` JSON NOT NULL,
    `candidate_profiles` JSON NOT NULL,
    `steam_id` VARCHAR(50) NULL,
    `player_id` INT NULL,
    `resolution_status` VARCHAR(20) NOT NULL DEFAULT 'unresolved',
    `mapping_source` VARCHAR(32) NULL,
    `packet_count` INT NOT NULL DEFAULT 0,
    `radio_packet_count` INT NOT NULL DEFAULT 0,
    `start_ms` INT NULL,
    `end_ms` INT NULL,
    `decided_by_admin_id` INT NULL,
    `decided_at` DATETIME NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_replay_voice_speaker` (`replay_id`, `speaker_id`),
    KEY `ix_replay_voice_speakers_replay_status` (`replay_id`, `resolution_status`),
    KEY `ix_replay_voice_speakers_steam_id` (`steam_id`),
    CONSTRAINT `fk_replay_voice_speaker_replay` FOREIGN KEY (`replay_id`) REFERENCES `round_replays` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_replay_voice_speaker_player` FOREIGN KEY (`player_id`) REFERENCES `players_data` (`player_id`) ON DELETE SET NULL,
    CONSTRAINT `fk_replay_voice_speaker_admin` FOREIGN KEY (`decided_by_admin_id`) REFERENCES `admin_users` (`id`) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS `replay_voice_segments` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `replay_id` INT NOT NULL,
    `speaker_id` INT NOT NULL,
    `steam_id` VARCHAR(50) NULL,
    `player_id` INT NULL,
    `start_ms` INT NOT NULL,
    `end_ms` INT NOT NULL,
    `packet_count` INT NOT NULL DEFAULT 0,
    `radio_packet_count` INT NOT NULL DEFAULT 0,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_replay_voice_segment` (`replay_id`, `speaker_id`, `start_ms`, `end_ms`),
    KEY `ix_replay_voice_segments_steam_time` (`steam_id`, `start_ms`),
    CONSTRAINT `fk_replay_voice_segment_replay` FOREIGN KEY (`replay_id`) REFERENCES `round_replays` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_replay_voice_segment_player` FOREIGN KEY (`player_id`) REFERENCES `players_data` (`player_id`) ON DELETE SET NULL
);

-- Older deployments already contain the two replay_voice_* tables from the
-- first replay index.  CREATE TABLE IF NOT EXISTS deliberately leaves those
-- tables untouched, so bring their missing columns up to the profile schema.
ALTER TABLE `replay_voice_speakers`
    ADD COLUMN IF NOT EXISTS `player_id` INT NULL AFTER `steam_id`,
    ADD COLUMN IF NOT EXISTS `radio_packet_count` INT NOT NULL DEFAULT 0 AFTER `packet_count`;

ALTER TABLE `replay_voice_segments`
    MODIFY COLUMN `steam_id` VARCHAR(50) NULL,
    ADD COLUMN IF NOT EXISTS `player_id` INT NULL AFTER `steam_id`,
    ADD COLUMN IF NOT EXISTS `radio_packet_count` INT NOT NULL DEFAULT 0 AFTER `packet_count`;

CREATE TABLE IF NOT EXISTS `voice_profile_clips` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `archive_key` VARCHAR(64) NOT NULL,
    `steam_id` VARCHAR(50) NOT NULL,
    `player_id` INT NULL,
    `source_replay_id` INT NULL,
    `source_replay_filename` VARCHAR(500) NOT NULL,
    `source_replay_hash` VARCHAR(64) NOT NULL,
    `speaker_id` INT NOT NULL,
    `start_ms` INT NOT NULL,
    `end_ms` INT NOT NULL,
    `packet_count` INT NOT NULL DEFAULT 0,
    `radio_packet_count` INT NOT NULL DEFAULT 0,
    `ndvc_filename` VARCHAR(500) NULL,
    `wav_filename` VARCHAR(500) NOT NULL,
    `storage_format` VARCHAR(32) NOT NULL DEFAULT 'wav-gzip',
    `storage_size` INT NOT NULL DEFAULT 0,
    `archive_version` INT NOT NULL DEFAULT 1,
    `recorded_at` DATETIME NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_voice_profile_clip_key` (`archive_key`),
    UNIQUE KEY `uq_voice_profile_clip_ndvc` (`ndvc_filename`),
    UNIQUE KEY `uq_voice_profile_clip_wav` (`wav_filename`),
    KEY `ix_voice_profile_clips_steam_time` (`steam_id`, `recorded_at`, `start_ms`),
    KEY `ix_voice_profile_clips_source` (`source_replay_id`, `speaker_id`),
    CONSTRAINT `fk_voice_profile_clip_player` FOREIGN KEY (`player_id`) REFERENCES `players_data` (`player_id`) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS `voice_profile_selections` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `steam_id` VARCHAR(50) NOT NULL,
    `slot` INT NOT NULL,
    `band` VARCHAR(16) NOT NULL,
    `clip_id` INT NOT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_voice_profile_selection_slot` (`steam_id`, `slot`),
    UNIQUE KEY `uq_voice_profile_selection_clip` (`steam_id`, `clip_id`),
    KEY `ix_voice_profile_selections_steam_slot` (`steam_id`, `slot`),
    CONSTRAINT `fk_voice_profile_selection_clip` FOREIGN KEY (`clip_id`) REFERENCES `voice_profile_clips` (`id`) ON DELETE CASCADE
);
