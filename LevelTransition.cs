using Barotrauma;
using Barotrauma.Items.Components;
using Barotrauma.LuaCs;
using Barotrauma.Networking;
using HarmonyLib;
using Microsoft.Xna.Framework;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;

namespace LevelTransition
{
    /// <summary>
    /// Плагин для перехода на следующий уровень в режиме миссий (Mission Mode).
    /// Команда !next в чате или nextlevel в консоли — завершает текущий раунд и стартует новый.
    /// Корабль тот же, состояние не сохраняется.
    /// </summary>
    public class LevelTransition : IAssemblyPlugin
    {
        public static LevelTransition Instance;
        private Harmony harmony;

        // Флаг: идёт ли сейчас переход
        private static volatile bool isTransitioning = false;
        // Параметры следующего уровня
        private static float nextDifficulty = 30f;

        private static void LogInfo(string msg) => GameServer.Log("[LevelTransition] " + msg, ServerLog.MessageType.ServerMessage);
        private static void LogError(string msg) => GameServer.Log("[LevelTransition] " + msg, ServerLog.MessageType.Error);

        public void Initialize()
        {
            Instance = this;
            harmony = new Harmony("LevelTransition.server.mod");

            // Ручной патчинг: ищем метод SendChatMessage, который принимает ChatMessage
            try
            {
                var targetMethod = FindSendChatMessageMethod();
                if (targetMethod != null)
                {
                    var prefix = typeof(LevelTransition).GetMethod(nameof(ChatMessagePrefix),
                        BindingFlags.Static | BindingFlags.NonPublic);
                    harmony.Patch(targetMethod, prefix: new HarmonyMethod(prefix));
                    LogInfo($"Патч на чат установлен: {targetMethod.DeclaringType?.Name}.{targetMethod.Name}");
                }
                else
                {
                    LogError("Не найден метод SendChatMessage(ChatMessage). Команда !next будет доступна только через консоль.");
                }
            }
            catch (Exception ex)
            {
                LogError($"Ошибка патчинга чата: {ex.Message}");
            }

            // Регистрируем серверную консольную команду
            try
            {
                var cmd = new DebugConsole.Command(
                    "nextlevel",
                    "nextlevel [difficulty] — перейти на следующий случайный уровень (тот же корабль, без сохранения состояния)",
                    args =>
                    {
                        float diff = 30f;
                        if (args != null && args.Length > 0 && float.TryParse(args[0], out float parsed))
                            diff = Math.Clamp(parsed, 0f, 100f);
                        TriggerTransition(diff);
                    });
                DebugConsole.Commands.Add(cmd);
            }
            catch (Exception ex)
            {
                LogError($"Не удалось зарегистрировать команду nextlevel: {ex.Message}");
            }

            LogInfo("Плагин инициализирован. Используйте !next в чате или nextlevel в консоли.");
        }

        public void Dispose()
        {
            harmony?.UnpatchSelf();
            isTransitioning = false;
            LogInfo("Плагин выгружен");
        }

        public void OnLoadCompleted() { }
        public void PreInitPatching() { }

        // ═══════════════════════════════════════════════════════════════
        // ПОИСК МЕТОДА ДЛЯ ПАТЧА
        // ═══════════════════════════════════════════════════════════════

        private static MethodInfo FindSendChatMessageMethod()
        {
            // Ищем все перегрузки SendChatMessage на GameServer
            var methods = typeof(GameServer).GetMethods(
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);

            // Ищем метод с единственным параметром ChatMessage
            foreach (var m in methods)
            {
                if (m.Name != "SendChatMessage") continue;
                var parms = m.GetParameters();
                if (parms.Length == 1 && parms[0].ParameterType == typeof(ChatMessage))
                    return m;
            }

            // Fallback: ищем метод с ChatMessage как первым параметром
            foreach (var m in methods)
            {
                if (m.Name != "SendChatMessage") continue;
                var parms = m.GetParameters();
                if (parms.Length >= 1 && parms[0].ParameterType == typeof(ChatMessage))
                    return m;
            }

            return null;
        }

        // ═══════════════════════════════════════════════════════════════
        // HARMONY PREFIX — перехват чат-команд
        // ═══════════════════════════════════════════════════════════════

        private static bool ChatMessagePrefix(ChatMessage msg)
        {
            try
            {
                if (msg == null) return true;
                if (msg.Type != ChatMessageType.Default && msg.Type != ChatMessageType.Radio) return true;

                string text = msg.Text?.Trim();
                if (string.IsNullOrEmpty(text)) return true;

                // Только команда !next
                if (!text.StartsWith("!next", StringComparison.OrdinalIgnoreCase)) return true;

                // Парсим сложность: !next 50
                float difficulty = 30f;
                string[] parts = text.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length > 1 && float.TryParse(parts[1], out float parsed))
                    difficulty = parsed;

                Client sender = msg.SenderClient;
                string senderName = sender?.Name ?? "Unknown";

                LogInfo($"Команда !next от {senderName} (difficulty={difficulty})");

                // Проверяем права
                if (sender != null && !HasTransitionPermission(sender))
                {
                    GameMain.Server?.SendDirectChatMessage(
                        "У вас нет прав для команды !next.",
                        sender, ChatMessageType.Server);
                    return false;
                }

                // Запускаем переход
                TriggerTransition(difficulty);

                // Не рассылаем команду в чат
                return false;
            }
            catch (Exception ex)
            {
                LogError($"Ошибка в ChatMessagePrefix: {ex.Message}");
                return true;
            }
        }

