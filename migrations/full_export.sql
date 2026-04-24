/*M!999999\- enable the sandbox mode */ 
-- MariaDB dump 10.19  Distrib 10.11.14-MariaDB, for debian-linux-gnu (x86_64)
--
-- Host: localhost    Database: barotrauma
-- ------------------------------------------------------
-- Server version	10.11.14-MariaDB-0ubuntu0.24.04.1

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `admin_action_logs`
--

DROP TABLE IF EXISTS `admin_action_logs`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `admin_action_logs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `admin_id` int(11) DEFAULT NULL,
  `action` varchar(255) DEFAULT NULL,
  `details` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`details`)),
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_admin_action_logs_id` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=171 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `admin_action_logs`
--

LOCK TABLES `admin_action_logs` WRITE;
/*!40000 ALTER TABLE `admin_action_logs` DISABLE KEYS */;
INSERT INTO `admin_action_logs` VALUES
(1,1,'login','{\"method\": \"discord_oauth\"}','2026-03-23 19:08:53'),
(2,1,'logout','{\"revoked\": true}','2026-03-23 19:22:41'),
(3,1,'login','{\"method\": \"discord_oauth\"}','2026-03-23 19:23:20'),
(4,1,'logout','{\"revoked\": true}','2026-03-23 19:26:22'),
(5,3,'login','{\"method\": \"discord_oauth\"}','2026-03-23 20:17:40'),
(6,1,'login','{\"method\": \"discord_oauth\"}','2026-03-23 20:20:23'),
(7,1,'update_player','{\"player_id\": 1}','2026-03-23 20:44:31'),
(8,1,'update_player','{\"player_id\": 1}','2026-03-23 20:44:44'),
(9,1,'update_player','{\"player_id\": 1}','2026-03-23 20:47:06'),
(10,1,'update_player','{\"player_id\": 1}','2026-03-23 20:48:17'),
(11,1,'update_player','{\"player_id\": 1}','2026-03-23 20:51:47'),
(12,1,'update_player','{\"player_id\": 1}','2026-03-23 20:51:54'),
(13,1,'update_player','{\"player_id\": 1}','2026-03-23 20:52:04'),
(14,1,'update_admin_role','{\"admin_id\": 3, \"role\": \"\\u0410\\u0434\\u043c\\u0438\\u043d\\u0438\\u0441\\u0442\\u0440\\u0430\\u0442\\u043e\\u0440\"}','2026-03-23 20:52:12'),
(15,1,'view_player_profile','{\"target\": {\"player_id\": 1, \"steamid\": \"76561198297267585\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440 \\u0410\\u043d\\u0434\\u0440\\u043e\\u0441\\u043e\\u0432\"}}','2026-03-23 21:01:47'),
(16,1,'update_player','{\"target\": {\"player_id\": 1, \"steamid\": \"76561198297267585\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440 \\u0410\\u043d\\u0434\\u0440\\u043e\\u0441\\u043e\\u0432\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 7, \"to\": 0}, {\"field\": \"kit_level\", \"from\": 4, \"to\": 3}]}','2026-03-23 21:01:57'),
(17,1,'view_player_profile','{\"target\": {\"player_id\": 1, \"steamid\": \"76561198297267585\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440 \\u0410\\u043d\\u0434\\u0440\\u043e\\u0441\\u043e\\u0432\"}}','2026-03-23 21:01:57'),
(18,1,'create_admin','{\"target\": {\"admin_id\": 6, \"discord_id\": \"800404741175246848\", \"username\": \"User_800404741175246848\"}, \"changes\": [{\"field\": \"role\", \"from\": null, \"to\": \"\\u0423\\u043f\\u0440\\u0430\\u0432\\u043b\\u044f\\u044e\\u0449\\u0438\\u0439\"}]}','2026-03-23 21:08:58'),
(19,6,'login','{\"method\": \"discord_oauth\"}','2026-03-25 20:13:15'),
(20,6,'view_player_profile','{\"target\": {\"player_id\": 1038, \"steamid\": \"76561199256373351\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0424\\u0435\\u043e\\u0444\\u0430\\u043d \\u041a\\u0440\\u0435\\u0441\\u0442\\u043e\\u0432\"}}','2026-03-25 20:13:24'),
(21,1,'login','{\"method\": \"discord_oauth\"}','2026-03-26 14:20:04'),
(22,6,'login','{\"method\": \"discord_oauth\"}','2026-03-30 10:44:42'),
(23,1,'login','{\"method\": \"discord_oauth\"}','2026-03-31 22:39:44'),
(24,1,'view_player_profile','{\"target\": {\"player_id\": 1, \"steamid\": \"76561198297267585\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440 \\u0410\\u043d\\u0434\\u0440\\u043e\\u0441\\u043e\\u0432\"}}','2026-03-31 22:39:48'),
(25,6,'view_player_profile','{\"target\": {\"player_id\": 1, \"steamid\": \"76561198297267585\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440 \\u0410\\u043d\\u0434\\u0440\\u043e\\u0441\\u043e\\u0432\"}}','2026-04-01 10:59:58'),
(26,6,'view_player_profile','{\"target\": {\"player_id\": 135, \"steamid\": \"76561199060347475\", \"name\": \"\\u0412\\u043e\\u0432\\u0430\\u043d \\u0416\\u0451\\u043b\\u0443\\u0434\\u044c\"}}','2026-04-01 11:00:23'),
(27,6,'view_player_profile','{\"target\": {\"player_id\": 1038, \"steamid\": \"76561199256373351\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0424\\u0435\\u043e\\u0444\\u0430\\u043d \\u041a\\u0440\\u0435\\u0441\\u0442\\u043e\\u0432\"}}','2026-04-01 11:01:54'),
(28,6,'view_player_profile','{\"target\": {\"player_id\": 533, \"steamid\": \"76561199186701625\", \"name\": \"FyLs\"}}','2026-04-01 11:13:32'),
(29,6,'view_player_profile','{\"target\": {\"player_id\": 881, \"steamid\": \"76561198300593777\", \"name\": \"Plaga Medicus\"}}','2026-04-01 13:39:33'),
(30,6,'view_player_profile','{\"target\": {\"player_id\": 881, \"steamid\": \"76561198300593777\", \"name\": \"Plaga Medicus\"}}','2026-04-01 15:27:03'),
(31,1,'view_player_profile','{\"target\": {\"player_id\": 5, \"steamid\": \"76561198155521878\", \"name\": \"W!Nd\"}}','2026-04-02 12:28:14'),
(32,1,'view_player_profile','{\"target\": {\"player_id\": 439, \"steamid\": \"76561199184041569\", \"name\": \"\\u0413\\u0435\\u043d\\u0440\\u0438\\u0445\"}}','2026-04-02 13:59:02'),
(33,6,'login','{\"method\": \"discord_oauth\"}','2026-04-05 12:36:28'),
(34,6,'view_player_profile','{\"target\": {\"player_id\": 439, \"steamid\": \"76561199184041569\", \"name\": \"\\u0413\\u0435\\u043d\\u0440\\u0438\\u0445\"}}','2026-04-05 12:36:44'),
(35,6,'update_player','{\"target\": {\"player_id\": 439, \"steamid\": \"76561199184041569\", \"name\": \"\\u0413\\u0435\\u043d\\u0440\\u0438\\u0445\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 2}, {\"field\": \"kit_level\", \"from\": 1, \"to\": 2}]}','2026-04-05 12:36:56'),
(36,6,'view_player_profile','{\"target\": {\"player_id\": 439, \"steamid\": \"76561199184041569\", \"name\": \"\\u0413\\u0435\\u043d\\u0440\\u0438\\u0445\"}}','2026-04-05 12:36:56'),
(37,6,'view_player_profile','{\"target\": {\"player_id\": 47, \"steamid\": \"76561199078030627\", \"name\": \"\\u0410\\u043d\\u0434\\u0440\\u044e\\u0448\\u0430\"}}','2026-04-05 12:37:30'),
(38,6,'update_player','{\"target\": {\"player_id\": 47, \"steamid\": \"76561199078030627\", \"name\": \"\\u0410\\u043d\\u0434\\u0440\\u044e\\u0448\\u0430\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 2, \"to\": 3}]}','2026-04-05 12:37:42'),
(39,6,'view_player_profile','{\"target\": {\"player_id\": 47, \"steamid\": \"76561199078030627\", \"name\": \"\\u0410\\u043d\\u0434\\u0440\\u044e\\u0448\\u0430\"}}','2026-04-05 12:37:42'),
(40,6,'view_player_profile','{\"target\": {\"player_id\": 4, \"steamid\": \"76561199566167348\", \"name\": \"\\u0421\\u0442\\u0430\\u0440\\u043e\\u0435 \\u0447\\u0443\\u0434\\u043e\\u0432\\u0438\\u0449\\u0435\"}}','2026-04-05 12:37:49'),
(41,6,'view_player_profile','{\"target\": {\"player_id\": 2, \"steamid\": \"76561198289354854\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0421\\u0435\\u0440\\u0433\\u0435\\u0439 \\u0410\\u0432\\u0435\\u0440\\u0438\\u043d\"}}','2026-04-05 12:37:56'),
(42,6,'view_player_profile','{\"target\": {\"player_id\": 881, \"steamid\": \"76561198300593777\", \"name\": \"Plaga Medicus\"}}','2026-04-05 13:29:22'),
(43,6,'update_player','{\"target\": {\"player_id\": 881, \"steamid\": \"76561198300593777\", \"name\": \"Plaga Medicus\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 1}, {\"field\": \"kit_level\", \"from\": 0, \"to\": 1}]}','2026-04-05 13:29:29'),
(44,6,'view_player_profile','{\"target\": {\"player_id\": 881, \"steamid\": \"76561198300593777\", \"name\": \"Plaga Medicus\"}}','2026-04-05 13:29:29'),
(45,6,'view_player_profile','{\"target\": {\"player_id\": 635, \"steamid\": \"76561199782470637\", \"name\": \"romasdmitriev\"}}','2026-04-05 13:29:40'),
(46,6,'view_player_profile','{\"target\": {\"player_id\": 635, \"steamid\": \"76561199782470637\", \"name\": \"romasdmitriev\"}}','2026-04-06 12:14:08'),
(47,6,'view_player_profile','{\"target\": {\"player_id\": 635, \"steamid\": \"76561199782470637\", \"name\": \"romasdmitriev\"}}','2026-04-06 20:22:32'),
(48,6,'view_player_profile','{\"target\": {\"player_id\": 55, \"steamid\": \"76561199220215839\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u041c\\u0430\\u0440\\u043a\\u0443\\u0441 \\u0411\\u0435\\u043b\\u043e\\u0432\"}}','2026-04-06 21:28:53'),
(49,6,'view_player_profile','{\"target\": {\"player_id\": 881, \"steamid\": \"76561198300593777\", \"name\": \"Plaga Medicus\"}}','2026-04-06 21:28:54'),
(50,6,'view_player_profile','{\"target\": {\"player_id\": 610, \"steamid\": \"76561198770065800\", \"name\": \"KvOrtIx\"}}','2026-04-06 21:30:39'),
(51,6,'view_player_profile','{\"target\": {\"player_id\": 1038, \"steamid\": \"76561199256373351\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0424\\u0435\\u043e\\u0444\\u0430\\u043d \\u041a\\u0440\\u0435\\u0441\\u0442\\u043e\\u0432\"}}','2026-04-07 08:23:31'),
(52,6,'login','{\"method\": \"discord_oauth\"}','2026-04-07 16:03:18'),
(53,6,'view_player_profile','{\"target\": {\"player_id\": 1096, \"steamid\": \"76561199231930625\", \"name\": \"bars\"}}','2026-04-07 16:03:36'),
(54,6,'login','{\"method\": \"discord_oauth\"}','2026-04-09 19:52:58'),
(55,6,'view_player_profile','{\"target\": {\"player_id\": 1096, \"steamid\": \"76561199231930625\", \"name\": \"bars\"}}','2026-04-09 19:53:06'),
(56,6,'view_player_profile','{\"target\": {\"player_id\": 1096, \"steamid\": \"76561199231930625\", \"name\": \"bars\"}}','2026-04-09 20:49:15'),
(57,3,'login','{\"method\": \"discord_oauth\"}','2026-04-09 23:23:19'),
(58,6,'view_player_profile','{\"target\": {\"player_id\": 1096, \"steamid\": \"76561199231930625\", \"name\": \"bars\"}}','2026-04-12 17:18:30'),
(59,6,'view_player_profile','{\"target\": {\"player_id\": 533, \"steamid\": \"76561199186701625\", \"name\": \"FyLs\"}}','2026-04-12 19:42:17'),
(60,6,'view_player_profile','{\"target\": {\"player_id\": 55, \"steamid\": \"76561199220215839\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u041c\\u0430\\u0440\\u043a\\u0443\\u0441 \\u0411\\u0435\\u043b\\u043e\\u0432\"}}','2026-04-13 17:47:19'),
(61,6,'view_player_profile','{\"target\": {\"player_id\": 314, \"steamid\": \"76561199388873317\", \"name\": \"Bydlo  Scytyjskie\"}}','2026-04-13 21:56:58'),
(62,1,'login','{\"method\": \"discord_oauth\"}','2026-04-16 15:36:28'),
(63,1,'view_player_profile','{\"target\": {\"player_id\": 533, \"steamid\": \"76561199186701625\", \"name\": \"FyLs\"}}','2026-04-17 01:16:28'),
(64,1,'view_player_profile','{\"target\": {\"player_id\": 1, \"steamid\": \"76561198297267585\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440 \\u0410\\u043d\\u0434\\u0440\\u043e\\u0441\\u043e\\u0432\"}}','2026-04-17 01:24:28'),
(65,1,'view_player_profile','{\"target\": {\"player_id\": 47, \"steamid\": \"76561199078030627\", \"name\": \"\\u0410\\u043d\\u0434\\u0440\\u044e\\u0448\\u0430\"}}','2026-04-17 01:27:32'),
(66,1,'view_player_profile','{\"target\": {\"player_id\": 1, \"steamid\": \"76561198297267585\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440 \\u0410\\u043d\\u0434\\u0440\\u043e\\u0441\\u043e\\u0432\"}}','2026-04-17 01:28:19'),
(67,1,'view_player_profile','{\"target\": {\"player_id\": 6, \"steamid\": \"76561199572763629\", \"name\": \"Supchik\"}}','2026-04-17 01:28:22'),
(68,1,'view_player_profile','{\"target\": {\"player_id\": 1, \"steamid\": \"76561198297267585\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440 \\u0410\\u043d\\u0434\\u0440\\u043e\\u0441\\u043e\\u0432\"}}','2026-04-17 01:29:15'),
(69,1,'grant_player_privilege','{\"steamid\": \"76561198297267585\", \"privileges\": [\"mute\", \"kick\", \"ban\", \"manage_whitelist\", \"manage_moderators\", \"manage_admins\"], \"reason\": \"\"}','2026-04-17 01:33:21'),
(70,1,'revoke_player_privilege','{\"steamid\": \"76561198297267585\"}','2026-04-17 01:33:52'),
(71,3,'login','{\"method\": \"discord_oauth\"}','2026-04-20 12:07:18'),
(72,3,'view_player_profile','{\"target\": {\"player_id\": 575, \"steamid\": \"76561199052213855\", \"name\": \"\\u041f\\u0435\\u0442\\u044f20777\"}}','2026-04-20 12:07:26'),
(73,3,'view_player_profile','{\"target\": {\"player_id\": 575, \"steamid\": \"76561199052213855\", \"name\": \"\\u041f\\u0435\\u0442\\u044f20777\"}}','2026-04-20 12:07:37'),
(74,3,'view_player_profile','{\"target\": {\"player_id\": 56, \"steamid\": \"76561199729584441\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 2-\\u0441\\u0442 \\u0412\\u0435\\u0440\\u043d\\u0435\\u0440 \\u041c\\u0430\\u0432\\u0440\\u0438\\u043d\"}}','2026-04-20 12:09:12'),
(75,3,'view_player_profile','{\"target\": {\"player_id\": 314, \"steamid\": \"76561199388873317\", \"name\": \"Bydlo  Scytyjskie\"}}','2026-04-20 12:11:19'),
(76,3,'update_player','{\"target\": {\"player_id\": 314, \"steamid\": \"76561199388873317\", \"name\": \"Bydlo  Scytyjskie\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 1}]}','2026-04-20 12:11:25'),
(77,3,'view_player_profile','{\"target\": {\"player_id\": 314, \"steamid\": \"76561199388873317\", \"name\": \"Bydlo  Scytyjskie\"}}','2026-04-20 12:11:25'),
(78,3,'view_player_profile','{\"target\": {\"player_id\": 1096, \"steamid\": \"76561199231930625\", \"name\": \"bars\"}}','2026-04-20 12:11:35'),
(79,3,'update_player','{\"target\": {\"player_id\": 1096, \"steamid\": \"76561199231930625\", \"name\": \"bars\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 1}, {\"field\": \"kit_level\", \"from\": 0, \"to\": 1}]}','2026-04-20 12:11:46'),
(80,3,'view_player_profile','{\"target\": {\"player_id\": 1096, \"steamid\": \"76561199231930625\", \"name\": \"bars\"}}','2026-04-20 12:11:46'),
(81,3,'view_player_profile','{\"target\": {\"player_id\": 272, \"steamid\": \"76561199132911357\", \"name\": \"pakepko_j\"}}','2026-04-20 12:12:43'),
(82,3,'update_player','{\"target\": {\"player_id\": 272, \"steamid\": \"76561199132911357\", \"name\": \"pakepko_j\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 1}]}','2026-04-20 12:13:08'),
(83,3,'view_player_profile','{\"target\": {\"player_id\": 272, \"steamid\": \"76561199132911357\", \"name\": \"pakepko_j\"}}','2026-04-20 12:13:08'),
(84,3,'view_player_profile','{\"target\": {\"player_id\": 77, \"steamid\": \"76561199016856843\", \"name\": \"\\u0425\\u0440\\u0438\\u0441\\u0442\\u043e\\u0444\\u043e\\u0440 \\u0413\\u0430\\u0439\\u043a\\u0438\\u043d\"}}','2026-04-20 12:14:00'),
(85,3,'view_player_profile','{\"target\": {\"player_id\": 881, \"steamid\": \"76561198300593777\", \"name\": \"Plaga Medicus\"}}','2026-04-20 12:14:17'),
(86,3,'view_player_profile','{\"target\": {\"player_id\": 55, \"steamid\": \"76561199220215839\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u041c\\u0430\\u0440\\u043a\\u0443\\u0441 \\u0411\\u0435\\u043b\\u043e\\u0432\"}}','2026-04-20 12:18:19'),
(87,3,'view_player_profile','{\"target\": {\"player_id\": 56, \"steamid\": \"76561199729584441\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 2-\\u0441\\u0442 \\u0412\\u0435\\u0440\\u043d\\u0435\\u0440 \\u041c\\u0430\\u0432\\u0440\\u0438\\u043d\"}}','2026-04-20 12:19:08'),
(88,3,'view_player_profile','{\"target\": {\"player_id\": 168, \"steamid\": \"76561199805575597\", \"name\": \"\\u0410\\u0440\\u0442\\u0435\\u043c \\u0421\\u0430\\u0432\\u0447\\u0435\\u043d\\u043a\\u043e\\u0432\"}}','2026-04-20 12:20:42'),
(89,3,'update_player','{\"target\": {\"player_id\": 168, \"steamid\": \"76561199805575597\", \"name\": \"\\u0410\\u0440\\u0442\\u0435\\u043c \\u0421\\u0430\\u0432\\u0447\\u0435\\u043d\\u043a\\u043e\\u0432\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 2, \"to\": 5}, {\"field\": \"kit_level\", \"from\": 2, \"to\": 3}]}','2026-04-20 12:20:50'),
(90,3,'view_player_profile','{\"target\": {\"player_id\": 168, \"steamid\": \"76561199805575597\", \"name\": \"\\u0410\\u0440\\u0442\\u0435\\u043c \\u0421\\u0430\\u0432\\u0447\\u0435\\u043d\\u043a\\u043e\\u0432\"}}','2026-04-20 12:20:50'),
(91,3,'view_player_profile','{\"target\": {\"player_id\": 46, \"steamid\": \"76561199067358956\", \"name\": \"\\u041b\\u0443\\u043d\\u0430\"}}','2026-04-20 12:20:56'),
(92,3,'update_player','{\"target\": {\"player_id\": 46, \"steamid\": \"76561199067358956\", \"name\": \"\\u041b\\u0443\\u043d\\u0430\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 2, \"to\": 5}, {\"field\": \"kit_level\", \"from\": 2, \"to\": 3}]}','2026-04-20 12:21:00'),
(93,3,'view_player_profile','{\"target\": {\"player_id\": 46, \"steamid\": \"76561199067358956\", \"name\": \"\\u041b\\u0443\\u043d\\u0430\"}}','2026-04-20 12:21:01'),
(94,3,'view_player_profile','{\"target\": {\"player_id\": 52, \"steamid\": \"76561198813594310\", \"name\": \"\\u0414\\u044e\\u0432\\u0430\\u043b\\u044c\"}}','2026-04-20 12:21:10'),
(95,3,'update_player','{\"target\": {\"player_id\": 52, \"steamid\": \"76561198813594310\", \"name\": \"\\u0414\\u044e\\u0432\\u0430\\u043b\\u044c\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 1, \"to\": 5}, {\"field\": \"kit_level\", \"from\": 1, \"to\": 3}]}','2026-04-20 12:21:21'),
(96,3,'view_player_profile','{\"target\": {\"player_id\": 52, \"steamid\": \"76561198813594310\", \"name\": \"\\u0414\\u044e\\u0432\\u0430\\u043b\\u044c\"}}','2026-04-20 12:21:21'),
(97,3,'view_player_profile','{\"target\": {\"player_id\": 2, \"steamid\": \"76561198289354854\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0421\\u0435\\u0440\\u0433\\u0435\\u0439 \\u0410\\u0432\\u0435\\u0440\\u0438\\u043d\"}}','2026-04-20 13:26:05'),
(98,3,'update_player','{\"target\": {\"player_id\": 2, \"steamid\": \"76561198289354854\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0421\\u0435\\u0440\\u0433\\u0435\\u0439 \\u0410\\u0432\\u0435\\u0440\\u0438\\u043d\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 9}]}','2026-04-20 13:26:12'),
(99,3,'view_player_profile','{\"target\": {\"player_id\": 2, \"steamid\": \"76561198289354854\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0421\\u0435\\u0440\\u0433\\u0435\\u0439 \\u0410\\u0432\\u0435\\u0440\\u0438\\u043d\"}}','2026-04-20 13:26:12'),
(100,3,'view_player_profile','{\"target\": {\"player_id\": 1, \"steamid\": \"76561198297267585\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440 \\u0410\\u043d\\u0434\\u0440\\u043e\\u0441\\u043e\\u0432\"}}','2026-04-20 13:26:16'),
(101,3,'update_player','{\"target\": {\"player_id\": 1, \"steamid\": \"76561198297267585\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440 \\u0410\\u043d\\u0434\\u0440\\u043e\\u0441\\u043e\\u0432\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 7}]}','2026-04-20 13:26:19'),
(102,3,'view_player_profile','{\"target\": {\"player_id\": 1, \"steamid\": \"76561198297267585\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440 \\u0410\\u043d\\u0434\\u0440\\u043e\\u0441\\u043e\\u0432\"}}','2026-04-20 13:26:19'),
(103,3,'view_player_profile','{\"target\": {\"player_id\": 1, \"steamid\": \"76561198297267585\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440 \\u0410\\u043d\\u0434\\u0440\\u043e\\u0441\\u043e\\u0432\"}}','2026-04-20 13:26:27'),
(104,3,'update_player','{\"target\": {\"player_id\": 1, \"steamid\": \"76561198297267585\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440 \\u0410\\u043d\\u0434\\u0440\\u043e\\u0441\\u043e\\u0432\"}, \"changes\": [{\"field\": \"kit_level\", \"from\": 3, \"to\": 4}]}','2026-04-20 13:26:30'),
(105,3,'view_player_profile','{\"target\": {\"player_id\": 1, \"steamid\": \"76561198297267585\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440 \\u0410\\u043d\\u0434\\u0440\\u043e\\u0441\\u043e\\u0432\"}}','2026-04-20 13:26:30'),
(106,3,'view_player_profile','{\"target\": {\"player_id\": 3, \"steamid\": \"76561199808918929\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 2-\\u0441\\u0442 \\u0424\\u0438\\u0437\\u0437\\u0438 \\u0423\\u043e\\u043b\\u043a\\u0435\\u0440\"}}','2026-04-20 13:26:35'),
(107,3,'update_player','{\"target\": {\"player_id\": 3, \"steamid\": \"76561199808918929\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 2-\\u0441\\u0442 \\u0424\\u0438\\u0437\\u0437\\u0438 \\u0423\\u043e\\u043b\\u043a\\u0435\\u0440\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 8}]}','2026-04-20 13:26:38'),
(108,3,'view_player_profile','{\"target\": {\"player_id\": 3, \"steamid\": \"76561199808918929\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 2-\\u0441\\u0442 \\u0424\\u0438\\u0437\\u0437\\u0438 \\u0423\\u043e\\u043b\\u043a\\u0435\\u0440\"}}','2026-04-20 13:26:38'),
(109,3,'view_player_profile','{\"target\": {\"player_id\": 13, \"steamid\": \"76561199099536012\", \"name\": \"\\u0441dio\"}}','2026-04-20 13:27:05'),
(110,3,'update_player','{\"target\": {\"player_id\": 13, \"steamid\": \"76561199099536012\", \"name\": \"\\u0441dio\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 8}]}','2026-04-20 13:27:10'),
(111,3,'view_player_profile','{\"target\": {\"player_id\": 13, \"steamid\": \"76561199099536012\", \"name\": \"\\u0441dio\"}}','2026-04-20 13:27:10'),
(112,3,'view_player_profile','{\"target\": {\"player_id\": 24, \"steamid\": \"76561199093198159\", \"name\": \"\\u0410\\u0439\\u0441\\u043b\\u0430\\u0442\"}}','2026-04-20 13:27:19'),
(113,3,'view_player_profile','{\"target\": {\"player_id\": 35, \"steamid\": \"76561199366368424\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u041c\\u0438\\u0445\\u0430\\u0438\\u043b \\u0420\\u043e\\u043c\\u0430\\u043d\\u043e\\u0432\"}}','2026-04-20 13:27:41'),
(114,3,'view_player_profile','{\"target\": {\"player_id\": 50, \"steamid\": \"76561198964710593\", \"name\": \"\\u0421\\u043e\\u043b\\u043d\\u0446\\u0435\\u0432\"}}','2026-04-20 13:28:09'),
(115,3,'update_player','{\"target\": {\"player_id\": 50, \"steamid\": \"76561198964710593\", \"name\": \"\\u0421\\u043e\\u043b\\u043d\\u0446\\u0435\\u0432\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 2, \"to\": 3}]}','2026-04-20 13:28:14'),
(116,3,'view_player_profile','{\"target\": {\"player_id\": 50, \"steamid\": \"76561198964710593\", \"name\": \"\\u0421\\u043e\\u043b\\u043d\\u0446\\u0435\\u0432\"}}','2026-04-20 13:28:14'),
(117,3,'update_player','{\"target\": {\"player_id\": 35, \"steamid\": \"76561199366368424\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u041c\\u0438\\u0445\\u0430\\u0438\\u043b \\u0420\\u043e\\u043c\\u0430\\u043d\\u043e\\u0432\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 1}]}','2026-04-20 13:28:19'),
(118,3,'view_player_profile','{\"target\": {\"player_id\": 35, \"steamid\": \"76561199366368424\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u041c\\u0438\\u0445\\u0430\\u0438\\u043b \\u0420\\u043e\\u043c\\u0430\\u043d\\u043e\\u0432\"}}','2026-04-20 13:28:19'),
(119,3,'update_player','{\"target\": {\"player_id\": 24, \"steamid\": \"76561199093198159\", \"name\": \"\\u0410\\u0439\\u0441\\u043b\\u0430\\u0442\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 7}]}','2026-04-20 13:28:23'),
(120,3,'view_player_profile','{\"target\": {\"player_id\": 24, \"steamid\": \"76561199093198159\", \"name\": \"\\u0410\\u0439\\u0441\\u043b\\u0430\\u0442\"}}','2026-04-20 13:28:23'),
(121,3,'view_player_profile','{\"target\": {\"player_id\": 54, \"steamid\": \"76561199857161978\", \"name\": \"Lemm\"}}','2026-04-20 13:28:36'),
(122,3,'update_player','{\"target\": {\"player_id\": 54, \"steamid\": \"76561199857161978\", \"name\": \"Lemm\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 1, \"to\": 2}, {\"field\": \"kit_level\", \"from\": 1, \"to\": 2}]}','2026-04-20 13:28:48'),
(123,3,'view_player_profile','{\"target\": {\"player_id\": 54, \"steamid\": \"76561199857161978\", \"name\": \"Lemm\"}}','2026-04-20 13:28:48'),
(124,3,'view_player_profile','{\"target\": {\"player_id\": 100, \"steamid\": \"76561198019784942\", \"name\": \"\\u0421\\u0435\\u0440\\u0430\\u0444\\u0438\\u043c \\u0410\\u043b\\u0430\\u043a\\u0441\\u0430\\u0439\\u0442\"}}','2026-04-20 13:30:08'),
(125,3,'view_player_profile','{\"target\": {\"player_id\": 54, \"steamid\": \"76561199857161978\", \"name\": \"Lemm\"}}','2026-04-20 13:30:13'),
(126,3,'update_player','{\"target\": {\"player_id\": 100, \"steamid\": \"76561198019784942\", \"name\": \"\\u0421\\u0435\\u0440\\u0430\\u0444\\u0438\\u043c \\u0410\\u043b\\u0430\\u043a\\u0441\\u0430\\u0439\\u0442\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 1, \"to\": 0}]}','2026-04-20 13:30:19'),
(127,3,'view_player_profile','{\"target\": {\"player_id\": 100, \"steamid\": \"76561198019784942\", \"name\": \"\\u0421\\u0435\\u0440\\u0430\\u0444\\u0438\\u043c \\u0410\\u043b\\u0430\\u043a\\u0441\\u0430\\u0439\\u0442\"}}','2026-04-20 13:30:19'),
(128,3,'view_player_profile','{\"target\": {\"player_id\": 100, \"steamid\": \"76561198019784942\", \"name\": \"\\u0421\\u0435\\u0440\\u0430\\u0444\\u0438\\u043c \\u0410\\u043b\\u0430\\u043a\\u0441\\u0430\\u0439\\u0442\"}}','2026-04-20 13:30:21'),
(129,3,'view_player_profile','{\"target\": {\"player_id\": 106, \"steamid\": \"76561198821450666\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 \\u0421\\u0442\\u0435\\u043f\\u0430\\u043d \\u0425\\u0432\\u043e\\u0440\\u044c\"}}','2026-04-20 13:30:31'),
(130,3,'view_player_profile','{\"target\": {\"player_id\": 184, \"steamid\": \"76561198865946520\", \"name\": \"Jerolock\"}}','2026-04-20 13:31:12'),
(131,3,'view_player_profile','{\"target\": {\"player_id\": 242, \"steamid\": \"76561198290070142\", \"name\": \"MyrlinMyrlo\"}}','2026-04-20 13:32:01'),
(132,3,'view_player_profile','{\"target\": {\"player_id\": 268, \"steamid\": \"76561199128813788\", \"name\": \"\\u041a\\u0440\\u0430\\u0431\"}}','2026-04-20 13:32:20'),
(133,3,'view_player_profile','{\"target\": {\"player_id\": 369, \"steamid\": \"76561198009099979\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 \\u0412\\u0435\\u043b\\u0435\\u0441\"}}','2026-04-20 13:33:26'),
(134,3,'update_player','{\"target\": {\"player_id\": 369, \"steamid\": \"76561198009099979\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 \\u0412\\u0435\\u043b\\u0435\\u0441\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 2}, {\"field\": \"kit_level\", \"from\": 1, \"to\": 2}]}','2026-04-20 13:33:33'),
(135,3,'view_player_profile','{\"target\": {\"player_id\": 369, \"steamid\": \"76561198009099979\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 \\u0412\\u0435\\u043b\\u0435\\u0441\"}}','2026-04-20 13:33:33'),
(136,3,'update_player','{\"target\": {\"player_id\": 268, \"steamid\": \"76561199128813788\", \"name\": \"\\u041a\\u0440\\u0430\\u0431\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 1}, {\"field\": \"kit_level\", \"from\": 0, \"to\": 1}]}','2026-04-20 13:34:02'),
(137,3,'view_player_profile','{\"target\": {\"player_id\": 268, \"steamid\": \"76561199128813788\", \"name\": \"\\u041a\\u0440\\u0430\\u0431\"}}','2026-04-20 13:34:03'),
(138,3,'update_player','{\"target\": {\"player_id\": 242, \"steamid\": \"76561198290070142\", \"name\": \"MyrlinMyrlo\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 1}]}','2026-04-20 13:34:08'),
(139,3,'view_player_profile','{\"target\": {\"player_id\": 242, \"steamid\": \"76561198290070142\", \"name\": \"MyrlinMyrlo\"}}','2026-04-20 13:34:08'),
(140,3,'update_player','{\"target\": {\"player_id\": 184, \"steamid\": \"76561198865946520\", \"name\": \"Jerolock\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 1}]}','2026-04-20 13:34:11'),
(141,3,'view_player_profile','{\"target\": {\"player_id\": 184, \"steamid\": \"76561198865946520\", \"name\": \"Jerolock\"}}','2026-04-20 13:34:12'),
(142,3,'update_player','{\"target\": {\"player_id\": 106, \"steamid\": \"76561198821450666\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 \\u0421\\u0442\\u0435\\u043f\\u0430\\u043d \\u0425\\u0432\\u043e\\u0440\\u044c\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 1}]}','2026-04-20 13:34:14'),
(143,3,'view_player_profile','{\"target\": {\"player_id\": 106, \"steamid\": \"76561198821450666\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 \\u0421\\u0442\\u0435\\u043f\\u0430\\u043d \\u0425\\u0432\\u043e\\u0440\\u044c\"}}','2026-04-20 13:34:15'),
(144,3,'view_player_profile','{\"target\": {\"player_id\": 387, \"steamid\": \"76561199244498592\", \"name\": \"\\u0410\\u0440\\u0441\\u0435\\u043d\"}}','2026-04-20 13:34:28'),
(145,3,'view_player_profile','{\"target\": {\"player_id\": 369, \"steamid\": \"76561198009099979\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 \\u0412\\u0435\\u043b\\u0435\\u0441\"}}','2026-04-20 13:34:32'),
(146,3,'update_player','{\"target\": {\"player_id\": 387, \"steamid\": \"76561199244498592\", \"name\": \"\\u0410\\u0440\\u0441\\u0435\\u043d\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 1}]}','2026-04-20 13:34:35'),
(147,3,'view_player_profile','{\"target\": {\"player_id\": 387, \"steamid\": \"76561199244498592\", \"name\": \"\\u0410\\u0440\\u0441\\u0435\\u043d\"}}','2026-04-20 13:34:35'),
(148,3,'view_player_profile','{\"target\": {\"player_id\": 421, \"steamid\": \"76561199407695375\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 2-\\u0441\\u0442 \\u041d\\u0438\\u043a\\u0438\\u0442\\u0430 \\u0422\\u0440\\u043e\\u043d\\u0438\\u043d\"}}','2026-04-20 13:35:01'),
(149,3,'update_player','{\"target\": {\"player_id\": 421, \"steamid\": \"76561199407695375\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 2-\\u0441\\u0442 \\u041d\\u0438\\u043a\\u0438\\u0442\\u0430 \\u0422\\u0440\\u043e\\u043d\\u0438\\u043d\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 1}]}','2026-04-20 13:35:45'),
(150,3,'view_player_profile','{\"target\": {\"player_id\": 421, \"steamid\": \"76561199407695375\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 2-\\u0441\\u0442 \\u041d\\u0438\\u043a\\u0438\\u0442\\u0430 \\u0422\\u0440\\u043e\\u043d\\u0438\\u043d\"}}','2026-04-20 13:35:45'),
(151,3,'view_player_profile','{\"target\": {\"player_id\": 482, \"steamid\": \"76561199101031638\", \"name\": \"Woolchokus\"}}','2026-04-20 13:36:02'),
(152,3,'view_player_profile','{\"target\": {\"player_id\": 533, \"steamid\": \"76561199186701625\", \"name\": \"FyLs\"}}','2026-04-20 13:36:15'),
(153,3,'view_player_profile','{\"target\": {\"player_id\": 537, \"steamid\": \"76561198892131951\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 \\u0421\\u0442\\u0440\\u043e\\u0442\\u0435\\u0433\"}}','2026-04-20 13:36:18'),
(154,3,'update_player','{\"target\": {\"player_id\": 533, \"steamid\": \"76561199186701625\", \"name\": \"FyLs\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 1}, {\"field\": \"kit_level\", \"from\": 0, \"to\": 1}]}','2026-04-20 13:36:37'),
(155,3,'view_player_profile','{\"target\": {\"player_id\": 533, \"steamid\": \"76561199186701625\", \"name\": \"FyLs\"}}','2026-04-20 13:36:37'),
(156,3,'update_player','{\"target\": {\"player_id\": 482, \"steamid\": \"76561199101031638\", \"name\": \"Woolchokus\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 1}]}','2026-04-20 13:36:43'),
(157,3,'view_player_profile','{\"target\": {\"player_id\": 482, \"steamid\": \"76561199101031638\", \"name\": \"Woolchokus\"}}','2026-04-20 13:36:43'),
(158,3,'view_player_profile','{\"target\": {\"player_id\": 720, \"steamid\": \"76561198965873863\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 \\u0421\\u0430\\u0442\\u043e\\u0440\\u0443 \\u0413\\u043e\\u0434\\u0436\\u043e\"}}','2026-04-20 13:38:52'),
(159,3,'update_player','{\"target\": {\"player_id\": 720, \"steamid\": \"76561198965873863\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 \\u0421\\u0430\\u0442\\u043e\\u0440\\u0443 \\u0413\\u043e\\u0434\\u0436\\u043e\"}, \"changes\": [{\"field\": \"rank_id\", \"from\": 0, \"to\": 1}]}','2026-04-20 13:42:20'),
(160,3,'view_player_profile','{\"target\": {\"player_id\": 720, \"steamid\": \"76561198965873863\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 \\u0421\\u0430\\u0442\\u043e\\u0440\\u0443 \\u0413\\u043e\\u0434\\u0436\\u043e\"}}','2026-04-20 13:42:21'),
(161,3,'view_player_profile','{\"target\": {\"player_id\": 55, \"steamid\": \"76561199220215839\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u041c\\u0430\\u0440\\u043a\\u0443\\u0441 \\u0411\\u0435\\u043b\\u043e\\u0432\"}}','2026-04-20 14:19:18'),
(162,3,'view_player_profile','{\"target\": {\"player_id\": 55, \"steamid\": \"76561199220215839\", \"name\": \"\\u041c\\u0430\\u0442\\u0440\\u043e\\u0441 1-\\u0441\\u0442 \\u041c\\u0430\\u0440\\u043a\\u0443\\u0441 \\u0411\\u0435\\u043b\\u043e\\u0432\"}}','2026-04-20 14:51:45'),
(163,6,'login','{\"method\": \"discord_oauth\"}','2026-04-20 19:14:08'),
(164,6,'view_player_profile','{\"target\": {\"player_id\": 575, \"steamid\": \"76561199052213855\", \"name\": \"\\u041f\\u0435\\u0442\\u044f20777\"}}','2026-04-20 19:17:13'),
(165,3,'view_player_profile','{\"target\": {\"player_id\": 164, \"steamid\": \"76561199091865079\", \"name\": \"kst\"}}','2026-04-21 14:25:05'),
(166,3,'view_player_profile','{\"target\": {\"player_id\": 610, \"steamid\": \"76561198770065800\", \"name\": \"KvOrtIx\"}}','2026-04-21 20:07:02'),
(167,6,'login','{\"method\": \"discord_oauth\"}','2026-04-23 17:36:54'),
(168,1,'login','{\"method\": \"discord_oauth\"}','2026-04-23 23:02:20'),
(169,6,'view_player_profile','{\"target\": {\"player_id\": 164, \"steamid\": \"76561199091865079\", \"name\": \"kst\"}}','2026-04-24 16:17:22'),
(170,6,'view_player_profile','{\"target\": {\"player_id\": 164, \"steamid\": \"76561199091865079\", \"name\": \"kst\"}}','2026-04-24 16:17:28');
/*!40000 ALTER TABLE `admin_action_logs` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `admin_sessions`
--

DROP TABLE IF EXISTS `admin_sessions`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `admin_sessions` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `admin_id` int(11) DEFAULT NULL,
  `token` varchar(128) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `expires_at` datetime DEFAULT NULL,
  `revoked` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_admin_sessions_token` (`token`),
  KEY `ix_admin_sessions_id` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=18 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `admin_sessions`
--

LOCK TABLES `admin_sessions` WRITE;
/*!40000 ALTER TABLE `admin_sessions` DISABLE KEYS */;
INSERT INTO `admin_sessions` VALUES
(1,1,'O-g1xRX4e1ok97G-BD7JXg6X6QmcZRqkqthaL-v4aUo','2026-03-23 19:08:53','2026-03-30 19:08:53',1),
(2,1,'JzcvhXBoeb1keNqxipruKXdpx5IHQVYAC8X_9VOrvJQ','2026-03-23 19:23:20','2026-03-30 19:23:20',1),
(3,3,'1dCDwoj1T905C3iKARMKd-afZuRLKpJij4WcMrm2l6s','2026-03-23 20:17:40','2026-03-30 20:17:40',0),
(4,1,'llGNVDvKDQbeV_DxOg0AyKTatRcQFEKdGUA4hOUXK6c','2026-03-23 20:20:23','2026-03-30 20:20:23',0),
(5,6,'4jnfRVGju90oQ3NqVHwN6t_PgYD4e5ZQ9Q_29pEUJYs','2026-03-25 17:13:15','2026-04-01 17:13:15',0),
(6,1,'FTy1AhoZYMuyudcN23A7jMWPeF97C2BLWOBs3PvOEaA','2026-03-26 11:20:04','2026-04-02 11:20:04',0),
(7,6,'xwik9SwZ8Jl7iF-j4F8qKBmdBsOxdb4RyD2FLjNUaic','2026-03-30 07:44:42','2026-04-06 07:44:42',0),
(8,1,'H62yGLocwT2U_18ZBR_GEp-EHSrpCPQv8HZA7nmiarU','2026-03-31 19:39:44','2026-04-07 19:39:44',0),
(9,6,'UAt5k8iQBcBONBFdAX3lbFkJLnNau88ojk5h8Ypywpk','2026-04-05 09:36:28','2026-04-12 09:36:28',0),
(10,6,'njtizTV1hORRlR2IDI4B2MXnx8hWSq28oZcbrE5PMss','2026-04-07 13:03:18','2026-04-14 13:03:18',0),
(11,6,'I13fAeBa8i5TcONQmUqqXkhX_jWcAeC6TNrT5KmVczE','2026-04-09 16:52:58','2026-04-16 16:52:58',0),
(12,3,'33xxvdQLJAfYgIQxMIQQd7aX3Hn3cVzA6E0c3AcKOwk','2026-04-09 20:23:19','2026-04-16 20:23:19',0),
(13,1,'WLOQUwA6gtrPm-38fiPbijUfZEzYQdbyZKDHdBz9Uok','2026-04-16 12:36:28','2026-04-23 12:36:28',0),
(14,3,'naz-mbGJyOsviOUsO5sB0jY7pByg2vIpl4Ar35UzfPE','2026-04-20 09:07:18','2026-04-27 09:07:18',0),
(15,6,'IAJXX_ai2g6VnZpzauzDO5EY2HVHLDwW9-J4ruMzthw','2026-04-20 16:14:08','2026-04-27 16:14:08',0),
(16,6,'kbiv2G3ByhIvCNxZvMg2OXuHKh_9jERMuzgr5l3IJZo','2026-04-23 14:36:54','2026-04-30 14:36:54',0),
(17,1,'OCvzLL2ZTDKKKL-kL94NFK9BqHytftDsxbxzCYzb1BM','2026-04-23 20:02:20','2026-04-30 20:02:20',0);
/*!40000 ALTER TABLE `admin_sessions` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `admin_users`
--

DROP TABLE IF EXISTS `admin_users`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `admin_users` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `discord_id` varchar(50) DEFAULT NULL,
  `discord_username` varchar(255) DEFAULT NULL,
  `discord_avatar` varchar(255) DEFAULT NULL,
  `role` varchar(50) DEFAULT NULL,
  `permissions` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`permissions`)),
  `server_permissions` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT '[]' CHECK (json_valid(`server_permissions`)),
  `created_at` datetime DEFAULT NULL,
  `last_login` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_admin_users_discord_id` (`discord_id`),
  KEY `ix_admin_users_id` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=7 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `admin_users`
--

LOCK TABLES `admin_users` WRITE;
/*!40000 ALTER TABLE `admin_users` DISABLE KEYS */;
INSERT INTO `admin_users` VALUES
(1,'406096773052891136','icebergdem','4dc93fb4a3e3028077985f39b9257c32','Администратор','[\"read\", \"write\", \"admin\"]','[]','2026-03-22 20:24:20','2026-04-23 20:02:20'),
(3,'987047045498011658','User_987047045498011658',NULL,'Администратор','[\"read\", \"write\", \"admin\"]','[]','2026-03-22 22:05:43','2026-04-20 09:07:18'),
(6,'800404741175246848','User_800404741175246848',NULL,'Управляющий','[\"read\", \"write\"]','[]','2026-03-23 21:08:58','2026-04-23 14:36:54');
/*!40000 ALTER TABLE `admin_users` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `inspector_messages`
--

DROP TABLE IF EXISTS `inspector_messages`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `inspector_messages` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `project_id` int(10) unsigned NOT NULL,
  `channel_id` bigint(20) unsigned NOT NULL,
  `message_id` bigint(20) unsigned NOT NULL,
  `kind` varchar(32) NOT NULL DEFAULT 'for_review',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_inspector_project` (`project_id`),
  KEY `idx_inspector_message` (`message_id`)
) ENGINE=InnoDB AUTO_INCREMENT=66 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `inspector_messages`
--

LOCK TABLES `inspector_messages` WRITE;
/*!40000 ALTER TABLE `inspector_messages` DISABLE KEYS */;
INSERT INTO `inspector_messages` VALUES
(5,5,1475045540579512602,1478389204575850527,'tech','2026-03-03 13:50:54'),
(23,10,1475045540579512602,1480261744462135328,'tech','2026-03-08 17:51:42'),
(31,9,1475045540579512602,1480906282511630468,'tech','2026-03-10 12:32:52'),
(42,13,1475045540579512602,1481193820052787334,'tech','2026-03-11 07:35:27'),
(46,12,1475045540579512602,1481237930138927115,'tech','2026-03-11 10:30:43'),
(49,2,1475045540579512602,1481238813807349782,'tech','2026-03-11 10:34:14'),
(52,14,1475045540579512602,1481296141051236402,'tech','2026-03-11 14:22:02'),
(53,4,1475045540579512602,1481609729997410336,'tech','2026-03-12 11:08:07'),
(54,8,1475045540579512602,1481621882095534212,'tech','2026-03-12 11:56:25'),
(62,15,1475045540579512602,1487742242692272198,'tech','2026-03-29 09:16:32'),
(64,3,1475045540579512602,1487745911026090054,'tech','2026-03-29 09:31:07');
/*!40000 ALTER TABLE `inspector_messages` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `kits`
--

DROP TABLE IF EXISTS `kits`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `kits` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `job_id` varchar(15) DEFAULT NULL,
  `kit_level` int(11) DEFAULT NULL,
  `items` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`items`)),
  PRIMARY KEY (`id`),
  KEY `ix_kits_id` (`id`),
  KEY `ix_kits_job_id` (`job_id`)
) ENGINE=InnoDB AUTO_INCREMENT=74 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `kits`
--

LOCK TABLES `kits` WRITE;
/*!40000 ALTER TABLE `kits` DISABLE KEYS */;
INSERT INTO `kits` VALUES
(7,'captain',0,'{\r\n  \"headset\": 1,\r\n  \"commonerclothes2\": 1,\r\n  \"cigar\": 1,\r\n  \"revolver\": {\r\n    \"nested\": {\r\n      \"revolverround\": 6\r\n    }\r\n  },\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"captainscap3\": 1,\r\n  \"handheldterminal\": 1\r\n}'),
(8,'engineer',0,'{\r\n  \"screwdriver\": 1,\r\n  \"wrench\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"bluewire\": 4,\r\n  \"headset\": 1,\r\n  \"minerclothes\": 1,\r\n  \"baseballcap\": 1\r\n}'),
(9,'mechanic',0,'{\r\n  \"wrench\": 1,\r\n  \"screwdriver\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n \r\n  \"weldingtool\": {\r\n    \"nested\": {\r\n      \"weldingfueltank\": 1\r\n    }\r\n  },\r\n\r\n  \"headset\": 1,\r\n  \"watchmanclothes\": 1,\r\n  \"baseballcap\": 1\r\n\r\n}'),
(10,'securityofficer',0,'{\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"stungun\": 1,\r\n  \"stungundart\": 2,\r\n  \"bandage\": 2,\r\n  \"headset\": 1,\r\n  \"securitypatrolclothes\": 1,\r\n  \"baseballcap\": 1\r\n}'),
(11,'medicaldoctor',0,'{\r\n  \"pomegrenadeextract \": 2,\r\n  \"headset\": 1,\r\n  \"medicseparatistsuniform2\": {\r\n    \"nested\": {\r\n      \"bandage\": 4,\r\n      \"opium\": 4,\r\n      \"antibiotics\": 1\r\n    }\r\n  },\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"baseballcap\": 1\r\n}'),
(12,'assistant',0,'{\r\n  \"headset\": 1,\r\n  \"quartermasterclothes\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"baseballcap\": 1\r\n}'),
(13,'captain',1,'{\r\n  \"headset\": 1,\r\n  \"captainsuniform2\": 1,\r\n  \"cigar\": 2,\r\n  \"revolver\": {\r\n    \"nested\": {\r\n      \"revolverround\": 6\r\n    }\r\n  },\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"captainscap3\": 1,\r\n  \"handheldterminal\": 1\r\n}'),
(14,'engineer',1,'{\r\n  \"screwdriver\": 1,\r\n  \"wrench\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"bandage\": 2,\r\n  \"bluewire\": 6,\r\n  \"headset\": 1,\r\n  \"orangejumpsuit2\": 1,\r\n  \"captainscap2\": 1\r\n}'),
(15,'mechanic',1,'{\r\n  \"wrench\": 1,\r\n  \"screwdriver\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \r\n  \"weldingtool\": {\r\n    \"nested\": {\r\n      \"weldingfueltank\": 1\r\n    }\r\n  },\r\n  \"bandage\": 2,\r\n  \"plasmacutter\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"headset\": 1,\r\n  \"bluejumpsuit2\": 1,\r\n  \"captainscap2\": 1\r\n\r\n}'),
(16,'securityofficer',1,'{\r\n  \"smgunique\": 1,\r\n  \"smgmagazine\": 2,\r\n  \"divingknife\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n\r\n  \"bandage\": 2,\r\n  \"headset\": 1,\r\n  \"securityseparatistsuniform2\": 1,\r\n  \"captainsseparatistcap2\": 1\r\n}'),
(17,'medicaldoctor',1,'{\r\n  \"deusizine\": 1,\r\n  \"opium\": 2,\r\n  \"antibiotics\": 2,\r\n  \"headset\": 1,\r\n  \"doctorsuniform2\": {\r\n    \"nested\": {\r\n      \"bandage\": 4,\r\n      \"antidama1\": 2,\r\n      \"antibloodloss1\": 4\r\n    }\r\n  },\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"captainscap2\": 1\r\n}'),
(18,'assistant',1,'{\r\n  \"headset\": 1,\r\n  \"assistantclothes\": 1,\r\n  \"cigar\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"captainscap2\": 1\r\n}'),
(19,'captain',2,'{\r\n  \"headset\": 1,\r\n  \"crewchiefclothes\": 1,\r\n  \"captainspipe\": {\r\n    \"nested\": {\r\n      \"pipetobacco\": 1\r\n    }\r\n  },\r\n  \"revolver\": {\r\n    \"nested\": {\r\n      \"revolverround\": 6\r\n    }\r\n  },\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"captainscap3\": 1,\r\n  \"handheldterminal\": 1\r\n}'),
(20,'engineer',2,'{\r\n  \"screwdriver\": 1,\r\n  \"wrench\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"bandage\": 2,\r\n  \"bluewire\": 6,\r\n  \"headset\": 1,\r\n  \"engineerseparatistsuniform2\": 1,\r\n  \"captainscap2\": 1\r\n\r\n}'),
(21,'mechanic',2,'{\r\n  \"wrench\": 1,\r\n  \"screwdriver\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \r\n  \"weldingtool\": {\r\n    \"nested\": {\r\n      \"weldingfueltank\": 1\r\n    }\r\n  },\r\n\r\n  \"bandage\": 3,\r\n  \r\n  \"plasmacutter\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n      },\r\n\r\n  \"weldingfueltank\": 1,\r\n  \"oxygentank\": 1,\r\n  \"headset\": 1,\r\n  \"bluejumpsuit1\": 1,\r\n  \"captainscap2\": 1\r\n\r\n}'),
(22,'securityofficer',2,'{\r\n  \"smg\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n\r\n  \"divingknife\": 1,\r\n  \"bandolier\": {\r\n    \"nested\": {\r\n      \"smgmagazine\": 4\r\n    }\r\n  },\r\n  \"hyperzine\": 1,\r\n  \"bandage\": 2,\r\n  \"headset\": 1,\r\n  \"securityseparatistsuniform3\": 1,\r\n  \"captainsseparatistcap2\": 1\r\n}'),
(24,'assistant',2,'{\r\n  \"headset\": 1,\r\n  \"administratorclothes\": 1,\r\n  \"cigar\": 2,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"captainscap1\": 1\r\n}'),
(25,'captain',4,'{\r\n  \"headset\": 1,\r\n  \"vipclothes2\": 1,\r\n  \"captainspipe\": {\r\n    \"nested\": {\r\n      \"pipetobacco\": 1\r\n    }\r\n  },\r\n  \"revolver\": {\r\n    \"nested\": {\r\n      \"revolverround\": 6\r\n    }\r\n  },\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"captainscap3\": 1,\r\n  \"handheldterminal\": 1\r\n}'),
(32,'captain',3,'{\r\n  \"headset\": 1,\r\n  \"captainsuniform1\": 1,\r\n  \"captainspipe\": {\r\n    \"nested\": {\r\n      \"pipetobacco\": 1\r\n    }\r\n  },\r\n  \"revolver\": {\r\n    \"nested\": {\r\n      \"revolverround\": 6\r\n    }\r\n  },\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"captainscap3\": 1,\r\n  \"handheldterminal\": 1\r\n}'),
(33,'mechanic',3,'{\r\n  \"wrench\": 1,\r\n  \"screwdriver\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \r\n  \"weldingtool\": {\r\n    \"nested\": {\r\n      \"weldingfueltank\": 1\r\n    }\r\n  },\r\n\r\n  \"bandage\": 3,\r\n  \r\n  \"plasmacutter\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n      },\r\n\r\n  \"weldingfueltank\": 1,\r\n  \"oxygentank\": 1,\r\n  \"headset\": 1,\r\n  \"mechanicseparatistsuniform2\": 1,\r\n  \"captainscap2\": 1\r\n\r\n}'),
(35,'mechanic',4,'{\r\n  \"wrench\": 1,\r\n  \"screwdriver\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \r\n  \"weldingtool\": {\r\n    \"nested\": {\r\n      \"weldingfueltank\": 1\r\n    }\r\n  },\r\n\r\n  \"bandage\": 3,\r\n  \r\n  \"plasmacutter\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n      },\r\n\r\n  \"weldingfueltank\": 1,\r\n  \"oxygentank\": 1,\r\n  \"headset\": 1,\r\n  \"mechanicseparatistsuniform1\": 1,\r\n  \"captainscap2\": 1\r\n\r\n}'),
(43,'engineer',3,'{\r\n  \"screwdriver\": 1,\r\n  \"wrench\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"bandage\": 2,\r\n  \"bluewire\": 6,\r\n  \"headset\": 1,\r\n  \"orangejumpsuit1\": 1,\r\n  \"captainscap2\": 1\r\n}'),
(44,'engineer',4,'{\n  \"screwdriver\": 1,\n  \"wrench\": 1,\n  \"divingmask\": {\n    \"nested\": {\n      \"oxygentank\": 1\n    }\n  },\n  \"bandage\": 2,\n  \"bluewire\": 6,\n  \"headset\": 1,\n  \"engineerseparatistsuniform1\": 1,\n  \"captainscap2\": 1\n}'),
(51,'securityofficer',3,'{\r\n  \"smg\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n\r\n  \"divingknife\": 1,\r\n  \"bandolier\": {\r\n    \"nested\": {\r\n      \"smgmagazine\": 4\r\n    }\r\n  },\r\n  \"hyperzine\": 1,\r\n  \"bandage\": 2,\r\n  \"headset\": 1,\r\n  \"securityuniform1\": 1,\r\n  \"captainsseparatistcap2\": 1\r\n}'),
(52,'securityofficer',4,'{\r\n  \"smg\": 1,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n\r\n  \"divingknife\": 1,\r\n  \"bandolier\": {\r\n    \"nested\": {\r\n      \"smgmagazine\": 4\r\n    }\r\n  },\r\n  \"hyperzine\": 1,\r\n  \"bandage\": 2,\r\n  \"headset\": 1,\r\n  \"securityseparatistsuniform1\": 1,\r\n  \"captainsseparatistcap2\": 1\r\n}'),
(63,'medicaldoctor',3,'{\r\n  \"syringegun \": 1,\r\n  \"deusizine\": 1,\r\n  \"antibiotics\": 2,\r\n  \"headset\": 1,\r\n  \"medicseparatistsuniform1\": {\r\n    \"nested\": {\r\n      \"bandage\": 6,\r\n      \"antidama1\": 4,\r\n      \"antibloodloss1\": 4\r\n    }\r\n  },\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"captainscap2\": 1\r\n}'),
(64,'medicaldoctor',4,'{\r\n  \"syringegun \": 1,\r\n  \"deusizine\": 1,\r\n  \"antibiotics\": 2,\r\n  \"headset\": 1,\r\n  \"medicalofficerclothes\": 1,\r\n  \"bandage\": 6,\r\n  \"antidama1\": 4,\r\n  \"antibloodloss1\": 4,\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"captainscap2\": 1\r\n}'),
(71,'medicaldoctor',2,'{\r\n  \"deusizine\": 1,\r\n  \"opium\": 2,\r\n  \"antibiotics\": 2,\r\n  \"headset\": 1,\r\n  \"doctorsuniform1\": {\r\n    \"nested\": {\r\n      \"bandage\": 4,\r\n      \"antidama1\": 2,\r\n      \"antibloodloss1\": 4\r\n    }\r\n  },\r\n  \"divingmask\": {\r\n    \"nested\": {\r\n      \"oxygentank\": 1\r\n    }\r\n  },\r\n  \"captainscap2\": 1\r\n}');
/*!40000 ALTER TABLE `kits` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `player_ips`
--

DROP TABLE IF EXISTS `player_ips`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `player_ips` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `player_id` int(11) NOT NULL,
  `ip` varchar(45) NOT NULL,
  `subnet` varchar(45) NOT NULL,
  `first_seen` datetime NOT NULL,
  `last_seen` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_player_ip` (`player_id`,`ip`),
  KEY `idx_ip` (`ip`),
  KEY `idx_subnet` (`subnet`),
  CONSTRAINT `player_ips_ibfk_1` FOREIGN KEY (`player_id`) REFERENCES `players_data` (`player_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `player_ips`
--

LOCK TABLES `player_ips` WRITE;
/*!40000 ALTER TABLE `player_ips` DISABLE KEYS */;
/*!40000 ALTER TABLE `player_ips` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `player_kills`
--

DROP TABLE IF EXISTS `player_kills`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `player_kills` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `player_id` int(11) NOT NULL,
  `monster_type` varchar(100) NOT NULL,
  `kills_count` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_player_monster` (`player_id`,`monster_type`),
  KEY `idx_player` (`player_id`),
  KEY `idx_monster` (`monster_type`),
  CONSTRAINT `player_kills_ibfk_1` FOREIGN KEY (`player_id`) REFERENCES `players_data` (`player_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `player_kills`
--

LOCK TABLES `player_kills` WRITE;
/*!40000 ALTER TABLE `player_kills` DISABLE KEYS */;
/*!40000 ALTER TABLE `player_kills` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `player_names`
--

DROP TABLE IF EXISTS `player_names`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `player_names` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `player_id` int(11) NOT NULL,
  `nickname` varchar(255) NOT NULL,
  `first_seen` datetime NOT NULL,
  `last_seen` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_player_name` (`player_id`,`nickname`),
  KEY `idx_nickname` (`nickname`),
  CONSTRAINT `player_names_ibfk_1` FOREIGN KEY (`player_id`) REFERENCES `players_data` (`player_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `player_names`
--

LOCK TABLES `player_names` WRITE;
/*!40000 ALTER TABLE `player_names` DISABLE KEYS */;
/*!40000 ALTER TABLE `player_names` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `player_role_actions`
--

DROP TABLE IF EXISTS `player_role_actions`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `player_role_actions` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `player_id` int(11) NOT NULL,
  `role` varchar(50) NOT NULL,
  `action_type` varchar(100) NOT NULL,
  `value` float DEFAULT 1,
  PRIMARY KEY (`id`),
  KEY `idx_player` (`player_id`),
  KEY `idx_role` (`role`),
  CONSTRAINT `player_role_actions_ibfk_1` FOREIGN KEY (`player_id`) REFERENCES `players_data` (`player_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `player_role_actions`
--

LOCK TABLES `player_role_actions` WRITE;
/*!40000 ALTER TABLE `player_role_actions` DISABLE KEYS */;
/*!40000 ALTER TABLE `player_role_actions` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `player_role_stats`
--

DROP TABLE IF EXISTS `player_role_stats`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `player_role_stats` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `player_id` int(11) NOT NULL,
  `role` varchar(50) NOT NULL,
  `rounds_played` int(11) DEFAULT 0,
  `mvp_score` float DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_player_role` (`player_id`,`role`),
  KEY `idx_player` (`player_id`),
  KEY `idx_role` (`role`),
  CONSTRAINT `player_role_stats_ibfk_1` FOREIGN KEY (`player_id`) REFERENCES `players_data` (`player_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `player_role_stats`
--

LOCK TABLES `player_role_stats` WRITE;
/*!40000 ALTER TABLE `player_role_stats` DISABLE KEYS */;
/*!40000 ALTER TABLE `player_role_stats` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `player_sessions`
--

DROP TABLE IF EXISTS `player_sessions`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `player_sessions` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `player_id` int(11) NOT NULL,
  `session_number` int(11) NOT NULL,
  `login_time` datetime NOT NULL,
  `logout_time` datetime DEFAULT NULL,
  `session_duration` int(11) DEFAULT NULL,
  `avg_ping` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_player_id` (`player_id`),
  KEY `idx_login_time` (`login_time`),
  CONSTRAINT `player_sessions_ibfk_1` FOREIGN KEY (`player_id`) REFERENCES `players_data` (`player_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `player_sessions`
--

LOCK TABLES `player_sessions` WRITE;
/*!40000 ALTER TABLE `player_sessions` DISABLE KEYS */;
/*!40000 ALTER TABLE `player_sessions` ENABLE KEYS */;
UNLOCK TABLES;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_unicode_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER trg_session_number
BEFORE INSERT ON player_sessions
FOR EACH ROW
BEGIN
    DECLARE last_session INT;

    SELECT COALESCE(MAX(session_number), 0)
    INTO last_session
    FROM player_sessions
    WHERE player_id = NEW.player_id;

    SET NEW.session_number = last_session + 1;
