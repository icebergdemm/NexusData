-- Durable speaker-resolution details and moderator decisions for replay voice indexing.
CREATE TABLE IF NOT EXISTS `replay_voice_speakers` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `replay_id` INT NOT NULL,
    `speaker_id` INT NOT NULL,
    `names` JSON NULL,
    `candidate_profiles` JSON NULL,
    `steam_id` VARCHAR(50) NULL,
    `resolution_status` VARCHAR(20) NOT NULL DEFAULT 'unresolved',
    `mapping_source` VARCHAR(32) NULL,
    `packet_count` INT NOT NULL DEFAULT 0,
    `start_ms` INT NULL,
    `end_ms` INT NULL,
    `decided_by_admin_id` INT NULL,
    `decided_at` DATETIME NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_replay_voice_speaker` (`replay_id`, `speaker_id`),
    KEY `ix_replay_voice_speakers_replay_id` (`replay_id`),
    KEY `ix_replay_voice_speakers_steam_id` (`steam_id`),
    KEY `ix_replay_voice_speakers_replay_status` (`replay_id`, `resolution_status`),
    CONSTRAINT `fk_replay_voice_speakers_replay`
        FOREIGN KEY (`replay_id`) REFERENCES `round_replays` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_replay_voice_speakers_admin`
        FOREIGN KEY (`decided_by_admin_id`) REFERENCES `admin_users` (`id`) ON DELETE SET NULL
);
