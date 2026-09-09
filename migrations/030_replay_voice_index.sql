-- Persistent metadata index for replay voice fragments.  Audio stays inside
-- the original replay archive; this table stores only lookup coordinates.
CREATE TABLE IF NOT EXISTS `replay_voice_segments` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `replay_id` INT NOT NULL,
    `steam_id` VARCHAR(50) NOT NULL,
    `speaker_id` INT NOT NULL,
    `start_ms` INT NOT NULL,
    `end_ms` INT NOT NULL,
    `packet_count` INT NOT NULL DEFAULT 0,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_replay_voice_segment` (`replay_id`, `speaker_id`, `start_ms`, `end_ms`),
    KEY `ix_replay_voice_segments_replay_id` (`replay_id`),
    KEY `ix_replay_voice_segments_steam_id` (`steam_id`),
    KEY `ix_replay_voice_segments_steam_time` (`steam_id`, `start_ms`),
    CONSTRAINT `fk_replay_voice_segments_replay`
        FOREIGN KEY (`replay_id`) REFERENCES `round_replays` (`id`) ON DELETE CASCADE
);
