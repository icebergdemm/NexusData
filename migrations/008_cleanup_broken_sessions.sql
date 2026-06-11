-- Migration 008: Auto-cleanup broken sessions (duration > 8 hours)
-- + Fix play_time discrepancies vs session totals
-- Creates events that run every hour.

DELIMITER $$

-- ═══════════════════════════════════════════════════════════════════════════
-- Procedure 1: удаляет битые сессии
-- ═══════════════════════════════════════════════════════════════════════════

DROP PROCEDURE IF EXISTS `cleanup_broken_sessions`$$
CREATE PROCEDURE `cleanup_broken_sessions`()
BEGIN
    DECLARE deleted_count INT DEFAULT 0;

    -- 1. Удалить завершённые сессии с длительностью > 8 часов (28800 секунд)
    DELETE FROM `player_sessions`
    WHERE `session_duration` IS NOT NULL
      AND `session_duration` > 28800;

    SET deleted_count = ROW_COUNT();

    -- 2. Удалить "зависшие" сессии без logout_time, начатые более 8 часов назад
    DELETE FROM `player_sessions`
    WHERE `logout_time` IS NULL
      AND `login_time` < DATE_SUB(NOW(), INTERVAL 8 HOUR);

    SET deleted_count = deleted_count + ROW_COUNT();

    IF deleted_count > 0 THEN
        INSERT INTO `admin_action_logs` (`admin_id`, `action`, `details`, `created_at`)
        VALUES (NULL, 'auto_cleanup_sessions', JSON_OBJECT('deleted', deleted_count), NOW());
    END IF;
END$$

-- ═══════════════════════════════════════════════════════════════════════════
-- Procedure 2: сверяет play_time с суммой сессий
-- Если play_time превышает сумму сессий на 20+ часов (72000 сек),
-- устанавливает play_time = сумма сессий (исправление некорректных данных)
-- ═══════════════════════════════════════════════════════════════════════════

DROP PROCEDURE IF EXISTS `fix_playtime_discrepancy`$$
CREATE PROCEDURE `fix_playtime_discrepancy`()
BEGIN
    DECLARE fixed_count INT DEFAULT 0;

    -- Обновить play_time для игроков, где play_time > session_total + 72000 сек (20 часов)
    UPDATE `players_data` pd
    INNER JOIN (
        SELECT
            ps.player_id,
            COALESCE(SUM(ps.session_duration), 0) AS session_total
        FROM `player_sessions` ps
        WHERE ps.session_duration IS NOT NULL
        GROUP BY ps.player_id
    ) st ON pd.player_id = st.player_id
    SET pd.play_time = st.session_total
    WHERE pd.play_time > (st.session_total + 72000);

    SET fixed_count = ROW_COUNT();

    IF fixed_count > 0 THEN
        INSERT INTO `admin_action_logs` (`admin_id`, `action`, `details`, `created_at`)
        VALUES (NULL, 'auto_fix_playtime', JSON_OBJECT('fixed_players', fixed_count), NOW());
    END IF;
END$$

-- ═══════════════════════════════════════════════════════════════════════════
-- Events
-- ═══════════════════════════════════════════════════════════════════════════

DROP EVENT IF EXISTS `evt_cleanup_broken_sessions`$$
CREATE EVENT `evt_cleanup_broken_sessions`
ON SCHEDULE EVERY 1 HOUR
STARTS CURRENT_TIMESTAMP
ON COMPLETION PRESERVE
ENABLE
DO
BEGIN
    CALL `cleanup_broken_sessions`();
    CALL `fix_playtime_discrepancy`();
END$$

DELIMITER ;

-- Убедитесь что event_scheduler включён:
-- SET GLOBAL event_scheduler = ON;
-- Или в my.cnf: event_scheduler=ON