END */;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_unicode_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER trg_session_duration
BEFORE UPDATE ON player_sessions
FOR EACH ROW
BEGIN
    IF NEW.logout_time IS NOT NULL AND NEW.login_time IS NOT NULL THEN
        SET NEW.session_duration = TIMESTAMPDIFF(SECOND, NEW.login_time, NEW.logout_time);
    END IF;
END */;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_unicode_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER trg_sessions_update_on_logout
AFTER UPDATE ON player_sessions
FOR EACH ROW
BEGIN
    IF NEW.logout_time IS NOT NULL THEN
        UPDATE players_data
        SET last_login_time = NEW.logout_time
        WHERE player_id = NEW.player_id;
    END IF;
END */;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;

--
-- Table structure for table `players_data`
--

DROP TABLE IF EXISTS `players_data`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `players_data` (
  `player_id` int(11) NOT NULL AUTO_INCREMENT,
  `player_last_time_name` varchar(255) DEFAULT NULL,
  `player_steamid` varchar(50) DEFAULT NULL,
  `last_login_time` datetime DEFAULT NULL,
  `play_time` int(11) DEFAULT NULL,
  `rank_id` int(11) DEFAULT NULL,
  `kit_level` int(11) DEFAULT NULL,
  `achievements` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`achievements`)),
  `last_ip` varchar(45) DEFAULT NULL COMMENT 'Последний IP игрока',
  `last_subnet` varchar(45) DEFAULT NULL COMMENT 'Подсеть (например 192.168.1.0/24)',
  `first_login` datetime DEFAULT NULL COMMENT 'Первый вход на сервер',
  `avg_ping` int(11) DEFAULT NULL COMMENT 'Средний пинг клиента',
  PRIMARY KEY (`player_id`),
  UNIQUE KEY `ix_players_data_player_steamid` (`player_steamid`),
  KEY `ix_players_data_player_id` (`player_id`),
  KEY `ix_players_data_player_last_time_name` (`player_last_time_name`)
) ENGINE=InnoDB AUTO_INCREMENT=1098 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `players_data`
--

