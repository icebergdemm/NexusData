-- Operator-visible, short-lived journal for manual replay voice indexing.
CREATE TABLE IF NOT EXISTS `voice_index_runs` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `requested_by_admin_id` INT NOT NULL,
    `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
    `total` INT NOT NULL DEFAULT 0,
    `processed` INT NOT NULL DEFAULT 0,
    `ready_count` INT NOT NULL DEFAULT 0,
    `error_count` INT NOT NULL DEFAULT 0,
    `last_replay_id` INT NULL,
    `last_message` VARCHAR(1000) NULL,
    `error` VARCHAR(1000) NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `started_at` DATETIME NULL,
    `finished_at` DATETIME NULL,
    `heartbeat_at` DATETIME NULL,
    PRIMARY KEY (`id`),
    KEY `ix_voice_index_runs_requested_by_admin_id` (`requested_by_admin_id`),
    KEY `ix_voice_index_runs_status` (`status`),
    KEY `ix_voice_index_runs_created_at` (`created_at`),
    KEY `ix_voice_index_runs_heartbeat_at` (`heartbeat_at`),
    CONSTRAINT `fk_voice_index_runs_admin`
        FOREIGN KEY (`requested_by_admin_id`) REFERENCES `admin_users` (`id`)
);

CREATE TABLE IF NOT EXISTS `voice_index_run_logs` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `run_id` INT NOT NULL,
    `replay_id` INT NULL,
    `level` VARCHAR(10) NOT NULL DEFAULT 'info',
    `status` VARCHAR(20) NOT NULL,
    `message` VARCHAR(1000) NOT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `ix_voice_index_run_logs_run_id` (`run_id`),
    KEY `ix_voice_index_run_logs_replay_id` (`replay_id`),
    KEY `ix_voice_index_run_logs_created_at` (`created_at`),
    KEY `ix_voice_index_run_logs_run_time` (`run_id`, `created_at`),
    CONSTRAINT `fk_voice_index_run_logs_run`
        FOREIGN KEY (`run_id`) REFERENCES `voice_index_runs` (`id`) ON DELETE CASCADE
);
