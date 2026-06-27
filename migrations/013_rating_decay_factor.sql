-- Migration 013: Persistent decay factor for ratings
-- Survives full recalculation (which rebuilds cumulative_points from raw actions).

ALTER TABLE `player_ratings`
    ADD COLUMN IF NOT EXISTS `decay_factor` FLOAT NOT NULL DEFAULT 1.0
        COMMENT 'Persistent multiplier 0.3-1.0 applied on top of raw rating. Erodes daily.';

-- Track when daily erosion was last applied (separate from inactivity decay)
ALTER TABLE `player_ratings`
    ADD COLUMN IF NOT EXISTS `last_daily_decay_at` DATETIME DEFAULT NULL;