LOCK TABLES `players_data` WRITE;
/*!40000 ALTER TABLE `players_data` DISABLE KEYS */;
INSERT INTO `players_data` VALUES
(1,'Матрос 1-ст Александр Андросов','76561198297267585','2026-03-21 11:50:17',300718,7,4,'[]',NULL,NULL,NULL,NULL),
(2,'Матрос 1-ст Сергей Аверин','76561198289354854','2026-03-16 20:14:32',290782,9,4,'[]',NULL,NULL,NULL,NULL),
(3,'Матрос 2-ст Физзи Уолкер','76561199808918929','2026-01-21 11:06:23',59056,8,4,'[]',NULL,NULL,NULL,NULL),
(4,'Старое чудовище','76561199566167348','2025-11-08 16:56:23',19,0,0,'[]',NULL,NULL,NULL,NULL),
(5,'W!Nd','76561198155521878','2026-02-07 15:18:43',62227,2,2,'[]',NULL,NULL,NULL,NULL),
(6,'Supchik','76561199572763629','2025-11-08 19:44:40',217,0,0,'[]',NULL,NULL,NULL,NULL),
(7,'Матрос Эдью Махено','76561199822694603','2025-11-08 20:15:42',767,0,0,'[]',NULL,NULL,NULL,NULL),
(8,'Не негр','76561198452591103','2025-11-09 08:28:26',5,0,0,'[]',NULL,NULL,NULL,NULL),
(9,'Матрос 1-ст Максим Мельников','76561199187453501','2026-01-24 09:38:19',4477,0,0,'[]',NULL,NULL,NULL,NULL),
(10,'Mihfil','76561199230711462','2026-03-08 01:58:35',10125,0,0,'[]',NULL,NULL,NULL,NULL),
(11,'Денис Крабов','76561198930496446','2026-02-04 11:48:58',20643,0,1,'[]',NULL,NULL,NULL,NULL),
(12,'Калыханка','76561199150391602','2025-11-10 20:12:13',56,0,0,'[]',NULL,NULL,NULL,NULL),
(13,'сdio','76561199099536012','2026-03-21 12:54:19',583127,8,4,'[]',NULL,NULL,NULL,NULL),
(14,'OScar','76561198089545255','2025-11-11 22:43:11',36,0,0,'[]',NULL,NULL,NULL,NULL),
(15,'Даобаб','76561199854117941','2025-11-12 09:03:06',13,0,0,'[]',NULL,NULL,NULL,NULL),
(16,'Гусеничка','76561198047721920','2025-11-12 10:23:57',34,0,0,'[]',NULL,NULL,NULL,NULL),
(17,'Komutan Logar','76561199497060452','2025-11-12 11:19:54',2471,0,0,'[]',NULL,NULL,NULL,NULL),
(18,'ryrtmoy0o','76561198887741584','2025-11-12 11:47:37',150,0,0,'[]',NULL,NULL,NULL,NULL),
(19,'Сергей Мавроди','76561199849772159','2025-11-12 13:47:46',7,0,0,'[]',NULL,NULL,NULL,NULL),
(20,'RuBratik','76561199803553552','2025-11-12 15:17:05',144,0,0,'[]',NULL,NULL,NULL,NULL),
(21,'VASABI','76561199109578098','2025-11-12 18:29:31',12,0,0,'[]',NULL,NULL,NULL,NULL),
(22,'TheSerGlade','76561198123459290','2025-11-12 22:48:55',4582,0,0,'[]',NULL,NULL,NULL,NULL),
(23,'Ugly Child Rapist, Wanted by Law','76561198135085751','2025-11-12 22:40:45',986,0,0,'[]',NULL,NULL,NULL,NULL),
(24,'Айслат','76561199093198159','2026-03-08 14:09:31',95764,7,4,'[]',NULL,NULL,NULL,NULL),
(25,'Dkay','76561198063556333','2025-11-12 22:27:46',97,0,0,'[]',NULL,NULL,NULL,NULL),
(26,'possum lord','76561198322505180','2025-11-12 22:39:28',37,0,0,'[]',NULL,NULL,NULL,NULL),
(27,'KindMen','76561199150802815','2025-11-13 20:33:10',8081,0,0,'[]',NULL,NULL,NULL,NULL),
(28,'Окси','76561198214464449','2025-11-13 18:23:05',61,0,0,'[]',NULL,NULL,NULL,NULL),
(29,'Матрос Марк Д`Револье','76561198869543216','2026-01-28 14:38:31',784,0,0,'[]',NULL,NULL,NULL,NULL),
(30,'Antony X','76561199012790640','2025-11-15 16:10:07',4,0,0,'[]',NULL,NULL,NULL,NULL),
(31,'Негодяй','76561199093494357','2025-11-15 17:42:00',39,0,0,'[]',NULL,NULL,NULL,NULL),
(32,'Сосинский Дмитрий Вадимович','76561198062398515','2025-11-15 21:23:41',35,0,0,'[]',NULL,NULL,NULL,NULL),
(33,'Рядовой Руман Лендхок','76561199586921525','2026-02-19 11:58:57',25463,0,0,'[]',NULL,NULL,NULL,NULL),
(34,'Shushka123','76561199553075756','2025-11-16 18:40:54',10,0,0,'[]',NULL,NULL,NULL,NULL),
(35,'Матрос 1-ст Михаил Романов','76561199366368424','2026-02-21 21:43:50',21239,1,1,'[]',NULL,NULL,NULL,NULL),
(36,'Azen_228','76561199690594377','2025-11-18 17:54:35',6,0,0,'[]',NULL,NULL,NULL,NULL),
(37,'Totengräber','76561199148191850','2025-11-20 09:36:01',55,0,0,'[]',NULL,NULL,NULL,NULL),
(38,'LaiSent666','76561199134804944','2025-11-23 11:07:56',107,0,0,'[]',NULL,NULL,NULL,NULL),
(39,'Quickster','76561199272340776','2025-11-28 16:18:05',11,0,0,'[]',NULL,NULL,NULL,NULL),
(40,'Капитан Чепух','76561198966560789','2025-11-28 21:35:06',23,0,0,'[]',NULL,NULL,NULL,NULL),
(41,'Winger','76561198310412827','2025-11-28 23:48:59',168,0,0,'[]',NULL,NULL,NULL,NULL),
(42,'Анонимус','76561199725122607','2025-12-01 15:53:31',4,0,0,'[]',NULL,NULL,NULL,NULL),
(43,'NeTy__SloFF','76561198415337160','2025-12-01 18:00:27',1373,0,0,'[]',NULL,NULL,NULL,NULL),
(44,'Легат','76561198868002247','2025-12-24 17:08:11',2067,0,0,'[]',NULL,NULL,NULL,NULL),
(45,'Червей','76561198949918460','2026-02-25 14:40:53',3938,0,0,'[]',NULL,NULL,NULL,NULL),
(46,'Луна','76561199067358956','2026-03-22 11:46:41',614296,5,3,'[]',NULL,NULL,NULL,NULL),
(47,'Андрюша','76561199078030627','2026-03-16 12:39:25',119420,3,2,'[]',NULL,NULL,NULL,NULL),
(48,'Саня','76561199037799189','2026-01-30 15:13:28',23333,0,0,'[]',NULL,NULL,NULL,NULL),
(49,'ВИЦЕ','76561198951417996','2025-12-24 17:08:36',2092,0,0,'[]',NULL,NULL,NULL,NULL),
(50,'Солнцев','76561198964710593','2026-03-22 13:49:11',583367,3,2,'[]',NULL,NULL,NULL,NULL),
(51,'Zhizhnik','76561198176644307','2025-12-24 17:10:45',2221,0,0,'[]',NULL,NULL,NULL,NULL),
(52,'Дюваль','76561198813594310','2026-03-22 19:05:51',418875,5,3,'[]',NULL,NULL,NULL,NULL),
(53,'Бина','76561199092488251','2025-12-24 17:09:12',3091,0,0,'[]',NULL,NULL,NULL,NULL),
(54,'Lemm','76561199857161978','2026-03-22 17:49:40',432946,2,2,'[]',NULL,NULL,NULL,NULL),
(55,'Матрос 1-ст Маркус Белов','76561199220215839','2026-03-18 11:29:25',258339,1,1,'[]',NULL,NULL,NULL,NULL),
(56,'Матрос 2-ст Вернер Маврин','76561199729584441','2026-03-21 11:26:12',253958,2,2,'[]',NULL,NULL,NULL,NULL),
(57,'Dr. Sausage','76561198211268815','2026-02-12 15:17:14',85,0,0,'[]',NULL,NULL,NULL,NULL),
(58,'Soviet_Engineer','76561199092547606','2026-01-20 19:50:35',5577,0,0,'[]',NULL,NULL,NULL,NULL),
(59,'furrylingus','76561198772107617','2026-01-20 19:50:38',5128,0,0,'[]',NULL,NULL,NULL,NULL),
(60,'Dorn','76561199253679304','2026-01-20 18:55:17',554,0,0,'[]',NULL,NULL,NULL,NULL),
(61,'XL','76561198055961316','2026-01-20 18:46:58',1,0,0,'[]',NULL,NULL,NULL,NULL),
(62,'Richard','76561199187910767','2026-01-20 19:31:45',1379,0,0,'[]',NULL,NULL,NULL,NULL),
(63,'Vaskapetka32','76561198971944015','2026-01-20 19:31:19',1275,0,0,'[]',NULL,NULL,NULL,NULL),
(64,'ОЛЕГ МОНГОЛ','76561199529876505','2026-01-20 19:50:56',1149,0,0,'[]',NULL,NULL,NULL,NULL),
(65,'Mr. Mania','76561198014625347','2026-01-20 19:59:27',46,0,0,'[]',NULL,NULL,NULL,NULL),
(66,'MedMaks','76561198082447762','2026-01-20 21:05:59',5,0,0,'[]',NULL,NULL,NULL,NULL),
(67,'klim4ek','76561199109322737','2026-01-20 21:09:06',2,0,0,'[]',NULL,NULL,NULL,NULL),
(68,'Vyachesslavik','76561199778787516','2026-01-21 07:15:47',3996,0,0,'[]',NULL,NULL,NULL,NULL),
(69,'Матрос 1-ст Владимир Степанов','76561199261809946','2026-03-18 10:18:07',118013,1,1,'[]',NULL,NULL,NULL,NULL),
(70,'IlyxSap','76561199304578442','2026-01-22 12:40:33',70,0,0,'[]',NULL,NULL,NULL,NULL),
(71,'Арнольд Готлер','76561199806408361','2026-02-20 13:35:03',9181,0,0,'[]',NULL,NULL,NULL,NULL),
(72,'grom','76561198072361710','2026-01-22 17:06:18',320,0,0,'[]',NULL,NULL,NULL,NULL),
(73,'dolbaeb','76561199863989734','2026-03-22 18:32:37',2825,0,0,'[]',NULL,NULL,NULL,NULL),
(74,'БУЛЬБАЗАВР','76561199652351162','2026-01-23 18:20:48',113,0,0,'[]',NULL,NULL,NULL,NULL),
(75,'Rhino','76561198107484882','2026-01-23 23:16:28',513,0,0,'[]',NULL,NULL,NULL,NULL),
(76,'BYCAt285','76561199512505260','2026-01-24 13:00:57',95,0,0,'[]',NULL,NULL,NULL,NULL),
(77,'Христофор Гайкин','76561199016856843','2026-03-22 17:02:55',98547,1,1,'[]',NULL,NULL,NULL,NULL),
(78,'Lonely boy','76561198158129318','2026-01-24 08:33:27',178,0,0,'[]',NULL,NULL,NULL,NULL),
(79,'КосойУебАн2010','76561198964463940','2026-01-25 11:17:27',12171,0,0,'[]',NULL,NULL,NULL,NULL),
(80,'Никита Петров','76561198190642903','2026-03-22 13:31:38',63344,0,0,'[]',NULL,NULL,NULL,NULL),
(81,'Oliver Firemage','76561198279937079','2026-01-24 12:23:29',8926,0,0,'[]',NULL,NULL,NULL,NULL),
(82,'KTKrieg','76561199154555637','2026-01-24 12:40:32',9983,0,0,'[]',NULL,NULL,NULL,NULL),
(83,'Zikfrid','76561198976809959','2026-01-24 10:15:09',717,0,0,'[]',NULL,NULL,NULL,NULL),
(84,'Олег Бобров','76561199066115991','2026-01-25 11:18:25',6948,0,0,'[]',NULL,NULL,NULL,NULL),
(85,'Shadow of space','76561198277779881','2026-01-24 12:39:21',4173,0,0,'[]',NULL,NULL,NULL,NULL),
(86,'Darmi155','76561199084800133','2026-01-24 11:31:30',82,0,0,'[]',NULL,NULL,NULL,NULL),
(87,'V_F A N A T I C_V','76561199165230298','2026-01-24 12:47:20',2864,0,0,'[]',NULL,NULL,NULL,NULL),
(88,'Матрос 2-ст Иван Бергер','76561199537978248','2026-01-31 09:50:12',41,0,0,'[]',NULL,NULL,NULL,NULL),
(89,'Льюис Крид','76561199466838443','2026-01-24 12:22:01',1498,0,0,'[]',NULL,NULL,NULL,NULL),
(90,'ОКАРИН','76561199119191916','2026-01-24 12:05:08',483,0,0,'[]',NULL,NULL,NULL,NULL),
(91,'Tai1erok','76561199750830518','2026-01-24 12:03:41',288,0,0,'[]',NULL,NULL,NULL,NULL),
(92,'Sunday Chick-fil-A','76561198968225358','2026-01-24 12:01:24',110,0,0,'[]',NULL,NULL,NULL,NULL),
(93,'DEIMOS','76561199830038562','2026-01-24 12:09:48',294,0,0,'[]',NULL,NULL,NULL,NULL),
(94,'максим','76561199665710656','2026-01-24 12:29:28',1018,0,0,'[]',NULL,NULL,NULL,NULL),
(95,'Нурми','76561199445041913','2026-02-13 13:57:44',30,0,0,'[]',NULL,NULL,NULL,NULL),
(96,'GOP','76561199485480249','2026-03-21 14:46:17',1275,0,0,'[]',NULL,NULL,NULL,NULL),
(97,'Гадэм','76561198269320728','2026-01-24 12:41:21',207,0,0,'[]',NULL,NULL,NULL,NULL),
(98,'Shio','76561199489175950','2026-01-24 12:51:32',485,0,0,'[]',NULL,NULL,NULL,NULL),
(99,'massiv','76561198105742025','2026-01-25 10:41:07',175,0,0,'[]',NULL,NULL,NULL,NULL),
(100,'Серафим Алаксайт','76561198019784942','2026-02-13 13:33:28',37839,0,0,'[]',NULL,NULL,NULL,NULL),
(101,'Rainbow Cat','76561199521976394','2026-02-04 21:27:54',0,0,0,'[]',NULL,NULL,NULL,NULL),
(102,'mr.Jon','76561198771659440','2026-01-25 11:36:21',11,0,0,'[]',NULL,NULL,NULL,NULL),
(103,'serega.saofenb','76561199655607311','2026-02-04 19:16:36',5574,0,0,'[]',NULL,NULL,NULL,NULL),
(104,'Stepler','76561199581842787','2026-01-27 19:18:19',269,0,0,'[]',NULL,NULL,NULL,NULL),
(105,'Матрос Александр Белов','76561198121409983','2026-03-16 13:28:40',168067,0,1,'[]',NULL,NULL,NULL,NULL),
(106,'Матрос Степан Хворь','76561198821450666','2026-03-04 13:34:42',51335,1,1,'[]',NULL,NULL,NULL,NULL),
(107,'Матрос Нидаррос','76561198866148138','2026-03-10 10:24:39',5989,0,0,'[]',NULL,NULL,NULL,NULL),
(108,'Мартин Коргутов','76561199461423413','2026-03-22 17:03:43',13847,0,0,'[]',NULL,NULL,NULL,NULL),
(109,'Les1','76561198068225886','2026-03-22 11:45:34',250764,1,1,'[]',NULL,NULL,NULL,NULL),
(110,'Буденка Мехаил','76561199577405976','2026-02-21 21:32:40',18324,0,0,'[]',NULL,NULL,NULL,NULL),
(111,'Матрос Павел Пурнов','76561197980281423','2026-01-30 15:08:21',3576,0,0,'[]',NULL,NULL,NULL,NULL),
(112,'Магистр','76561199128767318','2026-01-28 14:41:41',341,0,0,'[]',NULL,NULL,NULL,NULL),
(113,'hasterWhy','76561198780797215','2026-01-28 15:13:09',0,0,0,'[]',NULL,NULL,NULL,NULL),
(114,'Капитан Отчаяние','76561198402028575','2026-01-28 15:50:53',448,0,0,'[]',NULL,NULL,NULL,NULL),
(115,'v1rus35','76561199645737913','2026-01-29 09:44:12',4,0,0,'[]',NULL,NULL,NULL,NULL),
(116,'Moeconnoisseur','76561198826674668','2026-01-29 22:51:14',3506,0,0,'[]',NULL,NULL,NULL,NULL),
(117,'Dobrodeetel','76561198212501587','2026-01-29 22:03:40',15,0,0,'[]',NULL,NULL,NULL,NULL),
(118,'vavalera3045','76561199064112807','2026-01-29 22:46:54',5,0,0,'[]',NULL,NULL,NULL,NULL),
(119,'Винницкий Маньяк','76561199104400801','2026-02-27 08:03:47',1552,0,0,'[]',NULL,NULL,NULL,NULL),
(120,'молодой бандит','76561198868654168','2026-01-30 10:36:04',273,0,0,'[]',NULL,NULL,NULL,NULL),
(121,'Владик','76561199036895794','2026-01-30 10:33:13',204,0,0,'[]',NULL,NULL,NULL,NULL),
(122,'Алексей Попов','76561199569586618','2026-03-07 12:13:21',10614,0,0,'[]',NULL,NULL,NULL,NULL),
(123,'MegaMyxa_2210','76561198783050913','2026-01-30 11:36:17',1045,0,0,'[]',NULL,NULL,NULL,NULL),
(124,'BOEHKOMAT','76561199546102794','2026-02-16 12:55:27',123,0,0,'[]',NULL,NULL,NULL,NULL),
(125,'Agar','76561199682155398','2026-01-30 11:38:03',82,0,0,'[]',NULL,NULL,NULL,NULL),
(126,'Богдан Денисов','76561199090113767','2026-01-30 12:14:21',652,0,0,'[]',NULL,NULL,NULL,NULL),
(127,'Степан Кошечкин','76561198404422504','2026-02-20 15:39:43',24582,0,0,'[]',NULL,NULL,NULL,NULL),
(128,'Вадим Степанов','76561198423651557','2026-01-30 13:07:49',2881,0,0,'[]',NULL,NULL,NULL,NULL),
(129,'Алекс Дунн','76561198259218753','2026-01-30 13:12:17',2803,0,0,'[]',NULL,NULL,NULL,NULL),
(130,'Эрих Майер','76561199093230773','2026-01-30 13:06:12',2448,0,0,'[]',NULL,NULL,NULL,NULL),
(131,'Хоглид','76561199427066683','2026-01-30 12:38:48',370,0,0,'[]',NULL,NULL,NULL,NULL),
(132,'WierD_O','76561199515817398','2026-01-30 15:00:33',8700,0,0,'[]',NULL,NULL,NULL,NULL),
(133,'Кот Матроскин','76561198303452132','2026-01-30 13:24:02',147,0,0,'[]',NULL,NULL,NULL,NULL),
(134,'Фрол Фролов','76561199831743527','2026-01-30 13:52:53',57,0,0,'[]',NULL,NULL,NULL,NULL),
(135,'Вован Жёлудь','76561199060347475','2026-03-22 17:38:10',55552,0,0,'[]',NULL,NULL,NULL,NULL),
(136,'jxys','76561199827555124','2026-01-30 16:34:33',41,0,0,'[]',NULL,NULL,NULL,NULL),
(137,'Мартин','76561198880413284','2026-01-30 20:31:23',0,0,0,'[]',NULL,NULL,NULL,NULL),
(138,'(KAI) NOIR','76561199387612368','2026-01-30 20:38:15',65,0,0,'[]',NULL,NULL,NULL,NULL),
(139,'Qwizzz','76561198180433179','2026-01-31 01:14:22',42,0,0,'[]',NULL,NULL,NULL,NULL),
(140,'Dark_Olegik','76561199806491876','2026-01-31 05:58:53',20,0,0,'[]',NULL,NULL,NULL,NULL),
(141,'montin_kOban','76561199732393185','2026-01-31 06:07:08',241,0,0,'[]',NULL,NULL,NULL,NULL),
(142,'redbulltini','76561199076082572','2026-01-31 09:32:21',1557,0,0,'[]',NULL,NULL,NULL,NULL),
(143,'zeta','76561199821861193','2026-01-31 09:35:01',1573,0,0,'[]',NULL,NULL,NULL,NULL),
(144,'------','76561198132363178','2026-01-31 09:21:28',721,0,0,'[]',NULL,NULL,NULL,NULL),
(145,'Матрос Андрей Кравинов','76561198807502191','2026-01-31 09:42:57',1860,0,0,'[]',NULL,NULL,NULL,NULL),
(146,'авантюрист','76561198129624326','2026-01-31 09:32:11',596,0,0,'[]',NULL,NULL,NULL,NULL),
(147,'CHEMODAN','76561199185683404','2026-03-21 12:54:35',94762,1,1,'[]',NULL,NULL,NULL,NULL),
(148,'Рафаэль Текила Дю','76561198036111243','2026-01-31 11:27:50',5606,0,0,'[]',NULL,NULL,NULL,NULL),
(149,'ГрОб','76561198410638820','2026-01-31 11:09:21',2478,0,0,'[]',NULL,NULL,NULL,NULL),
(150,'Валера Далов','76561199556767847','2026-03-21 11:49:33',44230,0,0,'[]',NULL,NULL,NULL,NULL),
(151,'у','76561198335614788','2026-02-18 16:48:09',281,0,0,'[]',NULL,NULL,NULL,NULL),
(152,'pu6istik11','76561199683931818','2026-01-31 10:42:19',28,0,0,'[]',NULL,NULL,NULL,NULL),
(153,'Матрос Андрей Коробов','76561198752268497','2026-03-21 12:45:02',143769,1,1,'[]',NULL,NULL,NULL,NULL),
(154,'Елдыгей','76561199602630411','2026-01-31 11:44:53',1,0,0,'[]',NULL,NULL,NULL,NULL),
(155,'vastok','76561198282960734','2026-01-31 11:45:30',5,0,0,'[]',NULL,NULL,NULL,NULL),
(156,'n3kimi','76561199816053121','2026-01-31 11:46:36',36,0,0,'[]',NULL,NULL,NULL,NULL),
(157,'RexterTwin','76561199819968692','2026-01-31 12:03:32',0,0,0,'[]',NULL,NULL,NULL,NULL),
(158,'Jam','76561199643227207','2026-01-31 12:15:52',125,0,0,'[]',NULL,NULL,NULL,NULL),
(159,'Кен','76561198991947033','2026-01-31 12:30:02',103,0,0,'[]',NULL,NULL,NULL,NULL),
(160,'nb134e710501q','76561198984051116','2026-01-31 13:02:58',325,0,0,'[]',NULL,NULL,NULL,NULL),
(161,'koteika','76561199510863332','2026-02-04 11:09:59',379,0,0,'[]',NULL,NULL,NULL,NULL),
(162,'Pidorasik','76561199735945476','2026-01-31 21:45:27',303,0,0,'[]',NULL,NULL,NULL,NULL),
(163,'тайлер','76561199786448247','2026-02-12 18:34:38',1781,0,0,'[]',NULL,NULL,NULL,NULL),
(164,'kst','76561199091865079','2026-03-13 04:26:39',32758,0,0,'[]',NULL,NULL,NULL,NULL),
(165,'SHODOW_life','76561199745376082','2026-02-01 13:14:05',8,0,0,'[]',NULL,NULL,NULL,NULL),
(166,'ГусьВалера','76561199204731602','2026-02-01 15:45:43',42,0,0,'[]',NULL,NULL,NULL,NULL),
(167,'MafioznikGosha','76561199244320794','2026-02-02 14:53:58',12,0,0,'[]',NULL,NULL,NULL,NULL),
(168,'Артем Савченков','76561199805575597','2026-03-22 17:38:06',477767,5,3,'[]',NULL,NULL,NULL,NULL),
(169,'dirtyblood2008','76561199055834768','2026-02-02 21:06:32',7,0,0,'[]',NULL,NULL,NULL,NULL),
(170,'Gans','76561199480818683','2026-02-03 16:28:06',4,0,0,'[]',NULL,NULL,NULL,NULL),
(171,'HeroinoВый MonsТер','76561199267405596','2026-02-03 16:54:37',146,0,0,'[]',NULL,NULL,NULL,NULL),
(172,'Volkswagen','76561198176596851','2026-03-18 10:28:36',85486,0,0,'[]',NULL,NULL,NULL,NULL),
(173,'Dog','76561199265993930','2026-02-04 11:01:14',86,0,0,'[]',NULL,NULL,NULL,NULL),
(174,'M-2','76561199106924843','2026-02-08 20:22:30',933,0,0,'[]',NULL,NULL,NULL,NULL),
(175,'Akinaxis','76561198867944981','2026-02-04 14:43:55',136,0,0,'[]',NULL,NULL,NULL,NULL),
(176,'walentinka2255','76561199781413582','2026-02-04 15:00:00',77,0,0,'[]',NULL,NULL,NULL,NULL),
(177,'Zalypa','76561199675846806','2026-02-10 14:23:28',411,0,0,'[]',NULL,NULL,NULL,NULL),
(178,'Lutso Reb','76561199185884069','2026-02-04 15:19:12',52,0,0,'[]',NULL,NULL,NULL,NULL),
(179,'Иван Гьютон','76561199075098943','2026-02-07 09:24:31',9198,0,0,'[]',NULL,NULL,NULL,NULL),
(180,'ksrhsdfmghb','76561198764436161','2026-02-04 15:39:10',9,0,0,'[]',NULL,NULL,NULL,NULL),
(181,'Херомантий','76561198298780455','2026-02-04 17:13:02',3874,0,0,'[]',NULL,NULL,NULL,NULL),
(182,'Ганс ланда','76561199529024135','2026-03-07 09:06:33',3362,0,0,'[]',NULL,NULL,NULL,NULL),
(183,'SunSayMudak','76561199090226739','2026-02-08 20:40:55',3622,0,0,'[]',NULL,NULL,NULL,NULL),
(184,'Jerolock','76561198865946520','2026-03-22 17:38:14',73141,1,1,'[]',NULL,NULL,NULL,NULL),
(185,'Behemoth','76561198302691154','2026-02-04 19:07:47',173,0,0,'[]',NULL,NULL,NULL,NULL),
(186,'GAZZMASK','76561199254543639','2026-02-04 19:32:07',77,0,0,'[]',NULL,NULL,NULL,NULL),
(187,'НАЕЛДИН','76561199809923830','2026-02-04 20:09:58',5,0,0,'[]',NULL,NULL,NULL,NULL),
(188,'Жидкосрал Путович, Глиномес','76561198273944012','2026-02-04 22:20:19',2613,0,0,'[]',NULL,NULL,NULL,NULL),
(189,'Landann','76561198995690441','2026-02-04 21:45:24',26,0,0,'[]',NULL,NULL,NULL,NULL),
(190,'_Dark_Sider_','76561199071267191','2026-02-04 22:02:18',34,0,0,'[]',NULL,NULL,NULL,NULL),
(191,'dieushu','76561198312664045','2026-02-05 03:45:19',92,0,0,'[]',NULL,NULL,NULL,NULL),
(192,'If_LeoPan*','76561198017023229','2026-02-25 16:24:05',605,0,0,'[]',NULL,NULL,NULL,NULL),
(193,'Heisenberg','76561198108343223','2026-02-05 22:36:55',144,0,0,'[]',NULL,NULL,NULL,NULL),
(194,'vanzpapa37','76561198751112842','2026-02-06 13:03:01',14,0,0,'[]',NULL,NULL,NULL,NULL),
(195,'9mice','76561199405999604','2026-02-06 19:15:49',119,0,0,'[]',NULL,NULL,NULL,NULL),
(196,'Murlocotan','76561198081321370','2026-02-07 07:12:01',0,0,0,'[]',NULL,NULL,NULL,NULL),
(197,'Бобр','76561198839619162','2026-03-15 12:33:56',28076,1,1,'[]',NULL,NULL,NULL,NULL),
(198,'Shaffner','76561199758770303','2026-03-21 15:03:47',32241,0,0,'[]',NULL,NULL,NULL,NULL),
(199,'Пiпiдастр','76561199481946713','2026-02-07 09:39:51',504,0,0,'[]',NULL,NULL,NULL,NULL),
(200,'тимбер сюрприз','76561198962795247','2026-02-07 10:03:51',1899,0,0,'[]',NULL,NULL,NULL,NULL),
(201,'Old blood','76561199303493737','2026-03-09 10:19:00',3324,0,0,'[]',NULL,NULL,NULL,NULL),
(202,'NaZaReTuM','76561199014712433','2026-02-07 10:50:05',4084,0,0,'[]',NULL,NULL,NULL,NULL),
(203,'cynfigni','76561198757100946','2026-02-07 09:46:48',46,0,0,'[]',NULL,NULL,NULL,NULL),
(204,'Garlic Bread','76561198913333983','2026-02-07 10:11:31',1471,0,0,'[]',NULL,NULL,NULL,NULL),
(205,'Rosli','76561199214928111','2026-02-07 11:08:46',3745,0,0,'[]',NULL,NULL,NULL,NULL),
(206,'Murcka','76561198801397670','2026-02-07 10:12:55',61,0,0,'[]',NULL,NULL,NULL,NULL),
(207,'Сергий Колбасенко','76561199580657579','2026-02-20 14:06:59',2633,0,0,'[]',NULL,NULL,NULL,NULL),
(208,'Добрый Ээх','76561198064480676','2026-02-17 11:51:31',2316,0,0,'[]',NULL,NULL,NULL,NULL),
(209,'lazerad','76561199546007614','2026-02-07 10:57:28',71,0,0,'[]',NULL,NULL,NULL,NULL),
(210,'Инспектор желешк','76561198426196000','2026-02-07 11:11:26',134,0,0,'[]',NULL,NULL,NULL,NULL),
(211,'Volltik','76561199097230922','2026-02-07 11:22:38',272,0,0,'[]',NULL,NULL,NULL,NULL),
(212,'Гоша','76561199821978477','2026-02-07 11:25:37',30,0,0,'[]',NULL,NULL,NULL,NULL),
(213,'Отец Мефидроний','76561199505163856','2026-03-08 19:28:44',69202,0,0,'[]',NULL,NULL,NULL,NULL),
(214,'Слепой Пью','76561199132822267','2026-02-07 11:56:53',21,0,0,'[]',NULL,NULL,NULL,NULL),
(215,'mister Init','76561198242948144','2026-02-07 11:57:55',75,0,0,'[]',NULL,NULL,NULL,NULL),
(216,'Silverhand','76561199528984249','2026-03-05 13:29:07',10879,0,0,'[]',NULL,NULL,NULL,NULL),
(217,'oleglepihin196','76561199857473261','2026-02-07 12:39:19',40,0,0,'[]',NULL,NULL,NULL,NULL),
(218,'Матрос Дмитрий Пляскин','76561199277597718','2026-02-14 12:58:57',1824,0,0,'[]',NULL,NULL,NULL,NULL),
(219,'Артур Аврамович','76561199566688887','2026-02-09 21:28:03',10796,0,0,'[]',NULL,NULL,NULL,NULL),
(220,'Петр','76561198854751699','2026-02-07 14:49:59',4685,0,0,'[]',NULL,NULL,NULL,NULL),
(221,'Никита Лоболев    Череп','76561198387401227','2026-03-01 15:57:13',123694,1,1,'[]',NULL,NULL,NULL,NULL),
(222,'Матрос Дак Шестокович (Диоген)','76561199148910504','2026-02-08 13:23:39',9014,0,0,'[]',NULL,NULL,NULL,NULL),
(223,'СМЕРТЬ В НИЩИТЕ','76561199208816429','2026-02-07 14:36:58',50,0,0,'[]',NULL,NULL,NULL,NULL),
(224,'NIKEL','76561198982634871','2026-02-09 15:07:32',300,0,0,'[]',NULL,NULL,NULL,NULL),
(225,'латунь','76561199544760591','2026-02-07 16:09:41',2507,0,0,'[]',NULL,NULL,NULL,NULL),
(226,'Кто я','76561199547360464','2026-02-25 19:17:06',28804,0,0,'[]',NULL,NULL,NULL,NULL),
(227,'SosalProdakshn','76561198091014913','2026-02-07 15:41:27',379,0,0,'[]',NULL,NULL,NULL,NULL),
(228,'СЫН ЧЕТВЕРКИ','76561198929154013','2026-02-07 16:23:15',38,0,0,'[]',NULL,NULL,NULL,NULL),
(229,'Чарли','76561199189272266','2026-02-08 02:14:11',11,0,0,'[]',NULL,NULL,NULL,NULL),
(230,'Игорь Палычь','76561199556665877','2026-03-19 14:03:27',119,0,0,'[]',NULL,NULL,NULL,NULL),
(231,'sanyo','76561199817467466','2026-03-22 14:10:59',14296,0,0,'[]',NULL,NULL,NULL,NULL),
(232,'Bopon','76561198861418965','2026-02-08 08:56:46',26,0,0,'[]',NULL,NULL,NULL,NULL),
(233,'бэбсолини','76561199810231636','2026-02-27 10:45:54',8689,0,0,'[]',NULL,NULL,NULL,NULL),
(234,'Lakogelik','76561199059505142','2026-02-08 09:12:31',36,0,0,'[]',NULL,NULL,NULL,NULL),
(235,'Крошка Джон','76561197999372706','2026-02-08 09:21:33',7,0,0,'[]',NULL,NULL,NULL,NULL),
(236,'Андрей Белов','76561199671544119','2026-02-10 13:29:21',22632,0,0,'[]',NULL,NULL,NULL,NULL),
(237,'Матрос Виктор Кириешников','76561199380994305','2026-02-08 11:50:45',7138,0,0,'[]',NULL,NULL,NULL,NULL),
(238,'Пахан','76561199114834557','2026-02-08 09:43:13',127,0,0,'[]',NULL,NULL,NULL,NULL),
(239,'Pray for Astolfo','76561199088333299','2026-02-08 09:42:41',24,0,0,'[]',NULL,NULL,NULL,NULL),
(240,'D.2910','76561199867454704','2026-02-08 09:52:40',0,0,0,'[]',NULL,NULL,NULL,NULL),
(241,'Дочь Напердыша','76561198394395950','2026-02-08 11:11:49',3900,0,0,'[]',NULL,NULL,NULL,NULL),
(242,'MyrlinMyrlo','76561198290070142','2026-02-21 08:33:26',67163,1,1,'[]',NULL,NULL,NULL,NULL),
(243,'ШУТНЯРА ЁБАННЫЙ','76561199475928681','2026-02-17 17:51:25',183,0,0,'[]',NULL,NULL,NULL,NULL),
(244,'Евлампий Мадуров','76561199084447051','2026-02-08 12:11:01',3411,0,0,'[]',NULL,NULL,NULL,NULL),
(245,'Aequus','76561197999105591','2026-02-08 12:27:02',3259,0,0,'[]',NULL,NULL,NULL,NULL),
(246,'Runzle','76561198273540075','2026-02-08 11:46:17',9,0,0,'[]',NULL,NULL,NULL,NULL),
(247,'Матрос Поедатель','76561199492889605','2026-02-08 12:00:04',57,0,0,'[]',NULL,NULL,NULL,NULL),
(248,'Артур','76561198128097572','2026-02-08 12:03:43',195,0,0,'[]',NULL,NULL,NULL,NULL),
(249,'Авраам Русаков','76561199516258882','2026-02-10 16:34:43',16063,0,0,'[]',NULL,NULL,NULL,NULL),
(250,'Кафий','76561198238156512','2026-02-08 13:04:42',3294,0,0,'[]',NULL,NULL,NULL,NULL),
(251,'Матрос Юрий Нестеров','76561199048640857','2026-02-08 12:11:24',6,0,0,'[]',NULL,NULL,NULL,NULL),
(252,'Шарп Андерсон','76561198913741815','2026-02-21 09:19:41',4138,0,0,'[]',NULL,NULL,NULL,NULL),
(253,'DiktatoR','76561198743814316','2026-02-10 15:04:20',10728,0,0,'[]',NULL,NULL,NULL,NULL),
(254,'DECR!MENT','76561198930920666','2026-02-08 13:14:40',917,0,0,'[]',NULL,NULL,NULL,NULL),
(255,'ultra Arte324','76561199812049492','2026-02-08 13:21:33',1302,0,0,'[]',NULL,NULL,NULL,NULL),
(256,'Clemad','76561199554923705','2026-02-08 13:21:20',1263,0,0,'[]',NULL,NULL,NULL,NULL),
(257,'Vo1D','76561199209504663','2026-02-08 14:26:38',1033,0,0,'[]',NULL,NULL,NULL,NULL),
(258,'P3nDaliN','76561199085962104','2026-02-08 14:59:27',912,0,0,'[]',NULL,NULL,NULL,NULL),
(259,'3eJleHblUl','76561198409244999','2026-02-08 18:20:02',337,0,0,'[]',NULL,NULL,NULL,NULL),
(260,'КУПИТМАН','76561198271565294','2026-02-11 17:39:57',6206,0,0,'[]',NULL,NULL,NULL,NULL),
(261,'консе','76561198128453307','2026-02-25 18:46:02',146,0,0,'[]',NULL,NULL,NULL,NULL),
(262,'Йохан Фром','76561199437684604','2026-02-08 20:40:57',1070,0,0,'[]',NULL,NULL,NULL,NULL),
(263,'свинцовый чувак','76561198878123029','2026-02-08 19:05:09',45,0,0,'[]',NULL,NULL,NULL,NULL),
(264,'Матросенк','76561199475206710','2026-02-28 19:22:09',1285,0,0,'[]',NULL,NULL,NULL,NULL),
(265,'40313','76561198029304002','2026-02-08 19:29:17',1061,0,0,'[]',NULL,NULL,NULL,NULL),
(266,'Sesbian Lex','76561198116636250','2026-02-08 20:00:15',1574,0,0,'[]',NULL,NULL,NULL,NULL),
(267,'Podres Poderes','76561199001903862','2026-02-08 19:59:27',2828,0,0,'[]',NULL,NULL,NULL,NULL),
(268,'Краб','76561199128813788','2026-02-25 19:13:26',46962,1,1,'[]',NULL,NULL,NULL,NULL),
(269,'Deathwewd','76561198827005893','2026-03-21 10:55:55',202884,1,1,'[]',NULL,NULL,NULL,NULL),
(270,'Пётр Игнатьевич','76561199213965724','2026-02-08 19:53:50',91,0,0,'[]',NULL,NULL,NULL,NULL),
(271,'Иван Василичкин','76561199133100138','2026-02-15 19:58:14',1241,0,0,'[]',NULL,NULL,NULL,NULL),
(272,'pakepko_j','76561199132911357','2026-03-11 17:34:05',142072,1,1,'[]',NULL,NULL,NULL,NULL),
(273,'Матрос Музыкант','76561198336389500','2026-03-16 20:14:18',11056,0,0,'[]',NULL,NULL,NULL,NULL),
(274,'ЧЕРНАЯ БОРОДА','76561199346213778','2026-02-08 20:23:40',89,0,0,'[]',NULL,NULL,NULL,NULL),
(275,'DEMOKRATIK','76561199093290930','2026-02-08 20:49:50',44,0,0,'[]',NULL,NULL,NULL,NULL),
(276,'боб','76561198076391647','2026-03-19 07:03:46',5863,0,0,'[]',NULL,NULL,NULL,NULL),
(277,'Abyssal Тэтэк','76561199095698093','2026-02-11 19:22:40',38,0,0,'[]',NULL,NULL,NULL,NULL),
(278,'Кирилл','76561199403256023','2026-02-09 19:32:46',818,0,0,'[]',NULL,NULL,NULL,NULL),
(279,'VoidStep','76561199535433438','2026-02-09 20:45:07',66,0,0,'[]',NULL,NULL,NULL,NULL),
(280,'Joel244','76561199208197492','2026-03-15 06:21:07',2890,0,0,'[]',NULL,NULL,NULL,NULL),
(281,'Alkash','76561198421841636','2026-03-21 09:35:12',106,0,0,'[]',NULL,NULL,NULL,NULL),
(282,'Матрос Шутник','76561198896938215','2026-02-11 20:04:00',8251,0,0,'[]',NULL,NULL,NULL,NULL),
(283,'Святой Андрей','76561198981797495','2026-02-13 11:19:42',7138,0,0,'[]',NULL,NULL,NULL,NULL),
(284,'Батюшка Мефидрон','76561198860115104','2026-03-13 13:29:37',46300,0,0,'[]',NULL,NULL,NULL,NULL),
(285,'artifice','76561199754407815','2026-02-14 13:47:48',3641,0,0,'[]',NULL,NULL,NULL,NULL),
(286,'nik1ta22844','76561199481623061','2026-03-17 20:21:05',71981,0,0,'[]',NULL,NULL,NULL,NULL),
(287,'Дырявый сыр','76561199393030834','2026-02-11 16:40:00',259,0,0,'[]',NULL,NULL,NULL,NULL),
(288,'null','76561199817005710','2026-03-03 12:13:16',967,0,0,'[]',NULL,NULL,NULL,NULL),
(289,'HuangTheWeakII','76561198282591765','2026-02-10 13:08:18',119,0,0,'[]',NULL,NULL,NULL,NULL),
(290,'Пётр Громов','76561199417978124','2026-02-12 18:40:27',6081,0,0,'[]',NULL,NULL,NULL,NULL),
(291,'MIPPO','76561199636873602','2026-02-13 18:05:03',10290,0,0,'[]',NULL,NULL,NULL,NULL),
(292,'Джеймс Магни','76561198793854103','2026-03-09 12:25:58',23867,0,0,'[]',NULL,NULL,NULL,NULL),
(293,'Re1axPlay','76561198348287843','2026-02-14 13:56:50',1669,0,0,'[]',NULL,NULL,NULL,NULL),
(294,'Матрос Андрей Папазогло','76561198048463842','2026-02-10 15:37:29',2023,0,0,'[]',NULL,NULL,NULL,NULL),
(295,'Виктор Чекист','76561199019923760','2026-03-09 11:59:44',8345,0,0,'[]',NULL,NULL,NULL,NULL),
(296,'baxmetivan','76561199619141174','2026-02-10 16:27:39',1946,0,0,'[]',NULL,NULL,NULL,NULL),
(297,'beerman','76561199787314404','2026-02-11 13:06:20',471,0,0,'[]',NULL,NULL,NULL,NULL),
(298,'radmir993322','76561199819272437','2026-02-11 13:07:03',295,0,0,'[]',NULL,NULL,NULL,NULL),
(299,'juratret16','76561199804952599','2026-02-11 14:18:25',55,0,0,'[]',NULL,NULL,NULL,NULL),
(300,'QWERTY','76561199657553746','2026-02-11 14:51:25',675,0,0,'[]',NULL,NULL,NULL,NULL),
(301,'Mi','76561199211046867','2026-02-15 21:00:21',2754,0,0,'[]',NULL,NULL,NULL,NULL),
(302,'Профессор Кругло','76561199856915415','2026-02-13 16:47:03',20752,0,0,'[]',NULL,NULL,NULL,NULL),
(303,'Олег Вмятина','76561199013807804','2026-02-11 17:10:30',3656,0,0,'[]',NULL,NULL,NULL,NULL),
(304,'i kill myself','76561199200026314','2026-02-13 16:39:13',3335,0,0,'[]',NULL,NULL,NULL,NULL),
(305,'zxcbankay','76561199158354299','2026-02-13 16:39:01',3296,0,0,'[]',NULL,NULL,NULL,NULL),
(306,'Kasa','76561198109714567','2026-02-14 14:03:34',9643,0,0,'[]',NULL,NULL,NULL,NULL),
(307,'Бабі Джон','76561199239916445','2026-02-11 18:01:41',2302,0,0,'[]',NULL,NULL,NULL,NULL),
(308,'Михаил Хонкович','76561199503364853','2026-02-11 17:54:54',5,0,0,'[]',NULL,NULL,NULL,NULL),
(309,'rty000pro','76561199146598761','2026-02-11 18:50:35',2791,0,0,'[]',NULL,NULL,NULL,NULL),
(310,'Саймон Стойн','76561199216415513','2026-02-11 19:00:45',2723,0,0,'[]',NULL,NULL,NULL,NULL),
(311,'Clown','76561198843907128','2026-02-11 18:20:52',154,0,0,'[]',NULL,NULL,NULL,NULL),
(312,'Матрос Немой','76561199221941164','2026-03-16 16:24:56',203627,0,1,'[]',NULL,NULL,NULL,NULL),
(313,'Mohav','76561199225299913','2026-02-12 17:17:22',606,0,0,'[]',NULL,NULL,NULL,NULL),
(314,'Bydlo  Scytyjskie','76561199388873317','2026-03-22 16:29:49',410920,1,1,'[]',NULL,NULL,NULL,NULL),
(315,'Хенк Андерсон','76561199202880622','2026-03-08 12:25:54',1909,0,0,'[]',NULL,NULL,NULL,NULL),
(316,'Борис Чечнек','76561199251846016','2026-03-02 08:16:49',5370,0,0,'[]',NULL,NULL,NULL,NULL),
(317,'Матрос  Антон Медведев','76561198128476418','2026-03-18 11:18:51',508,0,0,'[]',NULL,NULL,NULL,NULL),
(318,'Hard   cray','76561199579632266','2026-03-07 15:18:46',89586,0,0,'[]',NULL,NULL,NULL,NULL),
(319,'Alren','76561198051054780','2026-02-12 14:31:10',61,0,0,'[]',NULL,NULL,NULL,NULL),
(320,'LordAkkord','76561199173728336','2026-02-13 11:47:49',11298,0,0,'[]',NULL,NULL,NULL,NULL),
(321,'Plexx_','76561199681821126','2026-02-18 20:32:36',22521,0,0,'[]',NULL,NULL,NULL,NULL),
(322,'вода немецкая','76561199247155947','2026-02-23 15:58:55',101,0,0,'[]',NULL,NULL,NULL,NULL),
(323,'Валерий Капустин','76561199242610615','2026-02-12 16:22:23',681,0,0,'[]',NULL,NULL,NULL,NULL),
(324,'Kot','76561199045591703','2026-03-14 09:34:27',3216,0,0,'[]',NULL,NULL,NULL,NULL),
(325,'z333','76561198871950975','2026-02-12 16:22:33',86,0,0,'[]',NULL,NULL,NULL,NULL),
(326,'АМОРАЛЬНЫЙ2','76561199796438222','2026-02-12 16:39:52',38,0,0,'[]',NULL,NULL,NULL,NULL),
(327,'кловун','76561198190743093','2026-03-07 06:18:49',41713,0,0,'[]',NULL,NULL,NULL,NULL),
(328,'pablo 1.2','76561199092125656','2026-02-12 17:35:35',307,0,0,'[]',NULL,NULL,NULL,NULL),
(329,'Больной Ублюдок','76561198991345730','2026-02-12 17:42:35',432,0,0,'[]',NULL,NULL,NULL,NULL),
(330,'Матрос Килла','76561198885307637','2026-03-22 19:06:30',381795,0,1,'[]',NULL,NULL,NULL,NULL),
(331,'Александр Смирнов','76561198296585324','2026-02-12 18:40:35',1885,0,0,'[]',NULL,NULL,NULL,NULL),
(332,'Лт.Арчи Рур','76561199061264160','2026-02-12 18:40:32',1872,0,0,'[]',NULL,NULL,NULL,NULL),
(333,'Данил Макаров','76561199038762116','2026-02-12 18:41:35',1924,0,0,'[]',NULL,NULL,NULL,NULL),
(334,'Сер Ползун','76561199082533359','2026-02-12 18:40:39',1866,0,0,'[]',NULL,NULL,NULL,NULL),
(335,'SKACDAN','76561198882279296','2026-02-12 18:40:43',1824,0,0,'[]',NULL,NULL,NULL,NULL),
(336,'Aurora','76561199000649856','2026-02-12 18:40:43',1818,0,0,'[]',NULL,NULL,NULL,NULL),
(337,'S.B.Q.M Велизарий Коул','76561199026002176','2026-02-13 10:03:04',1791,0,0,'[]',NULL,NULL,NULL,NULL),
(338,'Egororck','76561198941597015','2026-02-12 18:40:42',1739,0,0,'[]',NULL,NULL,NULL,NULL),
(339,'Moriarty','76561199341609105','2026-02-12 18:40:35',1701,0,0,'[]',NULL,NULL,NULL,NULL),
(340,'Jan  lean','76561198031417324','2026-02-12 18:40:46',1343,0,0,'[]',NULL,NULL,NULL,NULL),
(341,'Hunter','76561199115791855','2026-02-12 18:22:41',135,0,0,'[]',NULL,NULL,NULL,NULL),
(342,'xena','76561199673170329','2026-02-12 18:23:29',24,0,0,'[]',NULL,NULL,NULL,NULL),
(343,'Hank','76561199547045004','2026-02-13 11:06:58',11,0,0,'[]',NULL,NULL,NULL,NULL),
(344,'Матрос Ян Кох','76561198061910004','2026-02-18 16:37:26',14880,0,0,'[]',NULL,NULL,NULL,NULL),
(345,'Матрос Джон Харди','76561199206720937','2026-02-13 11:47:42',1371,0,0,'[]',NULL,NULL,NULL,NULL),
(346,'tapok','76561199218553085','2026-02-13 11:31:08',9,0,0,'[]',NULL,NULL,NULL,NULL),
(347,'BurntTail','76561198980965157','2026-02-14 11:57:37',232,0,0,'[]',NULL,NULL,NULL,NULL),
(348,'Korimary_(Kori)','76561199718757719','2026-02-14 12:49:51',17927,0,0,'[]',NULL,NULL,NULL,NULL),
(349,'BreBlastic','76561199799935833','2026-02-13 13:28:42',3874,0,0,'[]',NULL,NULL,NULL,NULL),
(350,'runt1g','76561199835565583','2026-02-13 13:37:42',39,0,0,'[]',NULL,NULL,NULL,NULL),
(351,'Nemah','76561198794603038','2026-02-13 13:41:26',4,0,0,'[]',NULL,NULL,NULL,NULL),
(352,'Dushnila','76561198033854795','2026-02-13 15:33:51',6653,0,0,'[]',NULL,NULL,NULL,NULL),
(353,'Матрос 1-ст Никита Кирченко','76561198976484366','2026-03-13 17:31:39',139434,0,1,'[]',NULL,NULL,NULL,NULL),
(354,'KittyCatYT','76561199812003190','2026-03-07 12:16:46',67001,0,0,'[]',NULL,NULL,NULL,NULL),
(355,'Kage','76561199558701598','2026-03-17 16:59:48',5839,0,0,'[]',NULL,NULL,NULL,NULL),
(356,'HIdi','76561199497248837','2026-03-20 17:46:25',8447,0,0,'[]',NULL,NULL,NULL,NULL),
(357,'Romzet','76561198063194860','2026-02-13 16:10:29',812,0,0,'[]',NULL,NULL,NULL,NULL),
(358,'Dodik','76561198818061843','2026-02-14 12:34:47',6208,0,0,'[]',NULL,NULL,NULL,NULL),
(359,'Герсон Форд','76561199444601869','2026-02-13 16:06:10',81,0,0,'[]',NULL,NULL,NULL,NULL),
(360,'Матрос  Макар Вишняков','76561199079276097','2026-03-08 11:38:23',29876,0,0,'[]',NULL,NULL,NULL,NULL),
(361,'p1n0ch3t','76561199004568871','2026-02-13 16:39:52',1547,0,0,'[]',NULL,NULL,NULL,NULL),
(362,'VYMOGATEL','76561198161912576','2026-02-13 16:39:52',1535,0,0,'[]',NULL,NULL,NULL,NULL),
(363,'Кронг Лебедев','76561199039689455','2026-02-13 16:26:23',41,0,0,'[]',NULL,NULL,NULL,NULL),
(364,'ВИТЯ MVP','76561198445740145','2026-02-16 13:02:13',8417,0,0,'[]',NULL,NULL,NULL,NULL),
(365,'Vissao','76561199210364556','2026-02-13 18:55:07',4413,0,0,'[]',NULL,NULL,NULL,NULL),
(366,'Владислав','76561199582523691','2026-03-08 13:20:03',9352,0,0,'[]',NULL,NULL,NULL,NULL),
(367,'Иван Изумрудов','76561199138874086','2026-03-19 17:13:21',23147,0,0,'[]',NULL,NULL,NULL,NULL),
(368,'_dilda_krakodilda_','76561199413347199','2026-02-18 12:46:19',292,0,0,'[]',NULL,NULL,NULL,NULL),
(369,'Матрос Велес','76561198009099979','2026-03-11 15:25:34',54201,2,2,'[]',NULL,NULL,NULL,NULL),
(370,'Om490','76561199468135157','2026-02-13 19:06:10',8,0,0,'[]',NULL,NULL,NULL,NULL),
(371,'AlR','76561198323719501','2026-02-13 21:56:55',382,0,0,'[]',NULL,NULL,NULL,NULL),
(372,'killer228','76561199556677345','2026-02-13 19:13:51',153,0,0,'[]',NULL,NULL,NULL,NULL),
(373,'Octopi','76561199427607717','2026-02-13 19:33:47',20,0,0,'[]',NULL,NULL,NULL,NULL),
(374,'Liko','76561199162485873','2026-02-13 20:01:33',623,0,0,'[]',NULL,NULL,NULL,NULL),
(375,'Матрос Джон Элдритч','76561198193988063','2026-02-13 21:56:28',355,0,0,'[]',NULL,NULL,NULL,NULL),
(376,'Мимен','76561199688223211','2026-02-16 18:15:04',3413,0,0,'[]',NULL,NULL,NULL,NULL),
(377,'V1','76561199811563615','2026-02-14 09:01:02',144,0,0,'[]',NULL,NULL,NULL,NULL),
(378,'Tvorojeck228','76561199470200828','2026-02-14 09:05:50',128,0,0,'[]',NULL,NULL,NULL,NULL),
(379,'DrUg_MiShEk','76561198879989369','2026-02-14 09:07:58',15,0,0,'[]',NULL,NULL,NULL,NULL),
(380,'WoaRobby','76561198237681348','2026-02-14 09:14:58',114,0,0,'[]',NULL,NULL,NULL,NULL),
(381,'DROZHI','76561198206688460','2026-02-14 11:21:14',6097,0,0,'[]',NULL,NULL,NULL,NULL),
(382,'Прапор','76561199538353583','2026-02-14 09:44:30',199,0,0,'[]',NULL,NULL,NULL,NULL),
(383,'Матрос','76561199757386572','2026-02-14 09:53:55',179,0,0,'[]',NULL,NULL,NULL,NULL),
(384,'BOREY','76561198154489552','2026-02-15 17:28:24',10769,0,0,'[]',NULL,NULL,NULL,NULL),
(385,'Kengurator','76561198011148196','2026-03-21 12:33:32',29232,0,0,'[]',NULL,NULL,NULL,NULL),
(386,'NightGhost5791','76561199061557033','2026-02-14 10:19:51',94,0,0,'[]',NULL,NULL,NULL,NULL),
(387,'Арсен','76561199244498592','2026-03-21 14:34:00',131377,1,1,'[]',NULL,NULL,NULL,NULL),
(388,'genialn7_chel','76561199513688334','2026-02-14 10:40:08',191,0,0,'[]',NULL,NULL,NULL,NULL),
(389,'Агроном','76561198334682885','2026-02-14 10:51:31',180,0,0,'[]',NULL,NULL,NULL,NULL),
(390,'Dead drummer','76561198173113739','2026-02-14 10:51:31',166,0,0,'[]',NULL,NULL,NULL,NULL),
(391,'Антон Чигурович','76561199198303373','2026-03-13 16:20:02',570,0,0,'[]',NULL,NULL,NULL,NULL),
(392,'Доктор Ливси','76561199881176872','2026-02-17 12:26:16',3002,0,0,'[]',NULL,NULL,NULL,NULL),
(393,'старик людоед','76561199241163013','2026-02-14 12:30:27',288,0,0,'[]',NULL,NULL,NULL,NULL),
(394,'Доусон Лайфвеб','76561199376529105','2026-02-14 13:58:15',4457,0,0,'[]',NULL,NULL,NULL,NULL),
(395,'Антон Чигур','76561199476946898','2026-03-08 11:14:38',44971,0,0,'[]',NULL,NULL,NULL,NULL),
(396,'Бо Син','76561198853827594','2026-02-14 13:40:38',196,0,0,'[]',NULL,NULL,NULL,NULL),
(397,'folk','76561198171553266','2026-02-14 13:55:58',14,0,0,'[]',NULL,NULL,NULL,NULL),
(398,'Floyd55','76561198157182741','2026-02-14 13:55:55',4,0,0,'[]',NULL,NULL,NULL,NULL),
(399,'Старший офицер СБ (21)Legioner','76561198763214329','2026-02-15 09:08:17',103,0,0,'[]',NULL,NULL,NULL,NULL),
(400,'Moonrover','76561198165689294','2026-02-15 12:35:04',439,0,0,'[]',NULL,NULL,NULL,NULL),
(401,'Френк Никольсон','76561199112414084','2026-02-15 12:37:54',589,0,0,'[]',NULL,NULL,NULL,NULL),
(402,'Уличная Магия','76561198166578531','2026-02-15 16:12:04',8888,0,0,'[]',NULL,NULL,NULL,NULL),
(403,'Iphone 14 Pro Max','76561198334931747','2026-03-21 11:02:01',21090,0,0,'[]',NULL,NULL,NULL,NULL),
(404,'doggomaster','76561199135045765','2026-03-21 10:58:39',9047,0,0,'[]',NULL,NULL,NULL,NULL),
(405,'Нормалды','76561199050944403','2026-03-21 11:36:43',9868,0,0,'[]',NULL,NULL,NULL,NULL),
(406,'luxdar','76561198802701562','2026-02-18 18:09:16',18044,0,0,'[]',NULL,NULL,NULL,NULL),
(407,'жорик адвакат сво','76561198858505038','2026-02-15 15:22:19',833,0,0,'[]',NULL,NULL,NULL,NULL),
(408,'no matter','76561199088990288','2026-03-08 09:01:13',1854,0,0,'[]',NULL,NULL,NULL,NULL),
(409,'порно актер','76561199348028860','2026-02-15 15:14:54',81,0,0,'[]',NULL,NULL,NULL,NULL),
(410,'бяка','76561198358454161','2026-02-16 15:10:49',15183,0,0,'[]',NULL,NULL,NULL,NULL),
(411,'манабрейк по копу','76561199439544121','2026-02-19 18:26:09',91862,0,1,'[]',NULL,NULL,NULL,NULL),
(412,'Eblusha','76561198774431498','2026-03-12 14:53:46',4173,0,0,'[]',NULL,NULL,NULL,NULL),
(413,'Матрос Отсос','76561198998540412','2026-02-15 17:13:50',249,0,0,'[]',NULL,NULL,NULL,NULL),
(414,'Wi\'adel','76561199121824232','2026-02-15 17:13:33',72,0,0,'[]',NULL,NULL,NULL,NULL),
(415,'Ляпис','76561199783425146','2026-02-15 17:28:14',224,0,0,'[]',NULL,NULL,NULL,NULL),
(416,'Бортовой медик Николай','76561199422801300','2026-02-15 19:30:57',4788,0,0,'[]',NULL,NULL,NULL,NULL),
(417,'ivostap413','76561198751037835','2026-03-20 20:17:54',60754,0,0,'[]',NULL,NULL,NULL,NULL),
(418,'Bigby____wolf','76561198199027781','2026-02-15 19:19:55',748,0,0,'[]',NULL,NULL,NULL,NULL),
(419,'風','76561198387453261','2026-02-19 21:10:40',10671,0,0,'[]',NULL,NULL,NULL,NULL),
(420,'Матрос Вереск','76561199346987654','2026-02-15 22:27:24',4298,0,0,'[]',NULL,NULL,NULL,NULL),
(421,'Матрос 2-ст Никита Тронин','76561199407695375','2026-02-25 18:47:39',85177,1,1,'[]',NULL,NULL,NULL,NULL),
(422,'IS3','76561198048027537','2026-02-17 21:22:28',305,0,0,'[]',NULL,NULL,NULL,NULL),
(423,'MEFEDRON','76561199393694813','2026-02-15 23:40:29',0,0,0,'[]',NULL,NULL,NULL,NULL),
(424,'MrSmile','76561199546680658','2026-02-16 11:18:20',239,0,0,'[]',NULL,NULL,NULL,NULL),
(425,'Старпом Лом','76561198145022724','2026-02-16 11:58:47',139,0,0,'[]',NULL,NULL,NULL,NULL),
(426,'MaLoi','76561199468332632','2026-02-16 12:33:59',302,0,0,'[]',NULL,NULL,NULL,NULL),
(427,'Егорульма','76561198848688039','2026-03-18 10:08:40',7587,0,0,'[]',NULL,NULL,NULL,NULL),
(428,'ХУЙ МОРЖЕВЫЙ','76561198139727272','2026-02-16 15:03:47',98,0,0,'[]',NULL,NULL,NULL,NULL),
(429,'JackOBot','76561199508341330','2026-02-16 15:02:22',10,0,0,'[]',NULL,NULL,NULL,NULL),
(430,'boxforfoxRu','76561199580060628','2026-02-16 17:01:22',61,0,0,'[]',NULL,NULL,NULL,NULL),
(431,'пупсик','76561199772017817','2026-03-17 16:18:10',26,0,0,'[]',NULL,NULL,NULL,NULL),
(432,'arhanvovan','76561199705564391','2026-02-19 18:42:55',1445,0,0,'[]',NULL,NULL,NULL,NULL),
(433,'Матрос Харло Петран','76561199560798903','2026-03-15 11:16:36',20463,0,0,'[]',NULL,NULL,NULL,NULL),
(434,'Кукареку','76561199655040969','2026-02-18 21:30:02',19379,0,0,'[]',NULL,NULL,NULL,NULL),
(435,'MonolitChild','76561199015492936','2026-02-16 21:33:48',325,0,0,'[]',NULL,NULL,NULL,NULL),
(436,'Утонувший','76561199202217891','2026-02-19 17:24:26',60042,0,0,'[]',NULL,NULL,NULL,NULL),
(437,'SHADOW','76561198993268797','2026-02-17 11:28:59',477,0,0,'[]',NULL,NULL,NULL,NULL),
(438,'Chaos Master','76561198930812109','2026-02-17 13:08:15',4647,0,0,'[]',NULL,NULL,NULL,NULL),
(439,'Генрих','76561199184041569','2026-03-22 13:29:33',119285,2,2,'[]',NULL,NULL,NULL,NULL),
(440,'101wadik101','76561198034609594','2026-02-19 14:32:28',793,0,0,'[]',NULL,NULL,NULL,NULL),
(441,'Генерал майонез','76561199090092376','2026-02-17 13:07:52',2715,0,0,'[]',NULL,NULL,NULL,NULL),
(442,'Максим Галкин','76561199651111973','2026-02-28 22:37:17',2386,0,0,'[]',NULL,NULL,NULL,NULL),
(443,'Никола Лютый','76561199055830084','2026-02-21 14:33:50',10954,0,0,'[]',NULL,NULL,NULL,NULL),
(444,'Ку-клукс','76561199172308731','2026-02-17 15:12:14',2708,0,0,'[]',NULL,NULL,NULL,NULL),
(445,'Артём Капс','76561199248380269','2026-02-20 13:36:35',5097,0,0,'[]',NULL,NULL,NULL,NULL),
(446,'Матрос 2-ст Филипп Беляков','76561197989658089','2026-02-21 13:08:07',3966,0,0,'[]',NULL,NULL,NULL,NULL),
(447,'Гендальф Белый','76561199200467415','2026-03-15 14:48:00',9078,0,0,'[]',NULL,NULL,NULL,NULL),
(448,'Karabas','76561198085142596','2026-02-17 16:26:23',3380,0,0,'[]',NULL,NULL,NULL,NULL),
(449,'Kramola','76561198305502553','2026-03-03 17:44:51',55758,0,0,'[]',NULL,NULL,NULL,NULL),
(450,'Матрас Илья Кузьмин','76561199089068781','2026-02-17 16:34:22',51,0,0,'[]',NULL,NULL,NULL,NULL),
(451,'Ironcrafter30','76561198866079374','2026-02-17 17:32:15',0,0,0,'[]',NULL,NULL,NULL,NULL),
(452,'bif','76561198802296729','2026-02-17 17:59:21',71,0,0,'[]',NULL,NULL,NULL,NULL),
(453,'Генрих Грауз','76561198049477595','2026-02-17 18:38:54',425,0,0,'[]',NULL,NULL,NULL,NULL),
(454,'BloodReaperAr','76561198851718430','2026-02-17 18:47:08',62,0,0,'[]',NULL,NULL,NULL,NULL),
(455,'Celica','76561199203127612','2026-02-17 19:21:49',130,0,0,'[]',NULL,NULL,NULL,NULL),
(456,'VALL','76561199117357041','2026-02-17 19:23:25',182,0,0,'[]',NULL,NULL,NULL,NULL),
(457,'Space2073','76561199545946552','2026-02-17 22:09:28',563,0,0,'[]',NULL,NULL,NULL,NULL),
(458,'NEkoChaN','76561199071205202','2026-02-17 23:09:40',3895,0,0,'[]',NULL,NULL,NULL,NULL),
(459,'Casta','76561199153380290','2026-02-17 22:42:19',472,0,0,'[]',NULL,NULL,NULL,NULL),
(460,'skitariimaister','76561199696409052','2026-02-18 13:31:29',935,0,0,'[]',NULL,NULL,NULL,NULL),
(461,'Матрос  Айс Крас','76561198253981746','2026-03-07 11:23:40',9528,0,0,'[]',NULL,NULL,NULL,NULL),
(462,'Саша Кошков','76561199484916225','2026-02-27 12:31:22',44,0,0,'[]',NULL,NULL,NULL,NULL),
(463,'seyt','76561199559538118','2026-02-18 14:24:44',14,0,0,'[]',NULL,NULL,NULL,NULL),
(464,'filipprijic9','76561199669724289','2026-02-18 14:28:22',16,0,0,'[]',NULL,NULL,NULL,NULL),
(465,'Atom','76561199542907646','2026-02-18 14:40:07',15,0,0,'[]',NULL,NULL,NULL,NULL),
(466,'stalas','76561199096822522','2026-02-18 14:47:24',13,0,0,'[]',NULL,NULL,NULL,NULL),
(467,'Xatelatyn','76561198858370408','2026-02-18 16:42:19',5193,0,0,'[]',NULL,NULL,NULL,NULL),
(468,'korovan','76561198306095731','2026-02-18 16:42:23',5194,0,0,'[]',NULL,NULL,NULL,NULL),
(469,'artem228204','76561198977863388','2026-02-18 15:28:02',351,0,0,'[]',NULL,NULL,NULL,NULL),
(470,'Mahtikere','76561197998861373','2026-02-18 15:40:57',55,0,0,'[]',NULL,NULL,NULL,NULL),
(471,'Alerka','76561198282141305','2026-02-18 16:40:46',2186,0,0,'[]',NULL,NULL,NULL,NULL),
(472,'Марк Август','76561198444261065','2026-02-18 16:43:35',1929,0,0,'[]',NULL,NULL,NULL,NULL),
(473,'мазафака','76561199063497422','2026-02-18 18:43:10',5589,0,0,'[]',NULL,NULL,NULL,NULL),
(474,'ГЛЕБ КУНИ (ЭГОИСТ)','76561199624327025','2026-02-18 17:19:40',6,0,0,'[]',NULL,NULL,NULL,NULL),
(475,'ЖРИ МЯСО','76561199518795702','2026-02-18 17:30:02',205,0,0,'[]',NULL,NULL,NULL,NULL),
(476,'Ataki','76561199069285449','2026-02-19 21:05:21',5124,0,0,'[]',NULL,NULL,NULL,NULL),
(477,'Матрос Кот','76561198099055397','2026-02-18 17:55:17',175,0,0,'[]',NULL,NULL,NULL,NULL),
(478,'Ghost_PUFU','76561199508866000','2026-02-19 19:59:34',15172,0,0,'[]',NULL,NULL,NULL,NULL),
(479,'dogon','76561199860544946','2026-02-18 19:28:39',8,0,0,'[]',NULL,NULL,NULL,NULL),
(480,'Firi(камикадзе)','76561198782385846','2026-02-19 11:58:18',800,0,0,'[]',NULL,NULL,NULL,NULL),
(481,'Puro','76561198857588024','2026-02-19 02:03:12',69,0,0,'[]',NULL,NULL,NULL,NULL),
(482,'Woolchokus','76561199101031638','2026-03-15 06:21:05',78182,1,1,'[]',NULL,NULL,NULL,NULL),
(483,'ПризрачныйКороль','76561198877652769','2026-02-19 09:32:03',600,0,0,'[]',NULL,NULL,NULL,NULL),
(484,'оузо','76561199229692327','2026-02-19 10:40:09',1395,0,0,'[]',NULL,NULL,NULL,NULL),
(485,'ArtPresidente','76561198020999871','2026-02-19 10:37:26',109,0,0,'[]',NULL,NULL,NULL,NULL),
(486,'Матрос Олег Соболев','76561198925835440','2026-02-19 12:09:04',2127,0,0,'[]',NULL,NULL,NULL,NULL),
(487,'СгнилНахуй','76561199057461524','2026-02-19 12:04:19',136,0,0,'[]',NULL,NULL,NULL,NULL),
(488,'Dimetriy Logis','76561198069637799','2026-02-19 13:44:44',715,0,0,'[]',NULL,NULL,NULL,NULL),
(489,'balls','76561199156626381','2026-02-19 14:22:55',30,0,0,'[]',NULL,NULL,NULL,NULL),
(490,'Ганса Ланда всех убила','76561198992430381','2026-02-19 14:38:01',117,0,0,'[]',NULL,NULL,NULL,NULL),
(491,'пончик','76561198316545661','2026-02-19 15:32:44',126,0,0,'[]',NULL,NULL,NULL,NULL),
(492,'MeLMaN','76561198762381435','2026-02-19 15:38:43',92,0,0,'[]',NULL,NULL,NULL,NULL),
(493,'Джон Матрас','76561199526135596','2026-02-27 10:56:01',3705,0,0,'[]',NULL,NULL,NULL,NULL),
(494,'Naykot Ren','76561198277340510','2026-02-19 17:15:29',231,0,0,'[]',NULL,NULL,NULL,NULL),
(495,'Денис Кузнецов','76561199477935296','2026-02-19 18:15:43',820,0,0,'[]',NULL,NULL,NULL,NULL),
(496,'CapitaineDésastre33','76561198737215753','2026-02-19 18:05:31',79,0,0,'[]',NULL,NULL,NULL,NULL),
(497,'Владанет','76561199481831147','2026-02-19 18:20:17',214,0,0,'[]',NULL,NULL,NULL,NULL),
(498,'oldestpc+','76561199489936818','2026-02-19 21:08:57',6286,0,0,'[]',NULL,NULL,NULL,NULL),
(499,'Xareld','76561198044989813','2026-02-23 18:11:52',917,0,0,'[]',NULL,NULL,NULL,NULL),
(500,'Эстонец','76561199049102039','2026-02-19 19:25:15',10,0,0,'[]',NULL,NULL,NULL,NULL),
(501,'GamerMonke','76561199508577169','2026-02-19 19:30:41',76,0,0,'[]',NULL,NULL,NULL,NULL),
(502,'Metallbender','76561199596663841','2026-02-24 20:36:50',3017,0,0,'[]',NULL,NULL,NULL,NULL),
(503,'Val','76561199604367418','2026-02-23 16:56:41',174,0,0,'[]',NULL,NULL,NULL,NULL),
(504,'Ananasbro','76561199879568105','2026-02-20 08:20:41',2129,0,0,'[]',NULL,NULL,NULL,NULL),
(505,'pasat','76561199177345842','2026-02-20 11:43:30',13225,0,0,'[]',NULL,NULL,NULL,NULL),
(506,'kostiapavlov01','76561199243122706','2026-02-20 10:45:25',3119,0,0,'[]',NULL,NULL,NULL,NULL),
(507,'kamap','76561199738188893','2026-02-20 10:46:38',293,0,0,'[]',NULL,NULL,NULL,NULL),
(508,'mavence','76561198271019551','2026-02-20 11:15:29',53,0,0,'[]',NULL,NULL,NULL,NULL),
(509,'Viiking','76561198041908604','2026-02-20 12:35:38',563,0,0,'[]',NULL,NULL,NULL,NULL),
(510,'Mad Sado-Hari','76561199120902752','2026-02-20 13:30:14',295,0,0,'[]',NULL,NULL,NULL,NULL),
(511,'Don1kaff','76561199386498072','2026-02-20 14:33:06',14,0,0,'[]',NULL,NULL,NULL,NULL),
(512,'SERER','76561199772328128','2026-03-08 21:35:49',1368,0,0,'[]',NULL,NULL,NULL,NULL),
(513,'uta16mily02','76561198736980950','2026-02-20 16:10:54',162,0,0,'[]',NULL,NULL,NULL,NULL),
(514,'Patrick Bateman','76561198881278966','2026-02-20 16:22:48',322,0,0,'[]',NULL,NULL,NULL,NULL),
(515,'*Certer*','76561198140712848','2026-02-20 19:14:30',300,0,0,'[]',NULL,NULL,NULL,NULL),
(516,'Odissey','76561198069471100','2026-02-25 15:23:11',1351,0,0,'[]',NULL,NULL,NULL,NULL),
(517,'Vinny','76561198194414533','2026-02-20 20:09:56',740,0,0,'[]',NULL,NULL,NULL,NULL),
(518,'kokoine','76561199571001985','2026-03-16 19:12:13',12618,0,0,'[]',NULL,NULL,NULL,NULL),
(519,'БОГДАН228','76561199446820411','2026-02-20 20:10:17',295,0,0,'[]',NULL,NULL,NULL,NULL),
(520,'Корнищен','76561199073258476','2026-02-21 07:23:25',49,0,0,'[]',NULL,NULL,NULL,NULL),
(521,'Grab_Traktor','76561199586880049','2026-02-21 07:23:34',46,0,0,'[]',NULL,NULL,NULL,NULL),
(522,'Zhi Zhen Zheng','76561199006778552','2026-02-23 07:55:41',189,0,0,'[]',NULL,NULL,NULL,NULL),
(523,'Матрос Алексей Слизень','76561198841521334','2026-02-21 11:53:31',211,0,0,'[]',NULL,NULL,NULL,NULL),
(524,'Egorikpss','76561198028347983','2026-02-21 10:25:52',8,0,0,'[]',NULL,NULL,NULL,NULL),
(525,'68lh1','76561199427059628','2026-02-21 10:55:17',13,0,0,'[]',NULL,NULL,NULL,NULL),
(526,'Кириллганс  Асенизатс','76561198983429476','2026-02-21 14:25:53',2450,0,0,'[]',NULL,NULL,NULL,NULL),
(527,'Нота','76561199218689881','2026-02-21 11:53:23',1209,0,0,'[]',NULL,NULL,NULL,NULL),
(528,'Арби Буенгуев','76561198750797300','2026-02-21 14:29:29',10263,0,0,'[]',NULL,NULL,NULL,NULL),
(529,'Pофлан','76561199486071456','2026-02-21 13:45:29',6960,0,0,'[]',NULL,NULL,NULL,NULL),
(530,'Матрос Касюпима Ибрагимов','76561199127358895','2026-02-21 11:53:42',187,0,0,'[]',NULL,NULL,NULL,NULL),
(531,'Feldmarshel','76561198053358000','2026-02-25 18:03:59',4369,0,0,'[]',NULL,NULL,NULL,NULL),
(532,'kuzan','76561199818673349','2026-02-21 13:37:34',1066,0,0,'[]',NULL,NULL,NULL,NULL),
(533,'FyLs','76561199186701625','2026-03-22 17:02:55',265064,1,1,'[]',NULL,NULL,NULL,NULL),
(534,'t1ly8','76561199828082269','2026-02-21 13:57:43',79,0,0,'[]',NULL,NULL,NULL,NULL),
(535,'Яков','76561199582429133','2026-02-24 10:02:55',640,0,0,'[]',NULL,NULL,NULL,NULL),
(536,'0staks0','76561199525432533','2026-02-21 21:37:18',18109,0,0,'[]',NULL,NULL,NULL,NULL),
(537,'Матрос Стротег','76561198892131951','2026-03-22 13:13:18',169284,0,0,'[]',NULL,NULL,NULL,NULL),
(538,'Матрос Артём Кинжал','76561198317329459','2026-02-21 18:02:41',819,0,0,'[]',NULL,NULL,NULL,NULL),
(539,'Матрос Пластик','76561199029124210','2026-03-11 16:34:34',47721,0,0,'[]',NULL,NULL,NULL,NULL),
(540,'ProMax2007','76561198042451660','2026-02-21 18:59:24',34,0,0,'[]',NULL,NULL,NULL,NULL),
(541,'Ефрейтор Сруль','76561198986166406','2026-02-21 20:31:31',1823,0,0,'[]',NULL,NULL,NULL,NULL),
(542,'Матрос Норг','76561199271331824','2026-02-21 21:46:03',5681,0,0,'[]',NULL,NULL,NULL,NULL),
(543,'goga','76561199231145847','2026-02-21 20:23:08',78,0,0,'[]',NULL,NULL,NULL,NULL),
(544,'alopoh','76561199522134930','2026-02-23 11:47:45',4844,0,0,'[]',NULL,NULL,NULL,NULL),
(545,'Dubr1s','76561198107739434','2026-02-21 20:51:39',115,0,0,'[]',NULL,NULL,NULL,NULL),
(546,'Матрос Оливер Пирсон','76561199389762396','2026-02-21 21:18:17',233,0,0,'[]',NULL,NULL,NULL,NULL),
(547,'винчеватель кака','76561198401661984','2026-02-21 21:44:47',191,0,0,'[]',NULL,NULL,NULL,NULL),
(548,'rustam.fulcrumair','76561198065676807','2026-03-22 17:22:48',7907,0,0,'[]',NULL,NULL,NULL,NULL),
(549,'tic_tac3000','76561199878462180','2026-02-22 08:10:28',838,0,0,'[]',NULL,NULL,NULL,NULL),
(550,'Morulus_Gladiolus','76561199601193188','2026-02-22 08:30:40',19,0,0,'[]',NULL,NULL,NULL,NULL),
(551,'Задоджил аборт','76561199804788690','2026-02-22 09:31:05',440,0,0,'[]',NULL,NULL,NULL,NULL),
(552,'Матрос Кузьмин','76561198321747919','2026-02-22 10:35:03',2998,0,0,'[]',NULL,NULL,NULL,NULL),
(553,'12','76561198942721250','2026-03-08 09:40:32',604,0,0,'[]',NULL,NULL,NULL,NULL),
(554,'ВкУсНаЯ_ХрЕнЬ','76561198788997389','2026-03-10 15:17:55',6829,0,0,'[]',NULL,NULL,NULL,NULL),
(555,'Peof','76561199684112865','2026-03-21 14:07:03',190,0,0,'[]',NULL,NULL,NULL,NULL),
(556,'юнец','76561198873077036','2026-02-22 13:25:45',6932,0,0,'[]',NULL,NULL,NULL,NULL),
(557,'Gordon','76561199206062147','2026-02-22 12:13:34',0,0,0,'[]',NULL,NULL,NULL,NULL),
(558,'Паштет Угрозов','76561199811805147','2026-03-06 16:39:08',1512,0,0,'[]',NULL,NULL,NULL,NULL),
(559,'Левый Барсик','76561198304771752','2026-03-05 12:47:30',12196,0,0,'[]',NULL,NULL,NULL,NULL),
(560,'Mario','76561198981063673','2026-02-22 14:02:50',234,0,0,'[]',NULL,NULL,NULL,NULL),
(561,'глибаба','76561199681191060','2026-02-22 14:20:05',805,0,0,'[]',NULL,NULL,NULL,NULL),
(562,'рататуй','76561199767456035','2026-02-25 13:04:50',757,0,0,'[]',NULL,NULL,NULL,NULL),
(563,'Matvey_K','76561199853223036','2026-02-22 14:49:18',32,0,0,'[]',NULL,NULL,NULL,NULL),
(564,'Немой','76561199261195900','2026-02-22 14:57:21',62,0,0,'[]',NULL,NULL,NULL,NULL),
(565,'Dynamoon','76561199863230516','2026-02-22 17:29:34',917,0,0,'[]',NULL,NULL,NULL,NULL),
(566,'maestro','76561199243358257','2026-02-22 17:18:15',130,0,0,'[]',NULL,NULL,NULL,NULL),
(567,'sashalesnicov9415','76561199064164990','2026-02-28 06:07:11',8103,0,0,'[]',NULL,NULL,NULL,NULL),
(568,'f','76561199103792215','2026-02-23 12:20:30',8421,0,0,'[]',NULL,NULL,NULL,NULL),
(569,'Stas Nyashkin','76561199796327221','2026-02-22 18:41:58',2502,0,0,'[]',NULL,NULL,NULL,NULL),
(570,'Матрос Карл Икович','76561198747085730','2026-02-22 18:40:20',4036,0,0,'[]',NULL,NULL,NULL,NULL),
(571,'TonY','76561199173968809','2026-02-22 17:34:31',31,0,0,'[]',NULL,NULL,NULL,NULL),
(572,'_Death','76561198774211034','2026-02-22 19:42:50',10,0,0,'[]',NULL,NULL,NULL,NULL),
(573,'Gubygun','76561199043032975','2026-02-23 08:58:27',17,0,0,'[]',NULL,NULL,NULL,NULL),
(574,'матрос харон кинеме','76561199185773593','2026-02-23 09:30:38',103,0,0,'[]',NULL,NULL,NULL,NULL),
(575,'Петя20777','76561199052213855','2026-02-23 11:28:35',2411,0,0,'[]',NULL,NULL,NULL,NULL),
(576,'Veene','76561199847926103','2026-02-23 11:09:35',162,0,0,'[]',NULL,NULL,NULL,NULL),
(577,'Pelmen','76561199629317972','2026-02-23 13:36:23',163,0,0,'[]',NULL,NULL,NULL,NULL),
(578,'Вафледрон','76561199094553645','2026-02-23 13:36:55',182,0,0,'[]',NULL,NULL,NULL,NULL),
(579,'мамачка власа','76561199737995373','2026-02-24 14:24:50',373,0,0,'[]',NULL,NULL,NULL,NULL),
(580,'Dead Inside','76561198280485015','2026-02-23 14:09:28',421,0,0,'[]',NULL,NULL,NULL,NULL),
(581,'Milk And Seledka','76561199045301430','2026-02-23 15:15:03',151,0,0,'[]',NULL,NULL,NULL,NULL),
(582,'Ludwig','76561198353571891','2026-02-25 19:12:53',37842,0,0,'[]',NULL,NULL,NULL,NULL),
(583,'StepanVff','76561199879216275','2026-02-28 12:34:04',46130,0,0,'[]',NULL,NULL,NULL,NULL),
(584,'Skill Issue','76561198970407277','2026-02-24 20:46:07',6043,0,0,'[]',NULL,NULL,NULL,NULL),
(585,'An_cools','76561199181922582','2026-02-27 16:05:51',1127,0,0,'[]',NULL,NULL,NULL,NULL),
(586,'donk2','76561199423798942','2026-02-23 20:33:21',89,0,0,'[]',NULL,NULL,NULL,NULL),
(587,'BogB','76561199180228402','2026-02-23 20:52:41',12,0,0,'[]',NULL,NULL,NULL,NULL),
(588,'yudachi','76561199251286083','2026-02-23 20:52:40',7,0,0,'[]',NULL,NULL,NULL,NULL),
(589,'Leon','76561198018847131','2026-02-23 21:04:37',370,0,0,'[]',NULL,NULL,NULL,NULL),
(590,'Матрос Приколист','76561198020062858','2026-02-24 15:31:16',210,0,0,'[]',NULL,NULL,NULL,NULL),
(591,'Joetoki','76561198126391178','2026-02-24 14:09:07',342,0,0,'[]',NULL,NULL,NULL,NULL),
(592,'Elessar','76561198188850542','2026-02-28 09:40:29',2919,0,0,'[]',NULL,NULL,NULL,NULL),
(593,'Caslee','76561198736668532','2026-02-25 17:52:23',7850,0,0,'[]',NULL,NULL,NULL,NULL),
(594,'pravedi1k','76561199052511934','2026-02-24 19:59:23',4174,0,0,'[]',NULL,NULL,NULL,NULL),
(595,'dfjy','76561198033780603','2026-02-24 19:59:24',4175,0,0,'[]',NULL,NULL,NULL,NULL),
(596,'Ай РПшечка родная прёт во всей..','76561198993441989','2026-02-28 13:33:24',42,0,0,'[]',NULL,NULL,NULL,NULL),
(597,'zhauniarovich','76561198287664891','2026-03-22 19:15:47',214125,1,1,'[]',NULL,NULL,NULL,NULL),
(598,'ChilChel','76561199019594032','2026-02-24 20:01:35',254,0,0,'[]',NULL,NULL,NULL,NULL),
(599,'gopnikopsi','76561199150854603','2026-03-05 13:53:31',2309,0,0,'[]',NULL,NULL,NULL,NULL),
(600,'Chupando-Rico','76561199068090057','2026-02-25 12:31:19',4,0,0,'[]',NULL,NULL,NULL,NULL),
(601,'Клаксон Клаксонович','76561198267324270','2026-02-25 16:10:22',8232,0,0,'[]',NULL,NULL,NULL,NULL),
(602,'Матрос Shirby','76561199830464075','2026-02-25 16:07:45',1270,0,0,'[]',NULL,NULL,NULL,NULL),
(603,'Lloyd3005','76561198397905308','2026-02-25 15:47:01',12,0,0,'[]',NULL,NULL,NULL,NULL),
(604,'ArthurPierce','76561198052973572','2026-02-25 15:53:55',93,0,0,'[]',NULL,NULL,NULL,NULL),
(605,'Kepler-435b','76561199475555735','2026-02-26 14:22:37',1384,0,0,'[]',NULL,NULL,NULL,NULL),
(606,'Матрос Ползун','76561198330753054','2026-02-27 15:53:48',8636,0,0,'[]',NULL,NULL,NULL,NULL),
(607,'Матрос Риггер','76561198160100487','2026-02-25 16:13:02',217,0,0,'[]',NULL,NULL,NULL,NULL),
(608,'Виктор Петрович','76561199230650990','2026-02-25 16:12:54',14,0,0,'[]',NULL,NULL,NULL,NULL),
(609,'Eggy boi','76561198239848660','2026-02-25 17:02:12',115,0,0,'[]',NULL,NULL,NULL,NULL),
(610,'KvOrtIx','76561198770065800','2026-03-22 19:10:07',369954,1,1,'[]',NULL,NULL,NULL,NULL),
(611,'Марко24','76561199582987615','2026-03-07 18:28:04',928,0,0,'[]',NULL,NULL,NULL,NULL),
(612,'sh0w','76561199034159762','2026-02-25 18:10:02',18,0,0,'[]',NULL,NULL,NULL,NULL),
(613,'sh0w','76561198773842370','2026-02-25 18:16:22',214,0,0,'[]',NULL,NULL,NULL,NULL),
(614,'FAKE','76561198253448839','2026-02-25 19:17:04',3548,0,0,'[]',NULL,NULL,NULL,NULL),
(615,'hi\'pupsik','76561199576545454','2026-02-28 10:33:13',1472,0,0,'[]',NULL,NULL,NULL,NULL),
(616,'ZOGOPLER','76561199801818461','2026-02-25 18:57:55',661,0,0,'[]',NULL,NULL,NULL,NULL),
(617,'Григорий','76561199771990869','2026-02-25 19:14:55',70,0,0,'[]',NULL,NULL,NULL,NULL),
(618,'SkyEkotic','76561199473772825','2026-02-26 02:28:32',70,0,0,'[]',NULL,NULL,NULL,NULL),
(619,'Денис Майданов','76561199783509568','2026-02-26 14:13:28',673,0,0,'[]',NULL,NULL,NULL,NULL),
(620,'Machinist','76561198419605429','2026-02-26 14:02:49',3539,0,0,'[]',NULL,NULL,NULL,NULL),
(621,'маленькая свага','76561199186681686','2026-02-26 14:02:43',3475,0,0,'[]',NULL,NULL,NULL,NULL),
(622,'Taldorim','76561199848938667','2026-02-26 14:01:18',2276,0,0,'[]',NULL,NULL,NULL,NULL),
(623,'Пряня','76561198426131399','2026-02-26 13:28:34',7,0,0,'[]',NULL,NULL,NULL,NULL),
(624,'sos30_2.RU','76561199090160004','2026-02-26 13:34:38',51,0,0,'[]',NULL,NULL,NULL,NULL),
(625,'anti lord','76561199092052440','2026-02-26 16:51:49',11871,0,0,'[]',NULL,NULL,NULL,NULL),
(626,'Sotarikollinen','76561198987207523','2026-02-26 14:16:04',776,0,0,'[]',NULL,NULL,NULL,NULL),
(627,'zIza','76561199030142352','2026-02-26 16:46:38',10018,0,0,'[]',NULL,NULL,NULL,NULL),
(628,'Arox232','76561199820307773','2026-02-28 10:32:06',3946,0,0,'[]',NULL,NULL,NULL,NULL),
(629,'Трахов Минет Русланов','76561199129209624','2026-03-06 18:36:44',10739,0,0,'[]',NULL,NULL,NULL,NULL),
(630,'foidslayer337','76561199569357567','2026-02-27 14:40:09',3937,0,0,'[]',NULL,NULL,NULL,NULL),
(631,'Молдавчик','76561198282126417','2026-02-26 17:08:10',775,0,0,'[]',NULL,NULL,NULL,NULL),
(632,'terminatog3000','76561199156773768','2026-02-27 10:07:36',194,0,0,'[]',NULL,NULL,NULL,NULL),
(633,'playable-custom7','76561199230638292','2026-02-27 11:35:39',4418,0,0,'[]',NULL,NULL,NULL,NULL),
(634,'Матрос Михаил Синицын','76561199565039795','2026-02-27 11:05:11',133,0,0,'[]',NULL,NULL,NULL,NULL),
(635,'romasdmitriev','76561199782470637','2026-03-22 19:16:12',6751,0,0,'[]',NULL,NULL,NULL,NULL),
(636,'почесал яйцо','76561199313288712','2026-02-27 13:32:33',2462,0,0,'[]',NULL,NULL,NULL,NULL),
(637,'Krakin','76561199330355970','2026-02-27 13:05:47',338,0,0,'[]',NULL,NULL,NULL,NULL),
(638,'Добрий','76561199885490044','2026-02-27 13:32:40',1297,0,0,'[]',NULL,NULL,NULL,NULL),
(639,'Pahahohlei','76561198135593072','2026-02-27 13:42:51',207,0,0,'[]',NULL,NULL,NULL,NULL),
(640,'Матрос Харибда','76561199009987668','2026-02-27 14:21:56',435,0,0,'[]',NULL,NULL,NULL,NULL),
(641,'тупой гандон','76561199167056149','2026-02-27 14:55:22',125,0,0,'[]',NULL,NULL,NULL,NULL),
(642,'Матрос Филя Юрьич','76561199529153436','2026-02-28 13:34:09',20,0,0,'[]',NULL,NULL,NULL,NULL),
(643,'Mr Soldi','76561199092255281','2026-02-28 10:32:36',4049,0,0,'[]',NULL,NULL,NULL,NULL),
(644,'TheDisSent','76561199438859625','2026-02-28 11:13:41',23,0,0,'[]',NULL,NULL,NULL,NULL),
(645,'fagginator','76561199388666897','2026-02-28 12:32:28',212,0,0,'[]',NULL,NULL,NULL,NULL),
(646,'Nick Ghur','76561199874312531','2026-02-28 12:32:45',158,0,0,'[]',NULL,NULL,NULL,NULL),
(647,'Nepazystu','76561199389323140','2026-02-28 13:32:06',33,0,0,'[]',NULL,NULL,NULL,NULL),
(648,'Lox','76561198895618101','2026-02-28 13:39:30',68,0,0,'[]',NULL,NULL,NULL,NULL),
(649,'Soter_D','76561199864459035','2026-02-28 13:39:38',49,0,0,'[]',NULL,NULL,NULL,NULL),
(650,'Евгений Колотилов','76561199181747588','2026-02-28 13:46:19',422,0,0,'[]',NULL,NULL,NULL,NULL),
(651,'Fort','76561199151043799','2026-03-07 20:47:02',777,0,0,'[]',NULL,NULL,NULL,NULL),
(652,'August','76561199683443510','2026-02-28 13:53:24',47,0,0,'[]',NULL,NULL,NULL,NULL),
(653,'Kratos Ivanovi4','76561199871052194','2026-03-19 05:44:33',24162,0,0,'[]',NULL,NULL,NULL,NULL),
(654,'`_` archimag `_`','76561199806419021','2026-02-28 18:40:30',18,0,0,'[]',NULL,NULL,NULL,NULL),
(655,'22UI22','76561199609312877','2026-02-28 18:40:58',8,0,0,'[]',NULL,NULL,NULL,NULL),
(656,'Матрос Бебрино','76561198999257095','2026-02-28 19:34:29',702,0,0,'[]',NULL,NULL,NULL,NULL),
(657,'Тотс','76561199780529408','2026-02-28 19:23:41',1269,0,0,'[]',NULL,NULL,NULL,NULL),
(658,'Lucas','76561198111973299','2026-02-28 19:17:54',912,0,0,'[]',NULL,NULL,NULL,NULL),
(659,'Матрас Макс Стардев','76561199491015149','2026-03-19 14:26:39',291,0,0,'[]',NULL,NULL,NULL,NULL),
(660,'Адрей Тягов','76561199641659802','2026-02-28 19:21:21',15,0,0,'[]',NULL,NULL,NULL,NULL),
(661,'Дональд Трамп','76561198883897350','2026-02-28 20:01:53',315,0,0,'[]',NULL,NULL,NULL,NULL),
(662,'матрас карл иков банка','76561199482677455','2026-03-08 20:53:36',103,0,0,'[]',NULL,NULL,NULL,NULL),
(663,'ubludus','76561198785753832','2026-02-28 20:48:22',22,0,0,'[]',NULL,NULL,NULL,NULL),
(664,'Злой','76561199036831945','2026-02-28 20:56:58',5,0,0,'[]',NULL,NULL,NULL,NULL),
(665,'картон','76561199024424829','2026-02-28 21:03:30',83,0,0,'[]',NULL,NULL,NULL,NULL),
(666,'Инви Эшфилд','76561198219987895','2026-02-28 23:20:35',7893,0,0,'[]',NULL,NULL,NULL,NULL),
(667,'ytim','76561199558659911','2026-02-28 21:23:26',153,0,0,'[]',NULL,NULL,NULL,NULL),
(668,'無生命DeadEmoJi無生命','76561198858370903','2026-02-28 22:29:16',720,0,0,'[]',NULL,NULL,NULL,NULL),
(669,'Герман Швабрированный','76561199079817526','2026-02-28 22:28:49',540,0,0,'[]',NULL,NULL,NULL,NULL),
(670,'Arthur','76561198083314616','2026-02-28 22:22:29',38,0,0,'[]',NULL,NULL,NULL,NULL),
(671,'Джек Воробей','76561199246649893','2026-03-02 18:09:24',7697,0,0,'[]',NULL,NULL,NULL,NULL),
(672,'P.E.S.H.K.A.','76561198833695727','2026-03-01 09:16:43',243,0,0,'[]',NULL,NULL,NULL,NULL),
(673,'Sexster Morgen','76561199400532154','2026-03-22 19:16:16',50208,0,0,'[]',NULL,NULL,NULL,NULL),
(674,'Rust','76561198744704369','2026-03-01 11:34:03',228,0,0,'[]',NULL,NULL,NULL,NULL),
(675,'Senate','76561198284442940','2026-03-01 11:37:08',374,0,0,'[]',NULL,NULL,NULL,NULL),
(676,'Omar Rakov','76561198050473260','2026-03-01 13:00:17',116,0,0,'[]',NULL,NULL,NULL,NULL),
(677,'Насос','76561199063745747','2026-03-19 13:26:34',22362,0,0,'[]',NULL,NULL,NULL,NULL),
(678,'CarnageSentinel','76561198885900440','2026-03-01 13:36:57',192,0,0,'[]',NULL,NULL,NULL,NULL),
(679,'Чифир','76561198411693656','2026-03-01 14:09:49',197,0,0,'[]',NULL,NULL,NULL,NULL),
(680,'未达到我','76561198980308658','2026-03-01 14:09:10',25,0,0,'[]',NULL,NULL,NULL,NULL),
(681,'Hir-vam','76561198035080132','2026-03-08 14:21:29',12044,0,0,'[]',NULL,NULL,NULL,NULL),
(682,'Георгий Павлович','76561199228635440','2026-03-01 15:39:38',63,0,0,'[]',NULL,NULL,NULL,NULL),
(683,'Shidih','76561199019143501','2026-03-01 15:54:57',6,0,0,'[]',NULL,NULL,NULL,NULL),
(684,'dsefim','76561199195382925','2026-03-01 19:49:12',391,0,0,'[]',NULL,NULL,NULL,NULL),
(685,'Cthulhu','76561198759551873','2026-03-01 19:50:41',94,0,0,'[]',NULL,NULL,NULL,NULL),
(686,'log','76561199840815271','2026-03-02 08:38:32',325,0,0,'[]',NULL,NULL,NULL,NULL),
(687,'трыцын дыцын','76561199096723335','2026-03-03 20:00:04',4914,0,0,'[]',NULL,NULL,NULL,NULL),
(688,'NUMBERONE','76561199627714393','2026-03-03 20:05:34',5097,0,0,'[]',NULL,NULL,NULL,NULL),
(689,'Predo','76561198791860118','2026-03-03 18:42:54',78,0,0,'[]',NULL,NULL,NULL,NULL),
(690,'eritikus.0756','76561199248906257','2026-03-05 13:36:39',932,0,0,'[]',NULL,NULL,NULL,NULL),
(691,'gabriel','76561199225101150','2026-03-06 21:15:59',2419,0,0,'[]',NULL,NULL,NULL,NULL),
(692,'матрос лев табурет','76561198951359589','2026-03-20 15:32:57',10303,0,0,'[]',NULL,NULL,NULL,NULL),
(693,'Аянами Рей','76561199060460823','2026-03-09 15:57:36',46450,0,0,'[]',NULL,NULL,NULL,NULL),
(694,'Zver276','76561199765547539','2026-03-04 14:17:09',2916,0,0,'[]',NULL,NULL,NULL,NULL),
(695,'Spectre7412','76561198839881210','2026-03-04 13:50:02',31,0,0,'[]',NULL,NULL,NULL,NULL),
(696,'the throngler','76561199792564205','2026-03-04 14:46:01',712,0,0,'[]',NULL,NULL,NULL,NULL),
(697,'CoreNi1','76561199497129370','2026-03-05 13:00:19',103,0,0,'[]',NULL,NULL,NULL,NULL),
(698,'Кунислав Пихалыч','76561199227137731','2026-03-05 14:12:40',72,0,0,'[]',NULL,NULL,NULL,NULL),
(699,'Эрен Егер','76561199793917079','2026-03-05 18:21:10',1588,0,0,'[]',NULL,NULL,NULL,NULL),
(700,'Mr_Twister','76561198296272208','2026-03-06 11:23:38',1053,0,0,'[]',NULL,NULL,NULL,NULL),
(701,'holoknayf','76561199773207009','2026-03-05 16:52:40',0,0,0,'[]',NULL,NULL,NULL,NULL),
(702,'Macktraher','76561198242036402','2026-03-06 21:08:01',5866,0,0,'[]',NULL,NULL,NULL,NULL),
(703,'The_0w1_N1ght','76561199238095725','2026-03-07 12:10:40',165,0,0,'[]',NULL,NULL,NULL,NULL),
(704,'Гарри Дьюбуа','76561199183269714','2026-03-06 11:09:03',85,0,0,'[]',NULL,NULL,NULL,NULL),
(705,'Шипостаев','76561199825298301','2026-03-06 11:28:05',419,0,0,'[]',NULL,NULL,NULL,NULL),
(706,'Мышуся','76561198047769772','2026-03-06 11:21:53',220,0,0,'[]',NULL,NULL,NULL,NULL),
(707,'Punker','76561199053086029','2026-03-06 11:43:56',212,0,0,'[]',NULL,NULL,NULL,NULL),
(708,'Arakh Mor','76561199473126164','2026-03-09 12:01:39',5084,0,0,'[]',NULL,NULL,NULL,NULL),
(709,'Матрос Марк Винил','76561199220426097','2026-03-21 12:05:18',3837,0,0,'[]',NULL,NULL,NULL,NULL),
(710,'Матрос Антон Гандонов','76561199801983665','2026-03-06 13:46:17',4815,0,0,'[]',NULL,NULL,NULL,NULL),
(711,'Матрос Ермак','76561199882974461','2026-03-06 13:46:29',4297,0,0,'[]',NULL,NULL,NULL,NULL),
(712,'чиикатило','76561198752675324','2026-03-07 08:24:25',6346,0,0,'[]',NULL,NULL,NULL,NULL),
(713,'TWINKOWOD24','76561198380323319','2026-03-06 17:09:46',1839,0,0,'[]',NULL,NULL,NULL,NULL),
(714,'F-15E Strike Eagle','76561199183746885','2026-03-06 18:44:55',1629,0,0,'[]',NULL,NULL,NULL,NULL),
(715,'РвуГлазаНаЗавтрак','76561199479847755','2026-03-11 14:40:42',2519,0,0,'[]',NULL,NULL,NULL,NULL),
(716,'GooDLivE','76561198022626289','2026-03-06 17:17:13',74,0,0,'[]',NULL,NULL,NULL,NULL),
(717,'Pipel','76561198018994842','2026-03-08 22:06:40',319,0,0,'[]',NULL,NULL,NULL,NULL),
(718,'shotgunmanic','76561197979378835','2026-03-06 18:45:12',851,0,0,'[]',NULL,NULL,NULL,NULL),
(719,'ARtIk_S2','76561199826323894','2026-03-06 19:23:08',63,0,0,'[]',NULL,NULL,NULL,NULL),
(720,'Матрос Сатору Годжо','76561198965873863','2026-03-22 17:38:14',166326,1,1,'[]',NULL,NULL,NULL,NULL),
(721,'Karapys','76561198148953996','2026-03-07 12:12:29',16252,0,0,'[]',NULL,NULL,NULL,NULL),
(722,'jonybe','76561198195544715','2026-03-06 21:15:02',964,0,0,'[]',NULL,NULL,NULL,NULL),
(723,'twiggy','76561198884028715','2026-03-06 21:16:21',244,0,0,'[]',NULL,NULL,NULL,NULL),
(724,'Starley','76561198292881672','2026-03-14 08:56:31',13795,0,0,'[]',NULL,NULL,NULL,NULL),
(725,'vovaserditov','76561199231196443','2026-03-07 13:16:58',15568,0,0,'[]',NULL,NULL,NULL,NULL),
(726,'Роганс Коровелин','76561199698002848','2026-03-09 13:13:57',16114,0,0,'[]',NULL,NULL,NULL,NULL),
(727,'秋风','76561199556850073','2026-03-07 09:00:19',128,0,0,'[]',NULL,NULL,NULL,NULL),
(728,'тима','76561199880868526','2026-03-07 09:30:34',563,0,0,'[]',NULL,NULL,NULL,NULL),
(729,'pahrtj123','76561199659214619','2026-03-07 09:33:16',18,0,0,'[]',NULL,NULL,NULL,NULL),
(730,'Tea','76561199182891983','2026-03-07 09:33:19',12,0,0,'[]',NULL,NULL,NULL,NULL),
(731,'unterdev','76561199155493788','2026-03-07 10:06:57',274,0,0,'[]',NULL,NULL,NULL,NULL),
(732,'kozl1k','76561199531389795','2026-03-07 10:35:57',50,0,0,'[]',NULL,NULL,NULL,NULL),
(733,'Basik','76561199507321032','2026-03-07 10:41:12',251,0,0,'[]',NULL,NULL,NULL,NULL),
(734,'вулканчик','76561198996057873','2026-03-09 10:49:26',20728,0,0,'[]',NULL,NULL,NULL,NULL),
(735,'Noitaman','76561198753622895','2026-03-21 11:29:47',16189,0,0,'[]',NULL,NULL,NULL,NULL),
(736,'Пригожин Женя','76561199212042954','2026-03-09 12:40:12',4002,0,0,'[]',NULL,NULL,NULL,NULL),
(737,'_TORNADO_','76561199466541631','2026-03-15 05:55:54',201,0,0,'[]',NULL,NULL,NULL,NULL),
(738,'Майк Родфелер','76561199190502075','2026-03-07 12:05:09',5,0,0,'[]',NULL,NULL,NULL,NULL),
(739,'Bilbo Beggins','76561198054737490','2026-03-07 12:09:54',280,0,0,'[]',NULL,NULL,NULL,NULL),
(740,'Матрос Сириус Светлов','76561199387344240','2026-03-07 17:47:43',8094,0,0,'[]',NULL,NULL,NULL,NULL),
(741,'Эрик Золл','76561198376528160','2026-03-07 22:34:59',23173,0,0,'[]',NULL,NULL,NULL,NULL),
(742,'SirSugar42','76561199072283190','2026-03-11 17:56:11',21161,0,0,'[]',NULL,NULL,NULL,NULL),
(743,'Григорий кузьма','76561198306907064','2026-03-07 17:15:10',27,0,0,'[]',NULL,NULL,NULL,NULL),
(744,'Alex122122','76561199152664560','2026-03-13 13:30:27',22364,0,0,'[]',NULL,NULL,NULL,NULL),
(745,'Kilvin654','76561199783539709','2026-03-07 20:15:56',8064,0,0,'[]',NULL,NULL,NULL,NULL),
(746,'Матрос Марчело','76561199385817053','2026-03-07 19:50:19',96,0,0,'[]',NULL,NULL,NULL,NULL),
(747,'Матрос Семит','76561199034710311','2026-03-07 19:50:21',44,0,0,'[]',NULL,NULL,NULL,NULL),
(748,'Арти Дулитл','76561198819522513','2026-03-07 20:10:48',41,0,0,'[]',NULL,NULL,NULL,NULL),
(749,'John Q  Roomba','76561198379571715','2026-03-07 20:48:14',779,0,0,'[]',NULL,NULL,NULL,NULL),
(750,'Барри Травмари','76561199426885928','2026-03-22 18:54:43',16014,0,0,'[]',NULL,NULL,NULL,NULL),
(751,'Чудеса','76561199433805627','2026-03-07 22:24:55',4129,0,0,'[]',NULL,NULL,NULL,NULL),
(752,'апчихба','76561199573420966','2026-03-13 15:28:20',50386,0,0,'[]',NULL,NULL,NULL,NULL),
(753,'святой святослав','76561199172674562','2026-03-14 14:18:33',198,0,0,'[]',NULL,NULL,NULL,NULL),
(754,'Jaoger','76561199021802397','2026-03-07 22:03:48',196,0,0,'[]',NULL,NULL,NULL,NULL),
(755,'Tepliy Ananas','76561198421969664','2026-03-14 22:40:03',19311,0,0,'[]',NULL,NULL,NULL,NULL),
(756,'Blue drink','76561198851616787','2026-03-14 22:40:05',19353,0,0,'[]',NULL,NULL,NULL,NULL),
(757,'Ден Миних','76561198105368998','2026-03-11 15:25:09',14465,0,0,'[]',NULL,NULL,NULL,NULL),
(758,'Механик Серега Пармизан','76561198213213937','2026-03-08 01:34:47',189,0,0,'[]',NULL,NULL,NULL,NULL),
(759,'Tertuy','76561199794632564','2026-03-08 09:11:05',2255,0,0,'[]',NULL,NULL,NULL,NULL),
(760,'Крутой_Семён228','76561199073725803','2026-03-08 09:00:10',744,0,0,'[]',NULL,NULL,NULL,NULL),
(761,'reint18','76561198251812180','2026-03-08 09:41:47',2052,0,0,'[]',NULL,NULL,NULL,NULL),
(762,'nullseeker','76561199240887746','2026-03-08 10:10:55',1802,0,0,'[]',NULL,NULL,NULL,NULL),
(763,'Просто Feliks-ks-ks','76561199820721777','2026-03-08 10:07:51',794,0,0,'[]',NULL,NULL,NULL,NULL),
(764,'blurp','76561199176913100','2026-03-14 10:57:15',1939,0,0,'[]',NULL,NULL,NULL,NULL),
(765,'1234','76561198128882412','2026-03-08 13:32:37',5231,0,0,'[]',NULL,NULL,NULL,NULL),
(766,'Костя Соколов','76561199146698956','2026-03-08 15:32:40',10643,0,0,'[]',NULL,NULL,NULL,NULL),
(767,'LyaS','76561198089223932','2026-03-08 12:53:11',87,0,0,'[]',NULL,NULL,NULL,NULL),
(768,'usuri77','76561199098985878','2026-03-08 12:59:04',13,0,0,'[]',NULL,NULL,NULL,NULL),
(769,'Фосикс','76561199175789338','2026-03-08 13:39:27',2310,0,0,'[]',NULL,NULL,NULL,NULL),
(770,'Hara','76561199324059686','2026-03-08 15:02:56',6839,0,0,'[]',NULL,NULL,NULL,NULL),
(771,'Матрос 2D-детонатор','76561199593040194','2026-03-08 13:15:43',59,0,0,'[]',NULL,NULL,NULL,NULL),
(772,'Дмитрий Гарпов','76561199773071552','2026-03-08 14:15:52',657,0,0,'[]',NULL,NULL,NULL,NULL),
(773,'eKSEm *ES','76561198967025507','2026-03-08 14:21:03',172,0,0,'[]',NULL,NULL,NULL,NULL),
(774,'Umbrella','76561199696674820','2026-03-17 20:24:23',19535,0,0,'[]',NULL,NULL,NULL,NULL),
(775,'decasplays','76561199385455958','2026-03-08 15:03:29',73,0,0,'[]',NULL,NULL,NULL,NULL),
(776,'sdmrosa','76561198030410230','2026-03-08 15:03:39',80,0,0,'[]',NULL,NULL,NULL,NULL),
(777,'PrillMrill','76561199096942426','2026-03-13 15:28:21',19984,0,0,'[]',NULL,NULL,NULL,NULL),
(778,'GayGayMan','76561198188960521','2026-03-08 18:56:20',13068,0,0,'[]',NULL,NULL,NULL,NULL),
(779,'Матрос Павел Морозов','76561199182106710','2026-03-21 14:32:40',81260,0,0,'[]',NULL,NULL,NULL,NULL),
(780,'FknGoose','76561199200760090','2026-03-08 15:45:25',25,0,0,'[]',NULL,NULL,NULL,NULL),
(781,'TheDonkey','76561199556824937','2026-03-08 18:33:11',8226,0,0,'[]',NULL,NULL,NULL,NULL),
(782,'MGE snaiper','76561199652289586','2026-03-08 16:34:59',0,0,0,'[]',NULL,NULL,NULL,NULL),
(783,'Матрос  Джон Кларк','76561199432491772','2026-03-08 18:03:25',4186,0,0,'[]',NULL,NULL,NULL,NULL),
(784,'Picules','76561199472987987','2026-03-08 18:56:37',835,0,0,'[]',NULL,NULL,NULL,NULL),
(785,'Bruno Bread','76561199480557697','2026-03-08 19:11:46',161,0,0,'[]',NULL,NULL,NULL,NULL),
(786,'Матрос Олег Семенов','76561199135233640','2026-03-21 15:46:11',20937,0,0,'[]',NULL,NULL,NULL,NULL),
(787,'Максим Шевцов','76561199404880743','2026-03-08 21:51:37',1924,0,0,'[]',NULL,NULL,NULL,NULL),
(788,'Ch1m1m1','76561199593639300','2026-03-08 21:46:54',1453,0,0,'[]',NULL,NULL,NULL,NULL),
(789,'Эболла Пепе','76561199588563251','2026-03-09 06:32:49',55,0,0,'[]',NULL,NULL,NULL,NULL),
(790,'Странник','76561199162303175','2026-03-09 07:42:55',212,0,0,'[]',NULL,NULL,NULL,NULL),
(791,'ZdraVO','76561199209122451','2026-03-09 07:42:02',48,0,0,'[]',NULL,NULL,NULL,NULL),
(792,'Квартет','76561199802577861','2026-03-09 09:25:42',1184,0,0,'[]',NULL,NULL,NULL,NULL),
(793,'BBI','76561199401974240','2026-03-09 09:12:35',203,0,0,'[]',NULL,NULL,NULL,NULL),
(794,'GGI-Hayrut-GGI','76561199466309949','2026-03-09 09:12:52',216,0,0,'[]',NULL,NULL,NULL,NULL),
(795,'Матрос Штефан','76561199529977414','2026-03-09 09:14:34',197,0,0,'[]',NULL,NULL,NULL,NULL),
(796,'ShortPie','76561199113858969','2026-03-09 09:33:34',201,0,0,'[]',NULL,NULL,NULL,NULL),
(797,'Diablo.','76561199231837702','2026-03-09 10:34:40',1274,0,0,'[]',NULL,NULL,NULL,NULL),
(798,'Мойша Ганибал','76561199809082186','2026-03-09 10:44:47',118,0,0,'[]',NULL,NULL,NULL,NULL),
(799,'Flossane','76561198981806818','2026-03-09 11:01:39',424,0,0,'[]',NULL,NULL,NULL,NULL),
(800,'dcuba71','76561199522138101','2026-03-09 11:00:52',345,0,0,'[]',NULL,NULL,NULL,NULL),
(801,'Flamme','76561199518170496','2026-03-09 11:23:11',519,0,0,'[]',NULL,NULL,NULL,NULL),
(802,'foggye','76561199549582572','2026-03-12 14:47:32',6858,0,0,'[]',NULL,NULL,NULL,NULL),
(803,'Тайлер Дерден','76561199875366501','2026-03-09 12:01:34',482,0,0,'[]',NULL,NULL,NULL,NULL),
(804,'W1ssTee','76561199229739101','2026-03-15 11:00:43',2685,0,0,'[]',NULL,NULL,NULL,NULL),
(805,'Роберт Финский','76561199119568375','2026-03-09 11:59:37',10,0,0,'[]',NULL,NULL,NULL,NULL),
(806,'Тесак','76561199125966858','2026-03-09 12:14:54',24,0,0,'[]',NULL,NULL,NULL,NULL),
(807,'Juicy','76561198177394245','2026-03-09 13:05:46',82,0,0,'[]',NULL,NULL,NULL,NULL),
(808,'Матрос Иван Иванов','76561198856809155','2026-03-09 13:32:50',217,0,0,'[]',NULL,NULL,NULL,NULL),
(809,'ArtiBoSSS','76561198013292590','2026-03-09 16:52:13',3543,0,0,'[]',NULL,NULL,NULL,NULL),
(810,'Матрос ПАНК','76561198966586210','2026-03-09 14:27:14',1252,0,0,'[]',NULL,NULL,NULL,NULL),
(811,'Ivanby2005','76561198826413994','2026-03-09 14:07:52',21,0,0,'[]',NULL,NULL,NULL,NULL),
(812,'Жидабор Дристаяр','76561198948964943','2026-03-20 15:54:50',578,0,0,'[]',NULL,NULL,NULL,NULL),
(813,'Override','76561198250154098','2026-03-09 16:51:19',3489,0,0,'[]',NULL,NULL,NULL,NULL),
(814,'Матрос Нос','76561198173156802','2026-03-09 16:51:37',3507,0,0,'[]',NULL,NULL,NULL,NULL),
(815,'my_cat_pumba','76561199141495445','2026-03-09 15:45:08',2111,0,0,'[]',NULL,NULL,NULL,NULL),
(816,'Челебец Михаил Олегович','76561199403413222','2026-03-11 16:45:36',3381,0,0,'[]',NULL,NULL,NULL,NULL),
(817,'Дядя Никита','76561199005881796','2026-03-11 16:10:26',123,0,0,'[]',NULL,NULL,NULL,NULL),
(818,'Ganz','76561198864011874','2026-03-22 18:16:13',456,0,0,'[]',NULL,NULL,NULL,NULL),
(819,'Мансур Стрела','76561198262153300','2026-03-09 17:29:09',63,0,0,'[]',NULL,NULL,NULL,NULL),
(820,'Super Scott','76561199454625362','2026-03-09 18:34:51',4,0,0,'[]',NULL,NULL,NULL,NULL),
(821,'усатый аферист','76561199784133695','2026-03-09 18:40:52',161,0,0,'[]',NULL,NULL,NULL,NULL),
(822,'Mr. Immortal','76561199390172939','2026-03-09 20:10:44',4410,0,0,'[]',NULL,NULL,NULL,NULL),
(823,'виктор геннадьевич','76561198850729643','2026-03-09 19:28:58',234,0,0,'[]',NULL,NULL,NULL,NULL),
(824,'1','76561199388267704','2026-03-10 10:48:58',7,0,0,'[]',NULL,NULL,NULL,NULL),
(825,'TRAP','76561199017392979','2026-03-14 12:15:10',1748,0,0,'[]',NULL,NULL,NULL,NULL),
(826,'аутист','76561198229574020','2026-03-10 14:51:04',66,0,0,'[]',NULL,NULL,NULL,NULL),
(827,'Sam Fisher','76561199467889191','2026-03-10 16:23:48',2125,0,0,'[]',NULL,NULL,NULL,NULL),
(828,'Фиталс','76561199028320878','2026-03-12 16:24:52',8449,0,0,'[]',NULL,NULL,NULL,NULL),
(829,'aksel1602','76561199732385449','2026-03-21 09:25:30',23,0,0,'[]',NULL,NULL,NULL,NULL),
(830,'skolot34','76561198129339504','2026-03-10 18:58:16',8,0,0,'[]',NULL,NULL,NULL,NULL),
(831,'Адмирал Эдик','76561198007142735','2026-03-11 17:55:56',12588,0,0,'[]',NULL,NULL,NULL,NULL),
(832,'Алергия на чаЙ','76561199683635299','2026-03-10 19:50:13',9,0,0,'[]',NULL,NULL,NULL,NULL),
(833,'FYFUUF079','76561199179482657','2026-03-11 06:43:17',10,0,0,'[]',NULL,NULL,NULL,NULL),
(834,'Чистый','76561199525183271','2026-03-11 08:04:03',1343,0,0,'[]',NULL,NULL,NULL,NULL),
(835,'XV-15','76561198966948554','2026-03-14 10:26:09',18671,0,0,'[]',NULL,NULL,NULL,NULL),
(836,'Мамочка Дорета','76561199150318826','2026-03-11 09:16:01',205,0,0,'[]',NULL,NULL,NULL,NULL),
(837,'snoob','76561199683209461','2026-03-13 17:40:51',64,0,0,'[]',NULL,NULL,NULL,NULL),
(838,'kir_guswwi','76561199881595819','2026-03-11 13:53:22',232,0,0,'[]',NULL,NULL,NULL,NULL),
(839,'Boevoi_Pomidor','76561199568351863','2026-03-11 13:50:22',12,0,0,'[]',NULL,NULL,NULL,NULL),
(840,'Kedman','76561199646312237','2026-03-11 16:50:31',10685,0,0,'[]',NULL,NULL,NULL,NULL),
(841,'Roko','76561199580232617','2026-03-17 13:15:23',16297,0,0,'[]',NULL,NULL,NULL,NULL),
(842,'Макс_Павлов','76561199433641830','2026-03-11 14:11:36',11,0,0,'[]',NULL,NULL,NULL,NULL),
(843,'Ермаков','76561199689140802','2026-03-11 14:29:48',531,0,0,'[]',NULL,NULL,NULL,NULL),
(844,'ivan','76561199847160779','2026-03-11 14:52:47',1046,0,0,'[]',NULL,NULL,NULL,NULL),
(845,'Гусь','76561199245516843','2026-03-11 15:12:11',1369,0,0,'[]',NULL,NULL,NULL,NULL),
(846,'hedgehog','76561198874534922','2026-03-17 12:55:27',32808,0,0,'[]',NULL,NULL,NULL,NULL),
(847,'Кяфир','76561198778318933','2026-03-13 17:31:44',553,0,0,'[]',NULL,NULL,NULL,NULL),
(848,'Влас Московский','76561199887779942','2026-03-11 15:11:05',36,0,0,'[]',NULL,NULL,NULL,NULL),
(849,'Chick','76561199522394609','2026-03-11 15:27:25',245,0,0,'[]',NULL,NULL,NULL,NULL),
(850,'KJlOyH','76561199223563659','2026-03-11 15:56:59',231,0,0,'[]',NULL,NULL,NULL,NULL),
(851,'124','76561199740783561','2026-03-11 16:48:36',2536,0,0,'[]',NULL,NULL,NULL,NULL),
(852,'Scrollak','76561199239292410','2026-03-11 16:21:54',132,0,0,'[]',NULL,NULL,NULL,NULL),
(853,'naak616','76561199614427810','2026-03-11 17:39:57',220,0,0,'[]',NULL,NULL,NULL,NULL),
(854,'Zugzug','76561198421944183','2026-03-11 17:41:55',194,0,0,'[]',NULL,NULL,NULL,NULL),
(855,'aebrger','76561198849238937','2026-03-11 17:56:04',168,0,0,'[]',NULL,NULL,NULL,NULL),
(856,'Матрос 2-ст Виталий Николаев','76561198864709901','2026-03-19 19:21:44',21733,0,0,'[]',NULL,NULL,NULL,NULL),
(857,'какой-то чел','76561198734306941','2026-03-12 14:18:11',311,0,0,'[]',NULL,NULL,NULL,NULL),
(858,'Серега Пират','76561199212387980','2026-03-12 13:05:54',336,0,0,'[]',NULL,NULL,NULL,NULL),
(859,'Обер-Лейтенант','76561199225540567','2026-03-12 13:57:26',679,0,0,'[]',NULL,NULL,NULL,NULL),
(860,'Veteran','76561198082144631','2026-03-16 08:30:19',276,0,0,'[]',NULL,NULL,NULL,NULL),
(861,'Pelmen','76561198795309424','2026-03-12 15:04:52',3562,0,0,'[]',NULL,NULL,NULL,NULL),
(862,'Alukard','76561199016762458','2026-03-14 15:41:45',4471,0,0,'[]',NULL,NULL,NULL,NULL),
(863,'MaRin_SZS','76561198767476683','2026-03-12 15:36:24',0,0,0,'[]',NULL,NULL,NULL,NULL),
(864,'Markfixser','76561199488794081','2026-03-12 15:43:17',316,0,0,'[]',NULL,NULL,NULL,NULL),
(865,'Фред Купер','76561198859699842','2026-03-12 16:25:37',1142,0,0,'[]',NULL,NULL,NULL,NULL),
(866,'ARDUBOY','76561199349524094','2026-03-13 17:30:58',10265,0,0,'[]',NULL,NULL,NULL,NULL),
(867,'ADwinch','76561198969588224','2026-03-12 16:21:07',746,0,0,'[]',NULL,NULL,NULL,NULL),
(868,'Матрос Вячеслав Боковой','76561198334715799','2026-03-12 16:48:07',29,0,0,'[]',NULL,NULL,NULL,NULL),
(869,'KOTOBYS','76561199817047150','2026-03-12 17:31:37',2227,0,0,'[]',NULL,NULL,NULL,NULL),
(870,'PELMEN9000','76561199863682703','2026-03-13 04:21:25',69,0,0,'[]',NULL,NULL,NULL,NULL),
(871,'Skam117','76561199200466678','2026-03-13 12:40:42',14643,0,0,'[]',NULL,NULL,NULL,NULL),
(872,'PIVO1','76561199873189921','2026-03-13 09:58:24',2254,0,0,'[]',NULL,NULL,NULL,NULL),
(873,'Катя_dust2_раза','76561199085957332','2026-03-13 10:40:10',4401,0,0,'[]',NULL,NULL,NULL,NULL),
(874,'Famasik','76561199556203740','2026-03-13 10:22:43',712,0,0,'[]',NULL,NULL,NULL,NULL),
(875,'Киви','76561199491540401','2026-03-13 12:01:16',13,0,0,'[]',NULL,NULL,NULL,NULL),
(876,'Ne7nakomca','76561199783470436','2026-03-17 11:57:28',374,0,0,'[]',NULL,NULL,NULL,NULL),
(877,'ZeRo','76561198272906273','2026-03-13 13:28:23',5131,0,0,'[]',NULL,NULL,NULL,NULL),
(878,'Mberd','76561198859932714','2026-03-13 12:03:41',25,0,0,'[]',NULL,NULL,NULL,NULL),
(879,'Doplerganger22','76561199861019191','2026-03-13 13:19:55',189,0,0,'[]',NULL,NULL,NULL,NULL),
(880,'Матрос Kingston Rize','76561199801987239','2026-03-13 12:15:12',39,0,0,'[]',NULL,NULL,NULL,NULL),
(881,'Plaga Medicus','76561198300593777','2026-03-22 16:30:11',83062,1,1,'[]',NULL,NULL,NULL,NULL),
(882,'Karol_Shrekow','76561199145987858','2026-03-13 14:36:00',3599,0,0,'[]',NULL,NULL,NULL,NULL),
(883,'Leonid Kruzen','76561199505665757','2026-03-13 14:49:38',2799,0,0,'[]',NULL,NULL,NULL,NULL),
(884,'Mitchell','76561198009701004','2026-03-13 14:06:36',164,0,0,'[]',NULL,NULL,NULL,NULL),
(885,'m.pok26','76561199665512141','2026-03-15 09:46:49',2217,0,0,'[]',NULL,NULL,NULL,NULL),
(886,'Шипящий Дифтонг','76561198867723866','2026-03-13 14:41:11',193,0,0,'[]',NULL,NULL,NULL,NULL),
(887,'eezyreal','76561198985492537','2026-03-13 16:46:29',7378,0,0,'[]',NULL,NULL,NULL,NULL),
(888,'Матрос Уильям Тейлор','76561199217864352','2026-03-13 16:44:43',4918,0,0,'[]',NULL,NULL,NULL,NULL),
(889,'tuchka','76561198799187744','2026-03-13 15:42:52',9,0,0,'[]',NULL,NULL,NULL,NULL),
(890,'marisamej3','76561199215301505','2026-03-21 11:04:43',45622,0,0,'[]',NULL,NULL,NULL,NULL),
(891,'Colizer','76561199804839133','2026-03-22 17:03:46',22552,0,0,'[]',NULL,NULL,NULL,NULL),
(892,'Lasos','76561199174623044','2026-03-13 17:33:00',96,0,0,'[]',NULL,NULL,NULL,NULL),
(893,'Paton','76561199073631267','2026-03-13 17:57:46',36,0,0,'[]',NULL,NULL,NULL,NULL),
(894,'Chiburpyx','76561198300894370','2026-03-14 01:26:11',70,0,0,'[]',NULL,NULL,NULL,NULL),
(895,'OVKA','76561199570199643','2026-03-14 08:55:52',26,0,0,'[]',NULL,NULL,NULL,NULL),
(896,'LORDillo','76561199772411576','2026-03-14 15:12:52',399,0,0,'[]',NULL,NULL,NULL,NULL),
(897,'popoсhka','76561199591923111','2026-03-14 12:01:53',36,0,0,'[]',NULL,NULL,NULL,NULL),
(898,'avokado5kg','76561198087798938','2026-03-14 10:05:11',343,0,0,'[]',NULL,NULL,NULL,NULL),
(899,'old fucking stump','76561199158976377','2026-03-15 12:17:17',2374,0,0,'[]',NULL,NULL,NULL,NULL),
(900,'Матрос Иван Ворошилов','76561198147676193','2026-03-15 11:51:16',5929,0,0,'[]',NULL,NULL,NULL,NULL),
(901,'Jailer','76561199754242445','2026-03-14 11:40:12',249,0,0,'[]',NULL,NULL,NULL,NULL),
(902,'diamond_355','76561199705216616','2026-03-14 11:40:11',246,0,0,'[]',NULL,NULL,NULL,NULL),
(903,'Alexei','76561199202875746','2026-03-14 11:48:06',18,0,0,'[]',NULL,NULL,NULL,NULL),
(904,'Major Bane','76561199014367975','2026-03-14 12:48:25',2596,0,0,'[]',NULL,NULL,NULL,NULL),
(905,'Максим Лимонов','76561199785715792','2026-03-16 08:56:23',1597,0,0,'[]',NULL,NULL,NULL,NULL),
(906,'Grandmaster','76561198089843073','2026-03-14 17:00:33',12530,0,0,'[]',NULL,NULL,NULL,NULL),
(907,'ParaziD','76561198042195351','2026-03-14 13:54:41',730,0,0,'[]',NULL,NULL,NULL,NULL),
(908,'Jokibo','76561199539160057','2026-03-15 10:37:32',131,0,0,'[]',NULL,NULL,NULL,NULL),
(909,'MRQ16_','76561199820467098','2026-03-14 14:25:21',40,0,0,'[]',NULL,NULL,NULL,NULL),
(910,'_Akihko_','76561199008487788','2026-03-14 15:22:44',1246,0,0,'[]',NULL,NULL,NULL,NULL),
(911,'Священник','76561198238405646','2026-03-14 15:04:09',18,0,0,'[]',NULL,NULL,NULL,NULL),
(912,'Святогор Шемякин','76561199422214536','2026-03-14 15:11:56',111,0,0,'[]',NULL,NULL,NULL,NULL),
(913,'Матрос Алексей Ненавальный','76561198749512301','2026-03-16 11:03:40',13484,0,0,'[]',NULL,NULL,NULL,NULL),
(914,'Матрос Лало Гонзалес','76561199229673414','2026-03-15 09:27:57',1943,0,0,'[]',NULL,NULL,NULL,NULL),
(915,'Я не Я','76561199542862726','2026-03-14 16:16:34',91,0,0,'[]',NULL,NULL,NULL,NULL),
(916,'Матрос Иван Морозов','76561198838473903','2026-03-14 16:43:07',64,0,0,'[]',NULL,NULL,NULL,NULL),
(917,'Chase_11','76561199511344815','2026-03-15 06:00:19',403,0,0,'[]',NULL,NULL,NULL,NULL),
(918,'MRCAT','76561199058428249','2026-03-14 17:51:13',506,0,0,'[]',NULL,NULL,NULL,NULL),
(919,'Матрос Рифт','76561199217818082','2026-03-14 18:42:48',2899,0,0,'[]',NULL,NULL,NULL,NULL),
(920,'RobastianBarehand','76561198301465746','2026-03-14 18:10:46',102,0,0,'[]',NULL,NULL,NULL,NULL),
(921,'yhka023','76561198730218422','2026-03-17 16:41:43',4754,0,0,'[]',NULL,NULL,NULL,NULL),
(922,'Jørgen Leif','76561198145243514','2026-03-14 18:25:56',537,0,0,'[]',NULL,NULL,NULL,NULL),
(923,'Матрос Кригор Семенов','76561198771658792','2026-03-21 15:11:23',8688,0,0,'[]',NULL,NULL,NULL,NULL),
(924,'VIPMartin','76561199568675761','2026-03-14 19:12:32',688,0,0,'[]',NULL,NULL,NULL,NULL),
(925,'PiGGY BANG','76561198171139557','2026-03-14 19:07:55',441,0,0,'[]',NULL,NULL,NULL,NULL),
(926,'Jlic','76561199060514064','2026-03-17 16:55:34',188,0,0,'[]',NULL,NULL,NULL,NULL),
(927,'kot.kv','76561198034616328','2026-03-16 19:40:53',1798,0,0,'[]',NULL,NULL,NULL,NULL),
(928,'Dylan O\'Brien','76561198437894349','2026-03-14 19:26:11',361,0,0,'[]',NULL,NULL,NULL,NULL),
(929,'костыч','76561199058438430','2026-03-14 20:47:54',5071,0,0,'[]',NULL,NULL,NULL,NULL),
(930,'Ewiban','76561199094748588','2026-03-14 20:48:14',5050,0,0,'[]',NULL,NULL,NULL,NULL),
(931,'Wesy','76561199854801126','2026-03-14 19:44:36',17,0,0,'[]',NULL,NULL,NULL,NULL),
(932,'Матрос Лис','76561198844620025','2026-03-15 11:10:01',11527,0,0,'[]',NULL,NULL,NULL,NULL),
(933,'kelsior','76561198063381820','2026-03-14 20:40:48',147,0,0,'[]',NULL,NULL,NULL,NULL),
(934,'Anarchist','76561199132885378','2026-03-14 20:45:15',312,0,0,'[]',NULL,NULL,NULL,NULL),
(935,'Матрос Виталий Новиков','76561199773938293','2026-03-14 20:44:19',80,0,0,'[]',NULL,NULL,NULL,NULL),
(936,'hxpro95','76561199127888473','2026-03-14 20:49:17',43,0,0,'[]',NULL,NULL,NULL,NULL),
(937,'Гарфилд','76561199144604579','2026-03-14 20:58:57',77,0,0,'[]',NULL,NULL,NULL,NULL),
(938,'Paksu-Aku','76561198141664820','2026-03-14 21:10:40',25,0,0,'[]',NULL,NULL,NULL,NULL),
(939,'ХЕРОБРИН  PLAY YOUTUBE','76561198995232205','2026-03-14 21:44:25',1470,0,0,'[]',NULL,NULL,NULL,NULL),
(940,'Anderal2','76561198942228617','2026-03-14 21:34:48',84,0,0,'[]',NULL,NULL,NULL,NULL),
(941,'Дранііііііііііііііііііііііііііік','76561199354304920','2026-03-14 22:37:49',95,0,0,'[]',NULL,NULL,NULL,NULL),
(942,'Leksus','76561198365930784','2026-03-14 22:33:26',207,0,0,'[]',NULL,NULL,NULL,NULL),
(943,'MorocK','76561199186331788','2026-03-14 22:33:30',180,0,0,'[]',NULL,NULL,NULL,NULL),
(944,'Rostik_2005','76561199086567891','2026-03-15 05:21:53',3307,0,0,'[]',NULL,NULL,NULL,NULL),
(945,'diddy','76561198986389428','2026-03-15 05:23:13',49,0,0,'[]',NULL,NULL,NULL,NULL),
(946,'Dr. Jefferson','76561199257235427','2026-03-15 05:23:47',179,0,0,'[]',NULL,NULL,NULL,NULL),
(947,'sin','76561199788889414','2026-03-15 06:15:25',2091,0,0,'[]',NULL,NULL,NULL,NULL),
(948,'Hzshke','76561199244450056','2026-03-15 08:12:20',10,0,0,'[]',NULL,NULL,NULL,NULL),
(949,'Альберт Вадымич','76561199210377212','2026-03-15 09:14:20',203,0,0,'[]',NULL,NULL,NULL,NULL),
(950,'dogwater','76561198387781837','2026-03-15 09:46:47',2099,0,0,'[]',NULL,NULL,NULL,NULL),
(951,'Негров Вротебалович','76561198313083500','2026-03-15 09:18:03',38,0,0,'[]',NULL,NULL,NULL,NULL),
(952,'котер900','76561198990341489','2026-03-15 11:27:13',5639,0,0,'[]',NULL,NULL,NULL,NULL),
(953,'ТУРБО ИШАК','76561198851964638','2026-03-15 09:40:31',4,0,0,'[]',NULL,NULL,NULL,NULL),
(954,'Klins_Art','76561199793460860','2026-03-15 10:05:34',1081,0,0,'[]',NULL,NULL,NULL,NULL),
(955,'Лаврентий Петров','76561199527392500','2026-03-15 11:37:55',5365,0,0,'[]',NULL,NULL,NULL,NULL),
(956,'Виталий Комаров','76561199365683457','2026-03-16 15:08:43',3974,0,0,'[]',NULL,NULL,NULL,NULL),
(957,'Krasava','76561198840508860','2026-03-21 12:16:40',367,0,0,'[]',NULL,NULL,NULL,NULL),
(958,'Kirill','76561198999756313','2026-03-15 10:57:14',149,0,0,'[]',NULL,NULL,NULL,NULL),
(959,'Кирпич','76561199508193771','2026-03-15 11:37:15',2386,0,0,'[]',NULL,NULL,NULL,NULL),
(960,'Александр Дружнев','76561199517871707','2026-03-15 11:51:49',2182,0,0,'[]',NULL,NULL,NULL,NULL),
(961,'(ЮпФем) Блядское','76561199508560974','2026-03-15 11:50:39',12,0,0,'[]',NULL,NULL,NULL,NULL),
(962,'avattson','76561199217707610','2026-03-15 12:23:24',111,0,0,'[]',NULL,NULL,NULL,NULL),
(963,'ND','76561199556730981','2026-03-15 21:25:43',4,0,0,'[]',NULL,NULL,NULL,NULL),
(964,'БАЛТИКА 9','76561198357729013','2026-03-16 09:01:34',1752,0,0,'[]',NULL,NULL,NULL,NULL),
(965,'Denis','76561199015724536','2026-03-16 09:05:33',722,0,0,'[]',NULL,NULL,NULL,NULL),
(966,'zorwthy','76561199789637727','2026-03-16 10:50:38',835,0,0,'[]',NULL,NULL,NULL,NULL),
(967,'VoidyBoi','76561199055986794','2026-03-16 10:59:13',85,0,0,'[]',NULL,NULL,NULL,NULL),
(968,'Mao Zedong','76561199847811976','2026-03-16 11:03:03',44,0,0,'[]',NULL,NULL,NULL,NULL),
(969,'Starmi','76561199844728411','2026-03-16 18:52:21',180,0,0,'[]',NULL,NULL,NULL,NULL),
(970,'egsr8','76561199426068458','2026-03-16 18:15:15',3568,0,0,'[]',NULL,NULL,NULL,NULL),
(971,'Harakyn','76561199432963858','2026-03-16 13:09:49',152,0,0,'[]',NULL,NULL,NULL,NULL),
(972,'dysthymia','76561199815105927','2026-03-16 14:29:49',506,0,0,'[]',NULL,NULL,NULL,NULL),
(973,'Джон Марстон','76561198201048487','2026-03-16 19:12:50',14110,0,0,'[]',NULL,NULL,NULL,NULL),
(974,'не воробей','76561199389926264','2026-03-16 15:57:52',47,0,0,'[]',NULL,NULL,NULL,NULL),
(975,'Матрос Сержинсео Ляк','76561199039638747','2026-03-16 20:14:05',1950,0,0,'[]',NULL,NULL,NULL,NULL),
(976,'CREATURE','76561199069240801','2026-03-16 18:24:20',743,0,0,'[]',NULL,NULL,NULL,NULL),
(977,'VanderBost','76561199130499091','2026-03-16 18:25:48',15,0,0,'[]',NULL,NULL,NULL,NULL),
(978,'MikeOxlong','76561198207512503','2026-03-16 18:30:55',217,0,0,'[]',NULL,NULL,NULL,NULL),
(979,'ВыбросDiзеля','76561198063980778','2026-03-16 19:04:54',671,0,0,'[]',NULL,NULL,NULL,NULL),
(980,'Salmon','76561199383040863','2026-03-16 19:04:26',426,0,0,'[]',NULL,NULL,NULL,NULL),
(981,'Ayaka','76561198177075327','2026-03-16 20:14:18',4523,0,0,'[]',NULL,NULL,NULL,NULL),
(982,'Дайсон Барашков','76561199076785383','2026-03-16 20:14:53',622,0,0,'[]',NULL,NULL,NULL,NULL),
(983,'johnepstein','76561199766514743','2026-03-16 20:14:59',623,0,0,'[]',NULL,NULL,NULL,NULL),
(984,'troanovgerman27','76561199649612851','2026-03-17 04:07:45',0,0,0,'[]',NULL,NULL,NULL,NULL),
(985,'Сержант Зумерков','76561199216724709','2026-03-17 10:45:28',2,0,0,'[]',NULL,NULL,NULL,NULL),
(986,'Питух','76561199429958725','2026-03-17 12:41:22',5989,0,0,'[]',NULL,NULL,NULL,NULL),
(987,'Mindfall7','76561199475990796','2026-03-17 11:21:01',307,0,0,'[]',NULL,NULL,NULL,NULL),
(988,'Уилл Тёрнер','76561198097903017','2026-03-17 12:33:26',5105,0,0,'[]',NULL,NULL,NULL,NULL),
(989,'M.Sibas','76561199133216556','2026-03-17 11:24:11',7,0,0,'[]',NULL,NULL,NULL,NULL),
(990,'maximfox17092011','76561198750415405','2026-03-19 13:31:29',366,0,0,'[]',NULL,NULL,NULL,NULL),
(991,'st-mah','76561199435962746','2026-03-20 15:25:04',1570,0,0,'[]',NULL,NULL,NULL,NULL),
(992,'mickaron','76561199641959128','2026-03-17 12:56:23',758,0,0,'[]',NULL,NULL,NULL,NULL),
(993,'Jebus','76561199228049292','2026-03-17 16:04:52',11238,0,0,'[]',NULL,NULL,NULL,NULL),
(994,'Дрочеслав','76561198032461954','2026-03-17 13:15:15',393,0,0,'[]',NULL,NULL,NULL,NULL),
(995,'Мега Уебан','76561198954982209','2026-03-17 14:11:10',1632,0,0,'[]',NULL,NULL,NULL,NULL),
(996,'nekto.2002','76561199799281508','2026-03-17 13:44:50',0,0,0,'[]',NULL,NULL,NULL,NULL),
(997,'AstraL','76561198983579500','2026-03-17 16:44:22',9794,0,0,'[]',NULL,NULL,NULL,NULL),
(998,'american_idiot_8','76561199811384483','2026-03-18 11:50:48',10112,0,0,'[]',NULL,NULL,NULL,NULL),
(999,'FrYkTik','76561198835601542','2026-03-17 15:30:35',3162,0,0,'[]',NULL,NULL,NULL,NULL),
(1000,'Gogi','76561199055385590','2026-03-17 14:47:11',297,0,0,'[]',NULL,NULL,NULL,NULL),
(1001,'Ased_qcold','76561199124409059','2026-03-17 14:59:39',112,0,0,'[]',NULL,NULL,NULL,NULL),
(1002,'lain','76561199522304512','2026-03-17 15:23:01',728,0,0,'[]',NULL,NULL,NULL,NULL),
(1003,'Khan','76561199388819703','2026-03-20 15:26:12',45,0,0,'[]',NULL,NULL,NULL,NULL),
(1004,'Джон Марстон','76561198815219625','2026-03-17 17:22:44',445,0,0,'[]',NULL,NULL,NULL,NULL),
(1005,'odonirokun','76561198868467528','2026-03-17 16:44:47',396,0,0,'[]',NULL,NULL,NULL,NULL),
(1006,'Bananello0','76561199196539967','2026-03-17 16:49:30',299,0,0,'[]',NULL,NULL,NULL,NULL),
(1007,'Богдан Барбосов','76561199509247553','2026-03-17 20:28:45',4498,0,0,'[]',NULL,NULL,NULL,NULL),
(1008,'Айзек Април','76561198994675728','2026-03-17 19:21:18',71,0,0,'[]',NULL,NULL,NULL,NULL),
(1009,'Комрад','76561198131648735','2026-03-17 20:13:00',134,0,0,'[]',NULL,NULL,NULL,NULL),
(1010,'Pivogde85','76561199664742182','2026-03-17 20:26:05',47,0,0,'[]',NULL,NULL,NULL,NULL),
(1011,'Тимур  Славков','76561199120433310','2026-03-18 06:52:06',83,0,0,'[]',NULL,NULL,NULL,NULL),
(1012,'vimile73','76561199403720771','2026-03-18 08:51:17',38,0,0,'[]',NULL,NULL,NULL,NULL),
(1013,'кен','76561199446453356','2026-03-19 14:53:39',4739,0,0,'[]',NULL,NULL,NULL,NULL),
(1014,'sukalpach_skorokovychkakak','76561199489241931','2026-03-18 10:46:21',837,0,0,'[]',NULL,NULL,NULL,NULL),
(1015,'z1kerz','76561199773627815','2026-03-18 10:49:33',40,0,0,'[]',NULL,NULL,NULL,NULL),
(1016,'kek_se_lot','76561199065036287','2026-03-18 11:04:26',400,0,0,'[]',NULL,NULL,NULL,NULL),
(1017,'Stereo Geode','76561199256450794','2026-03-18 11:44:17',1446,0,0,'[]',NULL,NULL,NULL,NULL),
(1018,'michaelppv1010','76561199553670155','2026-03-18 11:23:54',32,0,0,'[]',NULL,NULL,NULL,NULL),
(1019,'убитый спайсом','76561199184517512','2026-03-18 11:43:14',728,0,0,'[]',NULL,NULL,NULL,NULL),
(1020,'Виталий колготки','76561199567186359','2026-03-18 12:02:53',160,0,0,'[]',NULL,NULL,NULL,NULL),
(1021,'MrNesaman','76561199420614359','2026-03-18 12:10:59',458,0,0,'[]',NULL,NULL,NULL,NULL),
(1022,'atamanns22','76561198857870752','2026-03-18 12:39:42',16,0,0,'[]',NULL,NULL,NULL,NULL),
(1023,'Kei Latern','76561199142595244','2026-03-19 09:42:56',408,0,0,'[]',NULL,NULL,NULL,NULL),
(1024,'Zero','0','2026-03-20 20:14:58',6736,0,0,'[]',NULL,NULL,NULL,NULL),
(1025,'FeIix','76561199816124647','2026-03-19 07:09:14',0,0,0,'[]',NULL,NULL,NULL,NULL),
(1026,'alexkemeron','76561199397097390','2026-03-20 05:46:14',230,0,0,'[]',NULL,NULL,NULL,NULL),
(1027,'kebabus','76561198153081451','2026-03-19 11:50:18',119,0,0,'[]',NULL,NULL,NULL,NULL),
(1028,'m0rtem','76561198162233771','2026-03-19 12:19:25',34,0,0,'[]',NULL,NULL,NULL,NULL),
(1029,'Боба','76561198267827669','2026-03-19 13:58:04',4581,0,0,'[]',NULL,NULL,NULL,NULL),
(1030,'Minorka','76561198882209809','2026-03-20 16:16:42',3596,0,0,'[]',NULL,NULL,NULL,NULL),
(1031,'Francium','76561198089918924','2026-03-20 17:10:27',956,0,0,'[]',NULL,NULL,NULL,NULL),
(1032,'ГришкаКрутышка','76561198256513048','2026-03-19 14:26:35',23,0,0,'[]',NULL,NULL,NULL,NULL),
(1033,'Петр Кукин','76561198253692859','2026-03-19 16:22:23',472,0,0,'[]',NULL,NULL,NULL,NULL),
(1034,'JIBRIL','76561199536802610','2026-03-19 16:37:56',109,0,0,'[]',NULL,NULL,NULL,NULL),
(1035,'Виктор Дно','76561199258653500','2026-03-19 17:05:43',1469,0,0,'[]',NULL,NULL,NULL,NULL),
(1036,'Barыga','76561199142461856','2026-03-19 16:57:21',215,0,0,'[]',NULL,NULL,NULL,NULL),
(1037,'амогус сус','76561199876998260','2026-03-19 17:00:24',314,0,0,'[]',NULL,NULL,NULL,NULL),
(1038,'Матрос 1-ст Феофан Крестов','76561199256373351','2026-03-21 19:22:14',23376,0,0,'[]',NULL,NULL,NULL,NULL),
(1039,'zombkrytoi_228','76561198769751556','2026-03-20 15:36:57',315,0,0,'[]',NULL,NULL,NULL,NULL),
(1040,'Fleks','76561198273905246','2026-03-20 16:07:36',0,0,0,'[]',NULL,NULL,NULL,NULL),
(1041,'Палыч','76561198080539522','2026-03-20 16:29:13',120,0,0,'[]',NULL,NULL,NULL,NULL),
(1042,'Yankai','76561199833977464','2026-03-20 19:08:03',8775,0,0,'[]',NULL,NULL,NULL,NULL),
(1043,'гроза хачей','76561199010515569','2026-03-20 17:16:09',80,0,0,'[]',NULL,NULL,NULL,NULL),
(1044,'CRYMALLIN','76561199031656023','2026-03-20 17:28:43',247,0,0,'[]',NULL,NULL,NULL,NULL),
(1045,'Mouhito','76561199187115036','2026-03-20 19:57:29',8796,0,0,'[]',NULL,NULL,NULL,NULL),
(1046,'adamchiq','76561199201226005','2026-03-20 19:07:52',2174,0,0,'[]',NULL,NULL,NULL,NULL),
(1047,'ТОБИ ПИЗДА','76561199486147061','2026-03-20 19:45:09',691,0,0,'[]',NULL,NULL,NULL,NULL),
(1048,'Anal Troubadour','76561198896384085','2026-03-21 08:51:13',111,0,0,'[]',NULL,NULL,NULL,NULL),
(1049,'DEMAKX','76561198868449603','2026-03-21 15:13:10',19943,0,0,'[]',NULL,NULL,NULL,NULL),
(1050,'TROY','76561199303576372','2026-03-21 09:21:49',50,0,0,'[]',NULL,NULL,NULL,NULL),
(1051,'Дроченко Юлия','76561199680995240','2026-03-21 09:29:17',111,0,0,'[]',NULL,NULL,NULL,NULL),
(1052,'Maksik_Sansik','76561199783470666','2026-03-21 10:02:57',149,0,0,'[]',NULL,NULL,NULL,NULL),
(1053,'Funny Valentine','76561199519841888','2026-03-21 10:14:54',347,0,0,'[]',NULL,NULL,NULL,NULL),
(1054,'mamax2ded','76561199516029958','2026-03-21 10:18:14',161,0,0,'[]',NULL,NULL,NULL,NULL),
(1055,'AkiN','76561199025791210','2026-03-21 10:41:14',1036,0,0,'[]',NULL,NULL,NULL,NULL),
(1056,'Аllah','76561199512139398','2026-03-21 10:33:13',71,0,0,'[]',NULL,NULL,NULL,NULL),
(1057,'Maks1mka','76561199689899807','2026-03-21 11:03:18',1777,0,0,'[]',NULL,NULL,NULL,NULL),
(1058,'Амеба','76561199116275103','2026-03-21 10:43:57',252,0,0,'[]',NULL,NULL,NULL,NULL),
(1059,'паштет','76561198757618858','2026-03-21 10:47:05',43,0,0,'[]',NULL,NULL,NULL,NULL),
(1060,'Ст-а 1-ст Сергей Иванович','76561198343673730','2026-03-21 11:04:21',294,0,0,'[]',NULL,NULL,NULL,NULL),
(1061,'VENOM','76561198752452281','2026-03-21 11:40:57',235,0,0,'[]',NULL,NULL,NULL,NULL),
(1062,'Kilizant','76561198198943378','2026-03-21 11:46:36',200,0,0,'[]',NULL,NULL,NULL,NULL),
(1063,'Физик Ядерщик','76561199132621637','2026-03-21 12:06:00',885,0,0,'[]',NULL,NULL,NULL,NULL),
(1064,'JONL','76561199495941119','2026-03-21 15:10:00',7354,0,0,'[]',NULL,NULL,NULL,NULL),
(1065,'HGTERTY','76561198761446581','2026-03-21 12:37:47',1625,0,0,'[]',NULL,NULL,NULL,NULL),
(1066,'Егор Пива','76561199709579301','2026-03-21 12:26:45',29,0,0,'[]',NULL,NULL,NULL,NULL),
(1067,'Матрос Матадаир Калисултанов','76561199110676697','2026-03-21 14:24:43',1226,0,0,'[]',NULL,NULL,NULL,NULL),
(1068,'goosar','76561199561216000','2026-03-21 15:00:43',768,0,0,'[]',NULL,NULL,NULL,NULL),
(1069,'andrejnesterenko510','76561198967702344','2026-03-21 15:02:52',228,0,0,'[]',NULL,NULL,NULL,NULL),
(1070,'Демон Ночи','76561198107188215','2026-03-21 15:21:34',970,0,0,'[]',NULL,NULL,NULL,NULL),
(1071,'Новичок','76561198128204022','2026-03-21 16:55:38',61,0,0,'[]',NULL,NULL,NULL,NULL),
(1072,'Мощные Штаны','76561198378310674','2026-03-21 16:14:06',7,0,0,'[]',NULL,NULL,NULL,NULL),
(1073,'Сосисок предостаточно','76561198126023539','2026-03-21 17:37:55',66,0,0,'[]',NULL,NULL,NULL,NULL),
(1074,'nathanielhaugrud','76561199490742307','2026-03-22 08:45:32',1069,0,0,'[]',NULL,NULL,NULL,NULL),
(1075,'Мистер Пончик))','76561198811676156','2026-03-22 09:08:29',32,0,0,'[]',NULL,NULL,NULL,NULL),
(1076,'Dr.Blue','76561199165501573','2026-03-22 09:31:59',24,0,0,'[]',NULL,NULL,NULL,NULL),
(1077,'Guboshlop','76561198036491108','2026-03-22 10:03:44',48,0,0,'[]',NULL,NULL,NULL,NULL),
(1078,'Матрос Итан Грейс','76561199227842015','2026-03-22 10:43:56',646,0,0,'[]',NULL,NULL,NULL,NULL),
(1079,'ванявантуз','76561199049366343','2026-03-22 10:22:10',269,0,0,'[]',NULL,NULL,NULL,NULL),
(1080,'coffee','76561199849012504','2026-03-22 10:54:36',1679,0,0,'[]',NULL,NULL,NULL,NULL),
(1081,'Loter','76561198891619955','2026-03-22 12:56:44',128,0,0,'[]',NULL,NULL,NULL,NULL),
(1082,'Carl Sberg','76561198015760531','2026-03-22 13:16:39',10,0,0,'[]',NULL,NULL,NULL,NULL),
(1083,'Мочеglouth','76561199184398966','2026-03-22 17:24:49',927,0,0,'[]',NULL,NULL,NULL,NULL),
(1084,'Усатая бубна','76561199187671882','2026-03-22 14:04:30',233,0,0,'[]',NULL,NULL,NULL,NULL),
(1085,'Sanekmater123','76561199659602132','2026-03-22 15:39:16',1987,0,0,'[]',NULL,NULL,NULL,NULL),
(1086,'X_Anchovy_X','76561199014695527','2026-03-22 17:01:32',4767,0,0,'[]',NULL,NULL,NULL,NULL),
(1087,'Матрос Равшан Крокусов','76561198061082469','2026-03-22 16:23:54',2293,0,0,'[]',NULL,NULL,NULL,NULL),
(1088,'kanone','76561198282953839','2026-03-22 16:03:24',520,0,0,'[]',NULL,NULL,NULL,NULL),
(1089,'CNNIS MSTER','76561199405763374','2026-03-22 15:58:11',9,0,0,'[]',NULL,NULL,NULL,NULL),
(1090,'Жировой Паладинъ','76561199572089049','2026-03-22 16:04:10',58,0,0,'[]',NULL,NULL,NULL,NULL),
(1091,'битбокс молитвой','76561199436035169','2026-03-22 16:27:50',290,0,0,'[]',NULL,NULL,NULL,NULL),
(1092,'DETHDAN','76561199810518094','2026-03-22 17:04:36',2357,0,0,'[]',NULL,NULL,NULL,NULL),
(1093,'natti','76561198154219494','2026-03-22 16:50:19',714,0,0,'[]',NULL,NULL,NULL,NULL),
(1094,'адмирал Обосранные Трусы','76561199446400570','2026-03-22 16:50:59',100,0,0,'[]',NULL,NULL,NULL,NULL),
(1095,'Gid','76561198000570512','2026-03-22 19:11:00',4034,0,0,'[]',NULL,NULL,NULL,NULL),
(1096,'bars','76561199231930625','2026-03-22 19:16:11',2041,1,1,'[]',NULL,NULL,NULL,NULL),
(1097,'The last GOD','76561199219141408','2026-03-22 19:07:51',101,0,0,'[]',NULL,NULL,NULL,NULL);
/*!40000 ALTER TABLE `players_data` ENABLE KEYS */;
UNLOCK TABLES;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_unicode_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER trg_players_first_login
BEFORE INSERT ON players_data
FOR EACH ROW
BEGIN
    IF NEW.first_login IS NULL THEN
        SET NEW.first_login = NOW();
    END IF;
