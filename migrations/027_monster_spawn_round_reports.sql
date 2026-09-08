-- Durable per-round MonsterSpawnController reports and full event exchange log.
ALTER TABLE `monster_spawn_controls`
    ADD COLUMN IF NOT EXISTS `round_reports` JSON NOT NULL;
