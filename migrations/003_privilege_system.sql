-- Migration 003: Privilege groups and flexible permission system
-- MySQL / MariaDB — phpMyAdmin compatible
-- ⚠️ Запускать ОДИН РАЗ на чистой базе (без этих таблиц/колонок)

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ── Таблица групп привилегий ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `admin_privilege_groups` (
    `id`                     INT          NOT NULL AUTO_INCREMENT,
    `name`                   VARCHAR(100) NOT NULL,
    `description`            VARCHAR(500)          DEFAULT NULL,
    `permissions`            JSON         NOT NULL,
    `rate_limit_count`       INT          NOT NULL DEFAULT 0,
    `rate_limit_window_secs` INT          NOT NULL DEFAULT 3600,
    `created_at`             DATETIME              DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_apg_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Таблица счётчиков rate-limit ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `admin_rate_limits` (
    `id`           INT          NOT NULL AUTO_INCREMENT,
    `admin_id`     INT          NOT NULL,
    `action`       VARCHAR(100)          DEFAULT 'general',
    `count`        INT                   DEFAULT 0,
    `window_start` DATETIME              DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_admin_rate_limits_admin` (`admin_id`, `action`),
    CONSTRAINT `fk_arl_admin`
        FOREIGN KEY (`admin_id`) REFERENCES `admin_users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Расширение таблицы admin_users ─────────────────────────────────────────
ALTER TABLE `admin_users`
    ADD COLUMN `privilege_group_id`    INT          DEFAULT NULL,
    ADD COLUMN `individual_permissions` JSON         DEFAULT NULL,
    ADD COLUMN `is_superuser`          TINYINT(1)   NOT NULL DEFAULT 0,
    ADD CONSTRAINT `fk_au_privilege_group`
        FOREIGN KEY (`privilege_group_id`) REFERENCES `admin_privilege_groups` (`id`)
        ON DELETE SET NULL;

-- ── Стартовые группы ───────────────────────────────────────────────────────
INSERT IGNORE INTO `admin_privilege_groups`
    (`name`, `description`, `permissions`, `rate_limit_count`, `rate_limit_window_secs`)
VALUES
  (
    'Суперпользователь',
    'Полный доступ ко всем разделам без ограничений',
    '{"pages":{"players":{"view":true,"edit":true},"kits":{"view":true,"edit":true},"moderation":{"view":true,"edit":true},"punishments":{"view":true,"edit":true},"users":{"view":true,"edit":true},"audit":{"view":true},"sessions":{"view":true},"privileges":{"view":true,"edit":true},"administration":{"view":true}}}',
    0, 3600
  ),
  (
    'Администратор',
    'Полный доступ к игрокам, наказаниям, модерации и китам',
    '{"pages":{"players":{"view":true,"edit":true},"kits":{"view":true,"edit":true},"moderation":{"view":true,"edit":true},"punishments":{"view":true,"edit":true},"users":{"view":false,"edit":false},"audit":{"view":true},"sessions":{"view":true},"privileges":{"view":true,"edit":true},"administration":{"view":true}}}',
    200, 3600
  ),
  (
    'Модератор',
    'Просмотр игроков и выдача наказаний, без редактирования системных данных',
    '{"pages":{"players":{"view":true,"edit":false},"kits":{"view":true,"edit":false},"moderation":{"view":true,"edit":false},"punishments":{"view":true,"edit":true},"users":{"view":false,"edit":false},"audit":{"view":false},"sessions":{"view":true},"privileges":{"view":false,"edit":false},"administration":{"view":false}}}',
    100, 3600
  ),
  (
    'Читатель',
    'Только просмотр — без возможности изменений',
    '{"pages":{"players":{"view":true,"edit":false},"kits":{"view":true,"edit":false},"moderation":{"view":true,"edit":false},"punishments":{"view":true,"edit":false},"users":{"view":false,"edit":false},"audit":{"view":false},"sessions":{"view":true},"privileges":{"view":false,"edit":false},"administration":{"view":false}}}',
    50, 3600
  );

SET FOREIGN_KEY_CHECKS = 1;
