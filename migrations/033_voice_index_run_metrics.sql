-- Positive progress metrics for partial replay indexing.
ALTER TABLE `voice_index_runs`
    ADD COLUMN `partial_count` INT NOT NULL DEFAULT 0 AFTER `ready_count`,
    ADD COLUMN `segment_count` INT NOT NULL DEFAULT 0 AFTER `error_count`,
    ADD COLUMN `profile_count` INT NOT NULL DEFAULT 0 AFTER `segment_count`;
