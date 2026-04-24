-- ============================================
-- Database Schema and Triggers Export
-- Database: barotrauma
-- ============================================

-- ============================================
-- 1. GET ALL TABLES STRUCTURE
-- ============================================

SELECT 
    '-- ' as '',
    '-- TABLE: ' as '',
    TABLE_NAME as ''
FROM INFORMATION_SCHEMA.TABLES 
WHERE TABLE_SCHEMA = 'barotrauma' AND TABLE_TYPE = 'BASE TABLE'
ORDER BY TABLE_NAME;

-- Export create statements for all tables
SELECT CONCAT('-- ', TABLE_NAME, '\n', 'DROP TABLE IF EXISTS `', TABLE_NAME, '`;\n', table_create_statement)
FROM (
    SELECT 
        TABLE_NAME,
        CONCAT('CREATE TABLE `', TABLE_NAME, '` (', GROUP_CONCAT(column_def ORDER BY ORDINAL_POSITION SEPARATOR ', '), ');') as table_create_statement
    FROM (
        SELECT 
            t.TABLE_NAME,
            t.ORDINAL_POSITION,
            CONCAT('`', t.COLUMN_NAME, '` ', t.COLUMN_TYPE, 
                   IF(t.IS_NULLABLE = 'NO', ' NOT NULL', ''),
                   IF(t.COLUMN_DEFAULT IS NOT NULL, CONCAT(' DEFAULT ', 
                      IF(t.COLUMN_DEFAULT = 'CURRENT_TIMESTAMP', 'CURRENT_TIMESTAMP',
                      IF(t.DATA_TYPE = 'json', CONCAT("'", t.COLUMN_DEFAULT, "'"),
                      CONCAT("'", t.COLUMN_DEFAULT, "'")))), ''),
                   IF(t.EXTRA != '', CONCAT(' ', t.EXTRA), ''),
                   IF(t.COLUMN_KEY = 'PRI', ' PRIMARY KEY', ''),
                   IF(t.COLUMN_KEY = 'UNI', ' UNIQUE KEY', '')
            ) as column_def
        FROM INFORMATION_SCHEMA.COLUMNS t
        WHERE t.TABLE_SCHEMA = 'barotrauma'
    ) AS cols
    GROUP BY TABLE_NAME
) AS tables_with_statements
ORDER BY TABLE_NAME;

-- ============================================
-- 2. GET ALL INDEXES
-- ============================================

SELECT 
    CONCAT('-- Indexes for table: ', TABLE_NAME) as '',
    CONCAT('ALTER TABLE `', TABLE_NAME, '` ADD ', 
           IF(NON_UNIQUE = 0, 'UNIQUE KEY', 'KEY'),
           ' `', INDEX_NAME, '` (', COLUMN_NAME, ');') as ''
FROM INFORMATION_SCHEMA.STATISTICS
WHERE TABLE_SCHEMA = 'barotrauma' 
  AND INDEX_NAME NOT IN ('PRIMARY')
ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX;

-- ============================================
-- 3. GET ALL FOREIGN KEYS
-- ============================================

SELECT 
    CONCAT('-- Foreign Key: ', CONSTRAINT_NAME, ' on table: ', TABLE_NAME) as '',
    CONCAT('ALTER TABLE `', TABLE_NAME, '` ADD CONSTRAINT `', CONSTRAINT_NAME, '` ',
           'FOREIGN KEY (`', COLUMN_NAME, '`) ',
           'REFERENCES `', REFERENCED_TABLE_NAME, '` (`', REFERENCED_COLUMN_NAME, '`) ',
           'ON UPDATE ', UPDATE_RULE, ' ON DELETE ', DELETE_RULE, ';') as ''
FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = 'barotrauma' 
  AND REFERENCED_TABLE_NAME IS NOT NULL
ORDER BY TABLE_NAME, CONSTRAINT_NAME;

-- ============================================
-- 4. GET ALL TRIGGERS
-- ============================================

SELECT 
    CONCAT('-- Trigger: ', TRIGGER_NAME, ' on table: ', EVENT_OBJECT_TABLE, ' (', EVENT_MANIPULATION, ')') as '',
    CONCAT('DROP TRIGGER IF EXISTS `', TRIGGER_NAME, '`;') as '',
    CONCAT('DELIMITER $$') as '',
    ACTION_STATEMENT as '',
    CONCAT('$$') as '',
    CONCAT('DELIMITER ;') as ''
FROM INFORMATION_SCHEMA.TRIGGERS
WHERE TRIGGER_SCHEMA = 'barotrauma'
ORDER BY EVENT_OBJECT_TABLE, TRIGGER_NAME;

-- ============================================
-- 5. GET ALL STORED PROCEDURES
-- ============================================

SELECT 
    CONCAT('-- Stored Procedure: ', ROUTINE_NAME) as '',
    CONCAT('DROP PROCEDURE IF EXISTS `', ROUTINE_NAME, '`;') as ''
FROM INFORMATION_SCHEMA.ROUTINES
WHERE ROUTINE_SCHEMA = 'barotrauma' 
  AND ROUTINE_TYPE = 'PROCEDURE'
ORDER BY ROUTINE_NAME;

-- ============================================
-- 6. GET ALL FUNCTIONS
-- ============================================

SELECT 
    CONCAT('-- Function: ', ROUTINE_NAME) as '',
    CONCAT('DROP FUNCTION IF EXISTS `', ROUTINE_NAME, '`;') as ''
FROM INFORMATION_SCHEMA.ROUTINES
WHERE ROUTINE_SCHEMA = 'barotrauma' 
  AND ROUTINE_TYPE = 'FUNCTION'
ORDER BY ROUTINE_NAME;

-- ============================================
-- 7. SUMMARY INFO
-- ============================================

SELECT 
    'SUMMARY' as 'Report',
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'barotrauma' AND TABLE_TYPE = 'BASE TABLE') as 'Total Tables',
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.TRIGGERS WHERE TRIGGER_SCHEMA = 'barotrauma') as 'Total Triggers',
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS WHERE TABLE_SCHEMA = 'barotrauma' AND INDEX_NAME != 'PRIMARY') as 'Total Indexes',
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = 'barotrauma' AND REFERENCED_TABLE_NAME IS NOT NULL) as 'Total Foreign Keys',
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = 'barotrauma' AND ROUTINE_TYPE = 'PROCEDURE') as 'Total Procedures',
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = 'barotrauma' AND ROUTINE_TYPE = 'FUNCTION') as 'Total Functions';
