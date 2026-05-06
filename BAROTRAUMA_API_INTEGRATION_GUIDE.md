# Полная инструкция: Интеграция Barotrauma с FastAPI сервером

**Документ предназначен для:** Реализации полной работы с API со стороны сервера Barotrauma на C#  
**Версия:** 1.0  
**Дата:** 22 апреля 2026  

---

## СОДЕРЖАНИЕ

1. [Архитектура системы](#архитектура-системы)
2. [Подготовка C# проекта](#подготовка-c-проекта)
3. [Модели данных](#модели-данных)
4. [Эндпоинты API](#эндпоинты-api)
5. [Реализация клиента на C#](#реализация-клиента-на-c)
6. [Сбор и отправка событий](#сбор-и-отправка-событий)
7. [Обработка ошибок и retry](#обработка-ошибок-и-retry)
8. [Оптимизация и батчинг](#оптимизация-и-батчинг)
9. [Деплоймент и конфигурация](#деплоймент-и-конфигурация)
10. [Тестирование](#тестирование)

---

## АРХИТЕКТУРА СИСТЕМЫ

### Общая схема

```
┌─────────────────────┐
│  Barotrauma Server  │
│  (C# GameServer)    │
└──────────┬──────────┘
           │
           │ HTTP/HTTPS
           │ JSON events
           ▼
┌─────────────────────────────────────┐
│  API Server (FastAPI - Python)      │
│  http://api.example.com:8000        │
├─────────────────────────────────────┤
│ • /api/players/search               │
│ • /events/batch                     │
│ • /auth/server-token                │
│ • /stats/player/{id}                │
│ • /players/add                      │
└──────────┬──────────────────────────┘
           │
           ▼
   ┌──────────────────┐
   │  MySQL Database  │
   └──────────────────┘
```

### Поток данных

1. **Инициализация:** Barotrauma → Получает server_token у API
2. **События:** Игровые события → Буферизируются
3. **Батчинг:** Каждые 5 сек или после 100 событий
4. **Отправка:** HTTP POST /events/batch
5. **Обработка:** API парсит, агрегирует, сохраняет в БД

---

## ПОДГОТОВКА C# ПРОЕКТА

### NuGet пакеты

```xml
<!-- Добавить в .csproj -->
<PackageReference Include="Newtonsoft.Json" Version="13.0.3" />
<PackageReference Include="System.Net.Http" Version="4.3.4" />
```

### Установка

```bash
dotnet add package Newtonsoft.Json
```

### Структура проекта

```
BarotraumaServerPlugin/
├── Config/
│   └── ApiConfig.cs              # Конфигурация API
├── Models/
│   ├── EventPayload.cs           # Структура событий
│   ├── PlayerData.cs             # Данные игрока
│   └── AuthResponse.cs           # Ответы API
├── Services/
│   ├── ApiClient.cs              # HTTP клиент
│   ├── EventBatcher.cs           # Батчинг событий
│   └── EventCollector.cs         # Сбор событий
├── Handlers/
│   ├── PlayerEventHandler.cs     # Обработчик событий игроков
│   └── GameEventHandler.cs       # Обработчик игровых событий
└── Main.cs                        # Точка входа
```

---

## МОДЕЛИ ДАННЫХ

### 1. ApiConfig.cs - Конфигурация

```csharp
using System;

namespace BarotraumaServerPlugin.Config
{
    public class ApiConfig
    {
        // URL API сервера
        public string ApiBaseUrl { get; set; } = "http://localhost:8000";
        
        // Ключ для аутентификации (API_KEY)
        public string ApiKey { get; set; } = "your-api-key-here";
        
        // Уникальный идентификатор сервера
        public string ServerName { get; set; } = "Barotrauma_Server_1";
        
        // Интервал отправки батча (мс)
        public int BatchIntervalMs { get; set; } = 5000;
        
        // Максимальный размер батча
        public int MaxBatchSize { get; set; } = 100;
        
        // Таймаут запроса (сек)
        public int RequestTimeoutSeconds { get; set; } = 30;
        
        // Максимум попыток переотправки
        public int MaxRetries { get; set; } = 3;
        
        // Задержка между попытками (мс)
        public int RetryDelayMs { get; set; } = 1000;
        
        // Включить логирование
        public bool EnableLogging { get; set; } = true;
        
        // Путь к логам
        public string LogPath { get; set; } = "./Mods/api_logs.txt";
    }
}
```

### 2. EventPayload.cs - Структура события

```csharp
using Newtonsoft.Json;
using System;
using System.Collections.Generic;

namespace BarotraumaServerPlugin.Models
{
    /// <summary>
    /// Единичное событие для отправки на API
    /// </summary>
    public class GameEvent
    {
        [JsonProperty("type")]
        public string Type { get; set; }
        
        [JsonProperty("timestamp")]
        public long Timestamp { get; set; }
        
        [JsonProperty("server_name")]
        public string ServerName { get; set; }
        
        [JsonProperty("data")]
        public Dictionary<string, object> Data { get; set; }
        
        public GameEvent()
        {
            Timestamp = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            Data = new Dictionary<string, object>();
        }
    }

    /// <summary>
    /// Батч событий для отправки
    /// </summary>
    public class EventBatch
    {
        [JsonProperty("server_name")]
        public string ServerName { get; set; }
        
        [JsonProperty("api_key")]
        public string ApiKey { get; set; }
        
        [JsonProperty("events")]
        public List<GameEvent> Events { get; set; }
        
        [JsonProperty("timestamp")]
        public long Timestamp { get; set; }
        
        public EventBatch(string serverName, string apiKey)
        {
            ServerName = serverName;
            ApiKey = apiKey;
            Events = new List<GameEvent>();
            Timestamp = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        }
        
        public int Count => Events.Count;
    }

    /// <summary>
    /// Ответ от API
    /// </summary>
    public class ApiResponse
    {
        [JsonProperty("status")]
        public string Status { get; set; }
        
        [JsonProperty("message")]
        public string Message { get; set; }
        
        [JsonProperty("processed")]
        public int Processed { get; set; }
    }
}
```

### 3. PlayerData.cs - Данные игрока

```csharp
using Newtonsoft.Json;
using System;
using System.Collections.Generic;

namespace BarotraumaServerPlugin.Models
{
    public class PlayerInfo
    {
        [JsonProperty("steamid")]
        public string SteamId { get; set; }
        
        [JsonProperty("name")]
        public string Name { get; set; }
        
        [JsonProperty("ip")]
        public string IpAddress { get; set; }
        
        [JsonProperty("playtime_seconds")]
        public int PlaytimeSeconds { get; set; }
        
        [JsonProperty("role")]
        public string Role { get; set; }
        
        [JsonProperty("join_time")]
        public long JoinTime { get; set; }
        
        public PlayerInfo()
        {
            JoinTime = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        }
    }

    /// <summary>
    /// События игрока
    /// </summary>
    public enum PlayerEventType
    {
        Join,           // Игрок присоединился
        Leave,          // Игрок отключился
        Death,          // Игрок погиб
        Kill,           // Игрок убил
        MissionStart,   // Миссия началась
        MissionEnd,     // Миссия завершена
        Chat,           // Сообщение в чат
        RoleChange,     // Смена роли
        ItemPickup,     // Подбор предмета
        ItemUse         // Использование предмета
    }

    /// <summary>
    /// Событие убийства
    /// </summary>
    public class KillEvent
    {
        public string KillerSteamId { get; set; }
        public string KillerName { get; set; }
        public string VictimSteamId { get; set; }
        public string VictimName { get; set; }
        public string WeaponType { get; set; }
        public long Timestamp { get; set; }
    }

    /// <summary>
    /// Событие завершения миссии
    /// </summary>
    public class MissionEndEvent
    {
        public bool Success { get; set; }
        public string MissionType { get; set; }
        public int Duration { get; set; }
        public List<string> ParticipantsSteamIds { get; set; }
        public int RewardMoney { get; set; }
        public long Timestamp { get; set; }
    }
}
```

---

## ЭНДПОИНТЫ API

### Для реализации со стороны FastAPI (уже есть)

```
POST   /events/batch              - Отправка батча событий
POST   /players/add               - Добавление/обновление игрока
POST   /players/update            - Обновление данных игрока
GET    /players/search?q=         - Поиск игрока
GET    /auth/server-token         - Получение токена сервера
POST   /stats/player/{id}/update  - Обновление статистики
```

### Пример запроса POST /events/batch

```json
{
  "server_name": "Barotrauma_Server_1",
  "api_key": "your-api-key-here",
  "timestamp": 1713793200,
  "events": [
    {
      "type": "player.join",
      "timestamp": 1713793100,
      "server_name": "Barotrauma_Server_1",
      "data": {
        "steamid": "76561198000000000",
        "name": "Player Name",
        "ip": "192.168.1.100",
        "role": "Captain"
      }
    },
    {
      "type": "player.kill",
      "timestamp": 1713793150,
      "server_name": "Barotrauma_Server_1",
      "data": {
        "killer_steamid": "76561198000000001",
        "killer_name": "Killer Name",
        "victim_steamid": "76561198000000002",
        "victim_name": "Victim Name",
        "weapon": "Revolver",
        "reason": "PVP"
      }
    }
  ]
}
```

### Пример ответа

```json
{
  "status": "success",
  "message": "Events processed successfully",
  "processed": 2
}
```

---

## РЕАЛИЗАЦИЯ КЛИЕНТА НА C#

### 1. ApiClient.cs - HTTP клиент

```csharp
using Newtonsoft.Json;
using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using BarotraumaServerPlugin.Config;
using BarotraumaServerPlugin.Models;

namespace BarotraumaServerPlugin.Services
{
    public class ApiClient
    {
        private readonly ApiConfig _config;
        private readonly HttpClient _httpClient;
        private readonly ILogger _logger;

        public ApiClient(ApiConfig config, ILogger logger)
        {
            _config = config ?? throw new ArgumentNullException(nameof(config));
            _logger = logger;
            
            // Создание HttpClient с таймаутом
            _httpClient = new HttpClient();
            _httpClient.Timeout = TimeSpan.FromSeconds(config.RequestTimeoutSeconds);
            _httpClient.DefaultRequestHeaders.Add("User-Agent", "BarotraumaServerPlugin/1.0");
            _httpClient.DefaultRequestHeaders.Add("Content-Type", "application/json");
        }

        /// <summary>
        /// Отправка батча событий на API
        /// </summary>
        public async Task<bool> SendEventBatchAsync(EventBatch batch)
        {
            if (batch == null || batch.Count == 0)
                return true;

            int retries = 0;
            while (retries < _config.MaxRetries)
            {
                try
                {
                    var json = JsonConvert.SerializeObject(batch, Formatting.Indented);
                    var content = new StringContent(json, Encoding.UTF8, "application/json");

                    var url = $"{_config.ApiBaseUrl}/events/batch";
                    _logger?.Log($"Sending batch with {batch.Count} events to {url}");

                    var response = await _httpClient.PostAsync(url, content);

                    if (response.IsSuccessStatusCode)
                    {
                        var responseBody = await response.Content.ReadAsStringAsync();
                        var apiResponse = JsonConvert.DeserializeObject<ApiResponse>(responseBody);
                        
                        _logger?.Log($"✓ Batch sent successfully. Processed: {apiResponse?.Processed} events");
                        return true;
                    }
                    else
                    {
                        _logger?.Log($"✗ API returned error: {response.StatusCode}");
                        retries++;
                        
                        if (retries < _config.MaxRetries)
                        {
                            await Task.Delay(_config.RetryDelayMs * retries);
                        }
                    }
                }
                catch (HttpRequestException ex)
                {
                    _logger?.Log($"✗ Network error: {ex.Message}");
                    retries++;
                    
                    if (retries < _config.MaxRetries)
                    {
                        await Task.Delay(_config.RetryDelayMs * retries);
                    }
                }
                catch (Exception ex)
                {
                    _logger?.Log($"✗ Error sending batch: {ex.Message}");
                    return false;
                }
            }

            _logger?.Log($"✗ Failed to send batch after {_config.MaxRetries} retries");
            return false;
        }

        /// <summary>
        /// Добавление/обновление игрока на API
        /// </summary>
        public async Task<bool> AddOrUpdatePlayerAsync(PlayerInfo player)
        {
            try
            {
                var data = new
                {
                    steamid = player.SteamId,
                    name = player.Name,
                    ip = player.IpAddress,
                    playtime = player.PlaytimeSeconds,
                    role = player.Role,
                    api_key = _config.ApiKey
                };

                var json = JsonConvert.SerializeObject(data);
                var content = new StringContent(json, Encoding.UTF8, "application/json");

                var url = $"{_config.ApiBaseUrl}/players/add";
                var response = await _httpClient.PostAsync(url, content);

                if (response.IsSuccessStatusCode)
                {
                    _logger?.Log($"✓ Player {player.Name} ({player.SteamId}) added/updated");
                    return true;
                }
                else
                {
                    _logger?.Log($"✗ Failed to add player: {response.StatusCode}");
                    return false;
                }
            }
            catch (Exception ex)
            {
                _logger?.Log($"✗ Error adding player: {ex.Message}");
                return false;
            }
        }

        /// <summary>
        /// Получение информации об игроке
        /// </summary>
        public async Task<dynamic> SearchPlayerAsync(string query)
        {
            try
            {
                var url = $"{_config.ApiBaseUrl}/players/search?q={Uri.EscapeDataString(query)}&api_key={_config.ApiKey}";
                var response = await _httpClient.GetAsync(url);

                if (response.IsSuccessStatusCode)
                {
                    var responseBody = await response.Content.ReadAsStringAsync();
                    var result = JsonConvert.DeserializeObject<dynamic>(responseBody);
                    return result;
                }
                else
                {
                    _logger?.Log($"✗ Search failed: {response.StatusCode}");
                    return null;
                }
            }
            catch (Exception ex)
            {
                _logger?.Log($"✗ Error searching player: {ex.Message}");
                return null;
            }
        }

        public void Dispose()
        {
            _httpClient?.Dispose();
        }
    }
}
```

### 2. EventBatcher.cs - Батчинг событий

```csharp
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using BarotraumaServerPlugin.Config;
using BarotraumaServerPlugin.Models;

namespace BarotraumaServerPlugin.Services
{
    public class EventBatcher
    {
        private readonly ApiConfig _config;
        private readonly ApiClient _apiClient;
        private readonly ILogger _logger;
        
        private EventBatch _currentBatch;
        private DateTime _lastSendTime;
        private object _lockObject = new object();

        public EventBatcher(ApiConfig config, ApiClient apiClient, ILogger logger)
        {
            _config = config;
            _apiClient = apiClient;
            _logger = logger;
            _lastSendTime = DateTime.UtcNow;
            _currentBatch = new EventBatch(_config.ServerName, _config.ApiKey);
        }

        /// <summary>
        /// Добавить событие в батч
        /// </summary>
        public void AddEvent(GameEvent gameEvent)
        {
            lock (_lockObject)
            {
                _currentBatch.Events.Add(gameEvent);
                
                // Отправить, если батч переполнен
                if (_currentBatch.Count >= _config.MaxBatchSize)
                {
                    FlushBatchAsync();
                }
            }
        }

        /// <summary>
        /// Принудительно отправить батч
        /// </summary>
        public async Task FlushBatchAsync()
        {
            lock (_lockObject)
            {
                if (_currentBatch.Count == 0)
                    return;

                var batchToSend = _currentBatch;
                _currentBatch = new EventBatch(_config.ServerName, _config.ApiKey);
                _lastSendTime = DateTime.UtcNow;

                // Отправить асинхронно
                _ = Task.Run(() => _apiClient.SendEventBatchAsync(batchToSend));
            }
        }

        /// <summary>
        /// Проверить, нужно ли отправить батч по таймеру
        /// </summary>
        public void CheckAndFlushIfNeeded()
        {
            var elapsed = (DateTime.UtcNow - _lastSendTime).TotalMilliseconds;
            
            if (elapsed >= _config.BatchIntervalMs)
            {
                FlushBatchAsync();
            }
        }

        /// <summary>
        /// Получить количество событий в буфере
        /// </summary>
        public int GetPendingEventCount()
        {
            lock (_lockObject)
            {
                return _currentBatch.Count;
            }
        }
    }
}
```

### 3. EventCollector.cs - Сбор событий

```csharp
using System;
using System.Collections.Generic;
using BarotraumaServerPlugin.Config;
using BarotraumaServerPlugin.Models;

namespace BarotraumaServerPlugin.Services
{
    public class EventCollector
    {
        private readonly EventBatcher _batcher;
        private readonly ILogger _logger;
        private readonly ApiConfig _config;

        public EventCollector(EventBatcher batcher, ILogger logger, ApiConfig config)
        {
            _batcher = batcher;
            _logger = logger;
            _config = config;
        }

        /// <summary>
        /// Событие: Игрок присоединился
        /// </summary>
        public void OnPlayerJoined(string steamId, string playerName, string ipAddress, string role)
        {
            var gameEvent = new GameEvent
            {
                Type = "player.join",
                ServerName = _config.ServerName,
                Data = new Dictionary<string, object>
                {
                    { "steamid", steamId },
                    { "name", playerName },
                    { "ip", ipAddress },
                    { "role", role }
                }
            };

            _logger?.Log($"Event: Player joined - {playerName} ({steamId})");
            _batcher.AddEvent(gameEvent);
        }

        /// <summary>
        /// Событие: Игрок отключился
        /// </summary>
        public void OnPlayerLeft(string steamId, string playerName, int playtimeSeconds)
        {
            var gameEvent = new GameEvent
            {
                Type = "player.leave",
                ServerName = _config.ServerName,
                Data = new Dictionary<string, object>
                {
                    { "steamid", steamId },
                    { "name", playerName },
                    { "playtime_seconds", playtimeSeconds }
                }
            };

            _logger?.Log($"Event: Player left - {playerName} ({steamId})");
            _batcher.AddEvent(gameEvent);
        }

        /// <summary>
        /// Событие: Убийство
        /// </summary>
        public void OnPlayerKill(string killerSteamId, string killerName, 
                                  string victimSteamId, string victimName, 
                                  string weapon, string reason = "combat")
        {
            var gameEvent = new GameEvent
            {
                Type = "player.kill",
                ServerName = _config.ServerName,
                Data = new Dictionary<string, object>
                {
                    { "killer_steamid", killerSteamId },
                    { "killer_name", killerName },
                    { "victim_steamid", victimSteamId },
                    { "victim_name", victimName },
                    { "weapon", weapon },
                    { "reason", reason }
                }
            };

            _logger?.Log($"Event: Kill - {killerName} killed {victimName} with {weapon}");
            _batcher.AddEvent(gameEvent);
        }

        /// <summary>
        /// Событие: Смерть
        /// </summary>
        public void OnPlayerDeath(string steamId, string playerName, string cause = "unknown")
        {
            var gameEvent = new GameEvent
            {
                Type = "player.death",
                ServerName = _config.ServerName,
                Data = new Dictionary<string, object>
                {
                    { "steamid", steamId },
                    { "name", playerName },
                    { "cause", cause }
                }
            };

            _logger?.Log($"Event: Death - {playerName} died ({cause})");
            _batcher.AddEvent(gameEvent);
        }

        /// <summary>
        /// Событие: Начало миссии
        /// </summary>
        public void OnMissionStart(string missionType, List<string> playerSteamIds)
        {
            var gameEvent = new GameEvent
            {
                Type = "mission.start",
                ServerName = _config.ServerName,
                Data = new Dictionary<string, object>
                {
                    { "mission_type", missionType },
                    { "participants", playerSteamIds }
                }
            };

            _logger?.Log($"Event: Mission started - {missionType} with {playerSteamIds.Count} players");
            _batcher.AddEvent(gameEvent);
        }

        /// <summary>
        /// Событие: Завершение миссии
        /// </summary>
        public void OnMissionEnd(string missionType, bool success, 
                                 List<string> playerSteamIds, 
                                 int duration, int reward)
        {
            var gameEvent = new GameEvent
            {
                Type = "mission.end",
                ServerName = _config.ServerName,
                Data = new Dictionary<string, object>
                {
                    { "mission_type", missionType },
                    { "success", success },
                    { "participants", playerSteamIds },
                    { "duration_seconds", duration },
                    { "reward", reward }
                }
            };

            var status = success ? "SUCCESS" : "FAILED";
            _logger?.Log($"Event: Mission ended - {missionType} ({status}), reward: {reward}");
            _batcher.AddEvent(gameEvent);
        }

        /// <summary>
        /// Событие: Сообщение в чат
        /// </summary>
        public void OnChatMessage(string steamId, string playerName, string message)
        {
            var gameEvent = new GameEvent
            {
                Type = "chat.message",
                ServerName = _config.ServerName,
                Data = new Dictionary<string, object>
                {
                    { "steamid", steamId },
                    { "name", playerName },
                    { "message", message }
                }
            };

            _batcher.AddEvent(gameEvent);
        }
    }
}
```

---

## СБОР И ОТПРАВКА СОБЫТИЙ

### 1. Обработчик событий Barotrauma

```csharp
using Barotrauma;
using System;
using System.Collections.Generic;
using System.Linq;
using BarotraumaServerPlugin.Services;
using BarotraumaServerPlugin.Config;

namespace BarotraumaServerPlugin.Handlers
{
    public class GameEventHandler
    {
        private readonly EventCollector _eventCollector;
        private readonly EventBatcher _eventBatcher;
        private readonly ILogger _logger;
        private readonly ApiConfig _config;

        private Dictionary<Character, long> _playerJoinTimes = new Dictionary<Character, long>();

        public GameEventHandler(EventCollector eventCollector, EventBatcher eventBatcher, 
                               ILogger logger, ApiConfig config)
        {
            _eventCollector = eventCollector;
            _eventBatcher = eventBatcher;
            _logger = logger;
            _config = config;

            // Регистрация хуков событий Barotrauma
            GameMain.Server.ServerSettings.OnServerStarted += OnServerStarted;
            GameMain.Server.OnClientDisconnected += OnClientDisconnected;
            Character.OnDeathHandler += OnCharacterDeath;
            GameMain.GameSession.OnMissionEnded += OnMissionEnded;
        }

        /// <summary>
        /// Сервер запущен
        /// </summary>
        private void OnServerStarted()
        {
            _logger?.Log("=== Barotrauma Server Started ===");
        }

        /// <summary>
        /// Клиент подключился (игрок присоединился)
        /// </summary>
        public void OnClientConnected(Client client)
        {
            try
            {
                var character = client.Character;
                if (character == null) return;

                string steamId = client.SteamID.ToString();
                string playerName = client.Name;
                string ipAddress = client.Connection?.EndPoint?.Address?.ToString() ?? "unknown";
                string role = character.Specification?.Name ?? "Unknown";

                _eventCollector.OnPlayerJoined(steamId, playerName, ipAddress, role);
                _playerJoinTimes[character] = DateTimeOffset.UtcNow.ToUnixTimeSeconds();

                _logger?.Log($"Player connected: {playerName} ({steamId})");
            }
            catch (Exception ex)
            {
                _logger?.Log($"Error in OnClientConnected: {ex.Message}");
            }
        }

        /// <summary>
        /// Клиент отключился (игрок вышел)
        /// </summary>
        private void OnClientDisconnected(Client client)
        {
            try
            {
                var character = client.Character;
                if (character == null) return;

                string steamId = client.SteamID.ToString();
                string playerName = client.Name;
                
                int playtimeSeconds = 0;
                if (_playerJoinTimes.TryGetValue(character, out long joinTime))
                {
                    playtimeSeconds = (int)(DateTimeOffset.UtcNow.ToUnixTimeSeconds() - joinTime);
                    _playerJoinTimes.Remove(character);
                }

                _eventCollector.OnPlayerLeft(steamId, playerName, playtimeSeconds);
                
                _logger?.Log($"Player disconnected: {playerName} ({steamId}), playtime: {playtimeSeconds}s");
            }
            catch (Exception ex)
            {
                _logger?.Log($"Error in OnClientDisconnected: {ex.Message}");
            }
        }

        /// <summary>
        /// Персонаж погиб (смерть)
        /// </summary>
        private void OnCharacterDeath(Character deadCharacter, CauseOfDeath causeOfDeath)
        {
            try
            {
                if (deadCharacter?.Client == null) return;

                string steamId = deadCharacter.Client.SteamID.ToString();
                string playerName = deadCharacter.Client.Name;
                string cause = causeOfDeath?.Type.ToString() ?? "unknown";

                // Если есть убийца (игрок)
                if (causeOfDeath?.AttackerCharacter != null && 
                    causeOfDeath.AttackerCharacter.Client != null)
                {
                    var killer = causeOfDeath.AttackerCharacter;
                    string killerSteamId = killer.Client.SteamID.ToString();
                    string killerName = killer.Client.Name;
                    string weapon = causeOfDeath?.DamageSource?.Name ?? "unknown";

                    _eventCollector.OnPlayerKill(killerSteamId, killerName, 
                                                 steamId, playerName, weapon, "pvp");
                }
                else
                {
                    // Смерть от окружения/монстров
                    _eventCollector.OnPlayerDeath(steamId, playerName, cause);
                }

                _logger?.Log($"Character death: {playerName} ({cause})");
            }
            catch (Exception ex)
            {
                _logger?.Log($"Error in OnCharacterDeath: {ex.Message}");
            }
        }

        /// <summary>
        /// Миссия завершена
        /// </summary>
        private void OnMissionEnded(GameSession session, Mission mission, bool missionSuccessful)
        {
            try
            {
                var players = GameMain.Server.ConnectedClients
                    .Where(c => c.Character != null)
                    .Select(c => c.SteamID.ToString())
                    .ToList();

                int durationSeconds = (int)session.GameTime.TotalSeconds;
                int reward = missionSuccessful ? 5000 : 0; // Пример: 5000 за успешную миссию

                _eventCollector.OnMissionEnd(
                    mission?.Name ?? "Unknown",
                    missionSuccessful,
                    players,
                    durationSeconds,
                    reward
                );

                _logger?.Log($"Mission ended: {mission?.Name} - {(missionSuccessful ? "SUCCESS" : "FAILED")}");
            }
            catch (Exception ex)
            {
                _logger?.Log($"Error in OnMissionEnded: {ex.Message}");
            }
        }

        /// <summary>
        /// Вызвать периодически (каждый кадр или каждую секунду)
        /// для проверки отправки батча по таймеру
        /// </summary>
        public void Update()
        {
            _eventBatcher.CheckAndFlushIfNeeded();
        }

        public void Dispose()
        {
            GameMain.Server.ServerSettings.OnServerStarted -= OnServerStarted;
            GameMain.Server.OnClientDisconnected -= OnClientDisconnected;
            Character.OnDeathHandler -= OnCharacterDeath;
            GameMain.GameSession.OnMissionEnded -= OnMissionEnded;
        }
    }
}
```

### 2. Main.cs - Точка входа плагина

```csharp
using Barotrauma;
using System;
using System.IO;
using BarotraumaServerPlugin.Config;
using BarotraumaServerPlugin.Services;
using BarotraumaServerPlugin.Handlers;

namespace BarotraumaServerPlugin
{
    public class BarotraumaApiIntegration : IAssemblyPlugin
    {
        public static Version PluginVersion => new Version("1.0.0");
        public static string PluginName => "Barotrauma API Integration";

        private ApiConfig _config;
        private ApiClient _apiClient;
        private EventBatcher _eventBatcher;
        private EventCollector _eventCollector;
        private GameEventHandler _gameEventHandler;
        private FileLogger _logger;

        public void Initialize()
        {
            try
            {
                // Инициализация логирования
                _logger = new FileLogger();
                _logger.Log("=== Barotrauma API Integration Plugin Initializing ===");

                // Загрузка конфигурации
                _config = LoadConfig();
                _logger.Log($"Config loaded: {_config.ApiBaseUrl}");

                // Инициализация компонентов
                _apiClient = new ApiClient(_config, _logger);
                _eventBatcher = new EventBatcher(_config, _apiClient, _logger);
                _eventCollector = new EventCollector(_eventBatcher, _logger, _config);
                _gameEventHandler = new GameEventHandler(_eventCollector, _eventBatcher, _logger, _config);

                // Регистрация обновления
                GameMain.LuaCs?.Networking?.Loopback?.RegisterEventHandler(Update);

                _logger.Log("✓ Plugin initialized successfully");
            }
            catch (Exception ex)
            {
                _logger?.Log($"✗ Error during initialization: {ex.Message}");
                throw;
            }
        }

        private ApiConfig LoadConfig()
        {
            string configPath = Path.Combine(Directory.GetCurrentDirectory(), "Mods", "api_config.json");
            
            if (File.Exists(configPath))
            {
                try
                {
                    string json = File.ReadAllText(configPath);
                    var config = Newtonsoft.Json.JsonConvert.DeserializeObject<ApiConfig>(json);
                    return config ?? new ApiConfig();
                }
                catch (Exception ex)
                {
                    _logger?.Log($"Error loading config: {ex.Message}, using defaults");
                }
            }

            return new ApiConfig();
        }

        private void Update()
        {
            try
            {
                if (_gameEventHandler != null)
                {
                    _gameEventHandler.Update();
                }
            }
            catch (Exception ex)
            {
                _logger?.Log($"Error in Update: {ex.Message}");
            }
        }

        public void Dispose()
        {
            try
            {
                _logger?.Log("=== Plugin Disposing ===");
                
                // Отправить оставшиеся события
                _ = _eventBatcher?.FlushBatchAsync();
                System.Threading.Thread.Sleep(1000); // Дать время на отправку

                _gameEventHandler?.Dispose();
                _apiClient?.Dispose();
                
                _logger?.Log("✓ Plugin disposed");
                _logger?.Dispose();
            }
            catch (Exception ex)
            {
                _logger?.Log($"Error during disposal: {ex.Message}");
            }
        }
    }

    /// <summary>
    /// Простой логгер в файл
    /// </summary>
    public class FileLogger : ILogger
    {
        private readonly string _logPath;

        public FileLogger()
        {
            _logPath = Path.Combine(Directory.GetCurrentDirectory(), "Mods", "api_plugin.log");
        }

        public void Log(string message)
        {
            try
            {
                string timestamp = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
                string logEntry = $"[{timestamp}] {message}";
                
                File.AppendAllText(_logPath, logEntry + Environment.NewLine);
                
                // Также вывести в консоль
                System.Console.WriteLine(logEntry);
            }
            catch { }
        }

        public void Dispose()
        {
        }
    }

    public interface ILogger
    {
        void Log(string message);
        void Dispose();
    }
}
```

---

## ОБРАБОТКА ОШИБОК И RETRY

### Стратегия Retry с экспоненциальной задержкой

```csharp
public class RetryPolicy
{
    public static async Task<T> ExecuteWithRetryAsync<T>(
        Func<Task<T>> operation,
        int maxRetries = 3,
        int initialDelayMs = 1000)
    {
        int delay = initialDelayMs;
        
        for (int attempt = 0; attempt < maxRetries; attempt++)
        {
            try
            {
                return await operation();
            }
            catch (HttpRequestException ex) when (attempt < maxRetries - 1)
            {
                // Экспоненциальная задержка: 1s, 2s, 4s...
                await Task.Delay(delay);
                delay *= 2;
            }
        }

        throw new Exception("Max retries exceeded");
    }
}
```

### Обработка критических ошибок

```csharp
public void HandleApiError(Exception ex)
{
    if (ex is HttpRequestException)
    {
        // Сетевая ошибка - переподключиться
        _logger?.Log($"Network error: {ex.Message}. Will retry...");
    }
    else if (ex is JsonException)
    {
        // Ошибка парсинга - логировать и продолжить
        _logger?.Log($"JSON parse error: {ex.Message}");
    }
    else if (ex is TaskCanceledException)
    {
        // Таймаут - увеличить интервал отправки
        _logger?.Log("Request timeout. Increasing batch interval...");
    }
}
```

---

## ОПТИМИЗАЦИЯ И БАТЧИНГ

### Стратегия батчинга

```csharp
public class SmartBatcher
{
    // Отправить если:
    // 1. 100 событий накопилось
    // 2. 5 секунд прошло
    // 3. Критичное событие (миссия, рейд)
    
    private List<GameEvent> _buffer = new List<GameEvent>();
    private DateTime _lastFlush = DateTime.UtcNow;
    private const int MAX_BATCH = 100;
    private const int FLUSH_INTERVAL_MS = 5000;

    public void Add(GameEvent evt, bool isCritical = false)
    {
        _buffer.Add(evt);

        bool shouldFlush = _buffer.Count >= MAX_BATCH ||
                          (DateTime.UtcNow - _lastFlush).TotalMilliseconds >= FLUSH_INTERVAL_MS ||
                          isCritical; // Критичные события отправляются сразу

        if (shouldFlush)
        {
            Flush();
        }
    }

    private void Flush()
    {
        if (_buffer.Count == 0) return;

        var batch = _buffer.ToList();
        _buffer.Clear();
        _lastFlush = DateTime.UtcNow;

        _ = Task.Run(() => SendBatch(batch));
    }
}
```

### Метрики мониторинга

```csharp
public class PluginMetrics
{
    public int EventsSent { get; set; }
    public int EventsBuffered { get; set; }
    public int ErrorsOccurred { get; set; }
    public double AverageResponseTimeMs { get; set; }
    public DateTime LastSuccessfulSend { get; set; }
    
    public void LogMetrics()
    {
        Console.WriteLine($@"
            === Plugin Metrics ===
            Events Sent: {EventsSent}
            Events Buffered: {EventsBuffered}
            Errors: {ErrorsOccurred}
            Avg Response: {AverageResponseTimeMs}ms
            Last Send: {LastSuccessfulSend:yyyy-MM-dd HH:mm:ss}
        ");
    }
}
```

---

## ДЕПЛОЙМЕНТ И КОНФИГУРАЦИЯ

### Конфиг файл (api_config.json)

```json
{
  "ApiBaseUrl": "http://api.example.com:8000",
  "ApiKey": "your-secret-api-key-here",
  "ServerName": "Barotrauma_Server_1",
  "BatchIntervalMs": 5000,
  "MaxBatchSize": 100,
  "RequestTimeoutSeconds": 30,
  "MaxRetries": 3,
  "RetryDelayMs": 1000,
  "EnableLogging": true,
  "LogPath": "./Mods/api_logs.txt"
}
```

### Структура плагина

```
Mods/
├── BarotraumaApiIntegration.dll      # Скомпилированная DLL
├── api_config.json                    # Конфиг (создать вручную)
└── api_logs.txt                       # Логи (создается автоматически)
```

### Установка

1. Скомпилировать проект:
```bash
dotnet build -c Release
```

2. Скопировать DLL в Mods:
```bash
cp bin/Release/BarotraumaApiIntegration.dll [BarotraumaServer]/Mods/
```

3. Создать api_config.json

4. Перезагрузить сервер

---

## ТЕСТИРОВАНИЕ

### Unit тесты

```csharp
using Xunit;
using Moq;
using BarotraumaServerPlugin.Services;
using BarotraumaServerPlugin.Models;

namespace BarotraumaServerPlugin.Tests
{
    public class EventBatcherTests
    {
        [Fact]
        public void AddEvent_ShouldFlushWhenMaxBatchSizeReached()
        {
            // Arrange
            var config = new Config.ApiConfig { MaxBatchSize = 2 };
            var apiClient = new Mock<ApiClient>();
            var logger = new Mock<ILogger>();
            
            var batcher = new EventBatcher(config, apiClient.Object, logger.Object);

            // Act
            batcher.AddEvent(new GameEvent { Type = "test1" });
            batcher.AddEvent(new GameEvent { Type = "test2" });

            // Assert
            Assert.Equal(0, batcher.GetPendingEventCount()); // Должен отправиться
        }

        [Fact]
        public void CheckAndFlushIfNeeded_ShouldFlushAfterInterval()
        {
            // Arrange
            var config = new Config.ApiConfig { BatchIntervalMs = 100 };
            var apiClient = new Mock<ApiClient>();
            var logger = new Mock<ILogger>();
            
            var batcher = new EventBatcher(config, apiClient.Object, logger.Object);
            batcher.AddEvent(new GameEvent { Type = "test" });

            // Act
            System.Threading.Thread.Sleep(150);
            batcher.CheckAndFlushIfNeeded();

            // Assert
            Assert.Equal(0, batcher.GetPendingEventCount());
        }
    }
}
```

### Integration тест

```csharp
[Fact]
public async Task FullFlow_SendBatchToApi()
{
    // Arrange
    var config = LoadTestConfig();
    var apiClient = new ApiClient(config, new TestLogger());
    var batcher = new EventBatcher(config, apiClient, new TestLogger());
    var collector = new EventCollector(batcher, new TestLogger(), config);

    // Act
    collector.OnPlayerJoined("76561198000000000", "TestPlayer", "127.0.0.1", "Captain");
    collector.OnPlayerKill("76561198000000001", "Killer", 
                          "76561198000000000", "Victim", "Revolver");
    
    await batcher.FlushBatchAsync();

    // Assert
    // Проверить в БД, что события сохранились
}
```

### Тестирование вручную

```bash
# 1. Запустить API сервер
python main.py

# 2. Запустить Barotrauma сервер с плагином
./DedicatedServer.exe

# 3. Проверить логи
cat Mods/api_plugin.log

# 4. Проверить в БД
SELECT * FROM admin_action_logs WHERE action LIKE 'player.%';

# 5. Запросить статистику игрока
curl http://localhost:8000/players/search?q=TestPlayer
```

---

## КРИТЕРИИ УСПЕШНОСТИ

✓ События передаются батчами по 100 шт или каждые 5 сек  
✓ Размер батча < 50 KB (сжимается до ~10 KB)  
✓ Время ответа API < 100 ms  
✓ Нет потери событий при разрыве соединения  
✓ Автоматический retry с экспоненциальной задержкой  
✓ Полные логи всех операций  
✓ Нулевое влияние на производительность игрового сервера  

---

## ПОМОЩЬ И ОТЛАДКА

### Проверить подключение
```csharp
var testClient = new ApiClient(config, logger);
var result = await testClient.SearchPlayerAsync("test");
```

### Мониторинг буфера событий
```csharp
Console.WriteLine($"Pending events: {batcher.GetPendingEventCount()}");
```

### Включить verbose логирование
```json
{
  "EnableLogging": true,
  "LogPath": "./Mods/verbose.log"
}
```

### Проверить конфиг
```bash
cat Mods/api_config.json | jq .
```

---

**Документ создан: 22 апреля 2026**  
**Версия: 1.0**  
**Автор: AI Assistant**