END */;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_unicode_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER trg_players_ip_insert
AFTER INSERT ON players_data
FOR EACH ROW
BEGIN
    IF NEW.last_ip IS NOT NULL THEN

        INSERT INTO player_ips (player_id, ip, subnet, first_seen, last_seen)
        VALUES (NEW.player_id, NEW.last_ip, NEW.last_subnet, NOW(), NOW())
        ON DUPLICATE KEY UPDATE
            last_seen = NOW();

    END IF;
END */;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_unicode_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER trg_players_name_insert
AFTER INSERT ON players_data
FOR EACH ROW
BEGIN
    IF NEW.player_last_time_name IS NOT NULL THEN

        INSERT INTO player_names (player_id, nickname, first_seen, last_seen)
        VALUES (NEW.player_id, NEW.player_last_time_name, NOW(), NOW())
        ON DUPLICATE KEY UPDATE
            last_seen = NOW();

    END IF;
END */;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_unicode_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER trg_players_ip_update
AFTER UPDATE ON players_data
FOR EACH ROW
BEGIN
    IF NEW.last_ip IS NOT NULL AND (OLD.last_ip IS NULL OR NEW.last_ip <> OLD.last_ip) THEN

        INSERT INTO player_ips (player_id, ip, subnet, first_seen, last_seen)
        VALUES (NEW.player_id, NEW.last_ip, NEW.last_subnet, NOW(), NOW())
        ON DUPLICATE KEY UPDATE
            last_seen = NOW();

    END IF;
