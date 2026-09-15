-- NewWorldStreamer nwdemo-v2 voice profiles. Legacy replay/profile data
-- remains readable but is never selected by v2.

ALTER TABLE `round_replays`
    ADD COLUMN IF NOT EXISTS `replay_format` VARCHAR(32) NULL,
    ADD COLUMN IF NOT EXISTS `voice_profile_state` VARCHAR(20) NULL,
    ADD COLUMN IF NOT EXISTS `voice_profile_error` VARCHAR(2000) NULL,
    ADD COLUMN IF NOT EXISTS `voice_profile_processed_at` DATETIME NULL;

ALTER TABLE `voice_profile_clips`
    MODIFY COLUMN `speaker_id` INT NULL,
    MODIFY COLUMN `wav_filename` VARCHAR(500) NULL,
    ADD COLUMN IF NOT EXISTS `source_format` VARCHAR(32) NULL,
    ADD COLUMN IF NOT EXISTS `source_kind` VARCHAR(16) NULL,
    ADD COLUMN IF NOT EXISTS `is_radio` TINYINT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS `voice_replay_fingerprints` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `replay_id` INT NOT NULL,
    `steam_id` VARCHAR(50) NOT NULL,
    `algorithm` VARCHAR(32) NOT NULL,
    `source_channel` VARCHAR(16) NOT NULL,
    `vector` JSON NOT NULL,
    `quality` VARCHAR(16) NOT NULL DEFAULT 'low',
    `duration_ms` INT NOT NULL DEFAULT 0,
    `packet_count` INT NOT NULL DEFAULT 0,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_voice_replay_fingerprint` (`replay_id`, `steam_id`, `algorithm`),
    KEY `ix_voice_replay_fingerprint_steam_algorithm` (`steam_id`, `algorithm`, `source_channel`)
);

CREATE TABLE IF NOT EXISTS `voice_profile_signatures` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `steam_id` VARCHAR(50) NOT NULL,
    `algorithm` VARCHAR(32) NOT NULL,
    `source_channel` VARCHAR(16) NOT NULL,
    `vector` JSON NOT NULL,
    `sample_count` INT NOT NULL DEFAULT 0,
    `weighted_duration_ms` INT NOT NULL DEFAULT 0,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_voice_profile_signature` (`steam_id`, `algorithm`, `source_channel`)
);