        // ═══════════════════════════════════════════════════════════════
        // ПУБЛИЧНЫЙ МЕТОД: запуск перехода
        // ═══════════════════════════════════════════════════════════════

        public static void TriggerTransition(float difficulty = 30f)
        {
            if (isTransitioning)
            {
                LogInfo("Переход.");
                return;
            }

            if (GameMain.Server == null)
            {
                LogError("GameServer не инициализирован.");
                return;
            }

            if (!GameMain.Server.GameStarted)
            {
                LogError("пук.");
                return;
            }

            nextDifficulty = Math.Clamp(difficulty, 0f, 100f);
            isTransitioning = true;

            LogInfo($"Переход: difficulty={nextDifficulty}");

            GameMain.Server.SendChatMessage(
                $"некст епт сложность: {nextDifficulty:F0})...",
                ChatMessageType.Server);

            CoroutineManager.Invoke(() =>
            {
                try
                {
                    DoTransition();
                }
                catch (Exception ex)
                {
                    LogError($"Ошибка перехода: {ex.Message}\n{ex.StackTrace}");
                    isTransitioning = false;
                }
            }, delay: 1.0f);
        }

        private static void DoTransition()
        {
            var server = GameMain.Server;
            if (server == null)
            {
                isTransitioning = false;
                return;
            }

            LogInfo("стоп раунд...");
            server.EndGame();
            LogInfo("гружу");

            CoroutineManager.Invoke(() =>
            {
                try
                {
                    StartNewRound();
                }
                catch (Exception ex)
                {
                    LogError($"Ошибка старта: {ex.Message}\n{ex.StackTrace}");
                }
                finally
                {
                    isTransitioning = false;
                }
            }, delay: 2.0f);
        }

        private static void StartNewRound()
        {
            var server = GameMain.Server;
            if (server == null)
            {
                LogError("Сервер null при старте нового раунда.");
                return;
            }

            var settings = server.ServerSettings;
            if (settings != null)
            {
                settings.SelectedLevelDifficulty = nextDifficulty;
            }

            LogInfo($"Вызываем TryStartGame (difficulty={nextDifficulty})...");

            var result = server.TryStartGame();

            if (result == GameServer.TryStartGameResult.Success)
            {
                LogInfo("Новый раунд успешно запущен!");
                CoroutineManager.Invoke(() =>
                {
                    GameMain.Server?.SendChatMessage(
                        "[Переход] Новый уровень загружен!",
                        ChatMessageType.Server);
                }, delay: 3.0f);
            }
            else
            {
                LogError($"Не удалось запустить раунд: {result}");
                GameMain.Server?.SendChatMessage(
                    $"[Переход] Ошибка запуска: {result}",
                    ChatMessageType.Server);
            }
        }

        // ═══════════════════════════════════════════════════════════════
        // ПРОВЕРКА ПРАВ
        // ═══════════════════════════════════════════════════════════════

        private static bool HasTransitionPermission(Client client)
        {
            if (client == null) return false;

            // Владелец сервера
            if (client.Connection == GameMain.Server?.OwnerConnection) return true;

            // Имеет право управлять раундом
            if (client.HasPermission(ClientPermissions.ManageRound)) return true;

            // Проверяем через RoundManager_ND (если загружен)
            try
            {
                string steamId = client.AccountId.TryUnwrap(out AccountId accountId)
                    ? accountId.StringRepresentation
                    : null;

                if (!string.IsNullOrEmpty(steamId))
                {
                    var rmType = AppDomain.CurrentDomain.GetAssemblies()
                        .SelectMany(a => { try { return a.GetTypes(); } catch { return Array.Empty<Type>(); } })
                        .FirstOrDefault(t => t.FullName == "RoundManager_ND.RoundManager_ND");

                    if (rmType != null)
                    {
                        var checkMethod = rmType.GetMethod("CheckPlayerPrivilege",
                            BindingFlags.Public | BindingFlags.Static);
                        if (checkMethod != null)
                        {
                            bool allowed = (bool)checkMethod.Invoke(null, new object[] { steamId, "!next" });
                            if (allowed) return true;
                        }
                    }
                }
            }
            catch { /* RoundManager_ND не загружен */ }

            return false;
        }
    }
}