END */;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_unicode_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER trg_players_name_update
AFTER UPDATE ON players_data
FOR EACH ROW
BEGIN
    IF NEW.player_last_time_name IS NOT NULL AND (OLD.player_last_time_name IS NULL OR NEW.player_last_time_name <> OLD.player_last_time_name) THEN

        INSERT INTO player_names (player_id, nickname, first_seen, last_seen)
        VALUES (NEW.player_id, NEW.player_last_time_name, NOW(), NOW())
        ON DUPLICATE KEY UPDATE
            last_seen = NOW();

    END IF;
END */;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;

--
-- Table structure for table `players_stats`
--

DROP TABLE IF EXISTS `players_stats`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `players_stats` (
  `player_id` int(11) NOT NULL,
  `successful_missions_captain` int(11) DEFAULT NULL,
  `total_missions_captain` int(11) DEFAULT NULL,
  `saved_players` int(11) DEFAULT NULL,
  `killed_monsters` int(11) DEFAULT NULL,
  `killed_players` int(11) DEFAULT NULL,
  `killed_npc` int(11) DEFAULT NULL,
  PRIMARY KEY (`player_id`),
  CONSTRAINT `players_stats_ibfk_1` FOREIGN KEY (`player_id`) REFERENCES `players_data` (`player_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `players_stats`
--

LOCK TABLES `players_stats` WRITE;
/*!40000 ALTER TABLE `players_stats` DISABLE KEYS */;
/*!40000 ALTER TABLE `players_stats` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `projects`
--

DROP TABLE IF EXISTS `projects`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `projects` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `discord_message_id` bigint(20) unsigned DEFAULT NULL,
  `forum_channel_id` bigint(20) unsigned NOT NULL,
  `project_name` varchar(255) NOT NULL,
  `project_type` varchar(100) DEFAULT NULL,
  `author_discord_name` varchar(255) DEFAULT NULL,
  `author_discord_id` bigint(20) unsigned NOT NULL,
  `status` tinyint(4) NOT NULL DEFAULT 0,
  `requires_upload` tinyint(4) NOT NULL DEFAULT 0,
  `hull_work` tinyint(4) NOT NULL DEFAULT 0,
  `equipment_layout` tinyint(4) NOT NULL DEFAULT 0,
  `compartment_marking` tinyint(4) NOT NULL DEFAULT 0,
  `decor_work` tinyint(4) NOT NULL DEFAULT 0,
  `wiring_work` tinyint(4) NOT NULL DEFAULT 0,
  `notes` text DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_projects_discord_message` (`discord_message_id`),
  KEY `idx_projects_author` (`author_discord_id`),
  KEY `idx_projects_forum` (`forum_channel_id`)
) ENGINE=InnoDB AUTO_INCREMENT=17 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `projects`
--

LOCK TABLES `projects` WRITE;
/*!40000 ALTER TABLE `projects` DISABLE KEYS */;
INSERT INTO `projects` VALUES
(1,1477746895173714074,1477746895173714074,'Проверка 25','Корабль','icebergdem',406096773052891136,5,0,0,1,0,0,1,'Данный проект создан для проверки','2026-03-01 19:18:34','2026-03-21 21:25:55'),
(2,1477920492982767627,1477920492982767627,'Фантом','Корабль','kyper3',700615788305842196,3,0,1,1,1,1,1,NULL,'2026-03-02 06:48:22','2026-03-11 10:35:19'),
(3,1478328085991653499,1478328085991653499,'Акула','Корабль','zefir_rus',473032522763337739,3,0,1,1,1,1,1,NULL,'2026-03-03 09:48:00','2026-03-29 09:30:44'),
(4,1478331105068187700,1478331105068187700,'Тайпан','Корабль','zefir_rus',473032522763337739,3,0,1,1,1,1,1,NULL,'2026-03-03 10:00:00','2026-03-12 11:08:48'),
(5,1478387636346425376,1478387636346425376,'Снегирь','Челнок','zefir_rus',473032522763337739,3,0,1,1,1,1,1,NULL,'2026-03-03 13:44:38','2026-03-04 03:37:44'),
(6,1478711949884919890,1478711949884919890,'ГМ Линия Интереса','ГМ карта','ab0bum',822511267352281118,5,0,0,0,0,0,0,'пвп на членоках','2026-03-04 11:13:21','2026-03-07 11:13:07'),
(7,1479010419577196565,1479010419577196565,'Протей 2','Корабль','kyper3',700615788305842196,3,0,1,1,1,1,1,NULL,'2026-03-05 06:59:21','2026-04-01 07:53:05'),
(8,1479435252723220652,1479435252723220652,'Куролиск','Корабль','zefir_rus',473032522763337739,3,0,1,1,1,1,1,NULL,'2026-03-06 11:07:29','2026-04-01 07:46:38'),
(9,1479456380866920579,1479456380866920579,'Гидра-М','Подлодка','chumnoydak',873536867654570004,3,0,1,1,1,1,1,'Лёгкий корабль разведки','2026-03-06 12:31:26','2026-03-10 12:54:13'),
(10,1479772158979735593,1479772158979735593,'[PVP] Линия фронта','ГМ карта','ab0bum',822511267352281118,3,0,0,0,0,0,0,'Стандартное пвп на челноках 3 на 3','2026-03-07 09:26:14','2026-03-10 07:14:49'),
(11,1480873803625140387,1480873803625140387,'Дезраптор','Малый корабль','ab0bum',822511267352281118,0,0,1,1,1,0,0,NULL,'2026-03-10 10:23:46','2026-03-10 10:24:21'),
(12,1480923587866136656,1480923587866136656,'Агама-М','Корабль','chumnoydak',873536867654570004,3,0,1,1,1,1,1,NULL,'2026-03-10 13:41:36','2026-03-11 10:41:03'),
(13,1481184756774342698,1481184756774342698,'Бермут-К','Корабль','zefir_rus',473032522763337739,3,0,1,1,1,1,1,'Корабль для компании, не для заплывов','2026-03-11 06:59:23','2026-03-11 08:48:11'),
(14,1481295844883042446,1481295844883042446,'Бермут-К2','-','ab0bum',822511267352281118,3,0,0,0,0,0,0,'-','2026-03-11 14:20:48','2026-03-12 02:31:10'),
(15,1486709572004479012,1486709572004479012,'Легион','Корабль','kyper3',700615788305842196,3,0,1,1,1,1,1,NULL,'2026-03-26 12:52:52','2026-03-29 09:16:23'),
(16,1488811500578799736,1488811500578799736,'Клоп','Катер','2colorname',762975758015528990,4,0,1,1,1,1,1,'Малый катер бывшего следователя, умершего по неизвестным обстоятельствам. Ныне надежная боевая единица флота A.S.P.E.R.','2026-04-01 08:05:20','2026-04-02 13:04:14');
/*!40000 ALTER TABLE `projects` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `server_player_privileges`
--

DROP TABLE IF EXISTS `server_player_privileges`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `server_player_privileges` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `steamid` varchar(50) NOT NULL,
  `player_id` int(11) DEFAULT NULL,
  `privileges` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT '[]' CHECK (json_valid(`privileges`)),
  `reason` varchar(500) DEFAULT NULL,
  `granted_by_admin_id` int(11) DEFAULT NULL,
  `granted_at` datetime DEFAULT current_timestamp(),
  `expires_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `steamid` (`steamid`),
  KEY `idx_steamid` (`steamid`),
  KEY `idx_player_id` (`player_id`),
  KEY `idx_granted_at` (`granted_at`),
  KEY `fk_granted_by_admin_id` (`granted_by_admin_id`),
  CONSTRAINT `fk_granted_by_admin_id` FOREIGN KEY (`granted_by_admin_id`) REFERENCES `admin_users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_player_id` FOREIGN KEY (`player_id`) REFERENCES `players_data` (`player_id`) ON DELETE SET NULL
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `server_player_privileges`
--

LOCK TABLES `server_player_privileges` WRITE;
/*!40000 ALTER TABLE `server_player_privileges` DISABLE KEYS */;
/*!40000 ALTER TABLE `server_player_privileges` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-04-24 21:59:11
