# NexusData Game Server API — Инструкция по интеграции

Документ для AI-ассистента, реализующего клиентскую часть на игровом сервере Barotrauma.

## Аутентификация

Все запросы требуют API-ключ:
- Заголовок: `X-API-Key: ваш_ключ`
- Или: `Authorization: Bearer ваш_ключ`

Базовый URL: `https://nd.rp-asper.ru`

---

## 1. Система очереди действий (Actions)

### Обзор

`ServerActionQueue` — таблица в БД, через которую NexusData передаёт команды серверам. Сервер периодически опрашивает очередь, выполняет задачи и подтверждает выполнение (ACK).

### Типы действий

| `action_type` | Описание |
|---|---|
| `kick` | Кикнуть игрока |
| `ban` | Забанить игрока |
| `unban` | Разбанить игрока |
| `message` | Отправить личное сообщение |
| `trap` | Установить ловушку (ждать входа игрока) |

### Поле `server_name` в задаче

| Значение | Кто получит |
|---|---|
| `"ASPER1"` | Только ASPER1 |
| `"ASPER2"` | Только ASPER2 |
| `null` / `""` | Оба сервера (broadcast) |

---

### Шаг 1. Получение задач

```
GET /api/v1/server/actions?server_name=ASPER1
X-API-Key: <ключ>
```

Возвращает задачи где `server_name == "ASPER1"` **ИЛИ** `server_name IS NULL` (broadcast).

**Ответ:**
```json
[
  {
    "id": 47,
    "action_type": "ban",
    "target_steamid": "STEAM_0:1:23456789",
    "reason": "Читы",
    "duration_minutes": null,
    "message_text": null,
    "server_name": null,
    "queued_at": "2026-05-17T16:00:00",
    "ack_asper1": 0,
    "ack_asper2": 0
  },
  {
    "id": 48,
    "action_type": "trap",
    "target_steamid": "76561198000000000",
    "reason": "Подозрение",
    "server_name": "ASPER1",
    "queued_at": "2026-05-17T16:05:00",
    "ack_asper1": 0,
    "ack_asper2": 0
  }
]
```

**Формат `target_steamid`:**
- `ban` / `unban` / `kick` → **STEAM2** (`STEAM_0:X:Y`) — ванильная система Barotrauma
- `trap` → SteamID64 (`76561198...`)

---

### Шаг 2. Обработка действий

| Тип | Поведение |
|---|---|
| `kick` | Выполнить немедленно → ACK |
| `ban` | Выполнить немедленно → ACK |
| `unban` | Выполнить немедленно → ACK |
| `message` | Отправить сообщение → ACK |
| `trap` | **Не выполнять сразу.** Запомнить `steamid → action_id`. При входе игрока → ACK |

---

### Шаг 3. Подтверждение (ACK)

```
POST /api/v1/server/actions/{action_id}/ack
Content-Type: application/json
X-API-Key: <ключ>
```

**Обязательно передавать `server_name`:**

```json
{
  "success": true,
  "server_name": "ASPER1"
}
```

При ошибке:
```json
{
  "success": false,
  "error_message": "Player not found in banlist",
  "server_name": "ASPER1"
}
```

---

### Логика завершения задачи

**Targeted-задача** (`server_name` задачи = `"ASPER1"` или `"ASPER2"`):
- Помечается выполненной сразу после первого ACK от любого сервера.

**Broadcast-задача** (`server_name` задачи = `null`):

Одна запись в БД — каждый сервер видит её **независимо** через свой фильтр:

```
БД: id=47, server_name=null, ack_asper1=0, ack_asper2=0

ASPER1 GET ?server_name=ASPER1  →  видит id=47  (ack_asper1=0)
ASPER2 GET ?server_name=ASPER2  →  видит id=47  (ack_asper2=0)

ASPER1 выполняет → ACK → ack_asper1=1

ASPER1 GET ?server_name=ASPER1  →  НЕ видит id=47  (ack_asper1=1, отфильтровано)
ASPER2 GET ?server_name=ASPER2  →  видит id=47   (ack_asper2=0, ещё не подтвердил)

ASPER2 выполняет → ACK → ack_asper2=1 → executed=1 ✅ DONE

Оба сервера больше не видят id=47 (executed=1, не pending)
```

Правила:
- Поля `ack_asper1` / `ack_asper2`: `0` = ожидание, `1` = успех, `2` = ошибка
- Если любой сервер отправит `success: false` → задача помечается `failed` немедленно
- Только когда `ack_asper1 == 1` И `ack_asper2 == 1` → задача помечается `done`
- После отправки ACK сервер **больше не получает эту задачу** — повторного выполнения нет

Поля `ack_asper1` и `ack_asper2` видны в ответе `GET /server/actions` — только для информации/отладки. Клиент не должен на них полагаться для принятия решений: API сам фильтрует задачи.

---

### Шаг 4. Отправка онлайн-игроков

```
POST /api/v1/server/players?server_name=ASPER1
Content-Type: application/json
X-API-Key: <ключ>
```
```json
{
  "players": [
    {"steamid": "76561198000000000", "player_name": "Player1", "job_id": "captain", "ping": 45}
  ]
}
```

Данные хранятся отдельно для каждого сервера. В режиме «Все» в админке показываются игроки обоих серверов с колонкой «Сервер».

---

## 2. Ловушки (Traps)

### Постановка в очередь

```
POST /api/v1/game/traps/queue
Content-Type: application/json
X-API-Key: <ключ>
```
```json
{
  "player_steamid": "76561198000000000",
  "reason": "Подозрение на читы",
  "server_name": "ASPER1"
}
```

### Полный цикл

```
1. POST /traps/queue → action_id=49 в очереди
2. ASPER1: GET /server/actions → получает action_type="trap", target="76561198..."
3. ASPER1: запоминает pendingTraps["76561198..."] = 49
4. Игрок заходит на ASPER1
5. ASPER1: POST /server/actions/49/ack {success:true, server_name:"ASPER1"}
6. NexusData: создаёт PlayerTrap, отправляет Discord webhook
```

---

## 3. Баны

### Получить активные баны

```
GET /api/v1/game/bans
X-API-Key: <ключ>
```

### Проверить бан игрока

```
GET /api/v1/game/bans/check?steamid=76561198000000000
```

**Ответ (забанен):**
```json
{"banned": true, "ban_id": 5, "reason": "Читы", "expires_at": null, "is_permanent": true, "issued_by": "ND. AdminName"}
```

---

## 4. Наказания (создание)

```
POST /api/v1/game/punishments
Content-Type: application/json
X-API-Key: <ключ>
```
```json
{
  "player_steamid": "76561198000000000",
  "type": "ban",
  "reason": "Читы",
  "expires_days": 30,
  "issued_by_name": "AntiCheat"
}
```

Типы: `warning`, `reprimand`, `kick`, `ban`.

---

## 5. Разбан

```
POST /api/v1/game/unban
Content-Type: application/json
X-API-Key: <ключ>
```
```json
{
  "player_steamid": "76561198000000000",
  "reason": "Истёк срок",
  "issued_by_name": "AutoExpire",
  "server_name": null
}
```

Помечает все активные баны как `void` и ставит задачу `unban` в очередь.

---

## 6. Серверные привилегии

### Все группы

```
GET /api/v1/game/privileges/groups
```

### Все игроки с привилегиями

```
GET /api/v1/game/privileges/players
```

### Привилегии конкретного игрока

```
GET /api/v1/game/privileges/player?steamid=76561198000000000
```

**Ответ:**
```json
{
  "has_privileges": true,
  "steamid": "76561198000000000",
  "group_key": "moderator",
  "commands": ["/mute", "/kick"],
  "allowed_action_ids": [100, 101]
}
```

---

## 7. Информация об игроке

```
GET /api/v1/players/info?type=steamid&identifier=76561198000000000
```

**Ответ (фрагмент):**
```json
{
  "player_id": 42,
  "player_steamid": "76561198000000000",
  "rank_id": 3,
  "kit_level": 2,
  "achievements_data": {
    "kval_kits": {"captain": 2, "mechanic": 1},
    "items": {"revolver": 1},
    "stats": {"all": 5},
    "talents": {"captain": ["leadership"]}
  },
  "rank_name": "Старшина 1-ст",
  "kit_name": "Набор старшины"
}
```

---

## 8. Квалификационные киты

Квал-киты хранятся в `achievements_data.kval_kits` игрока: `{job_id: level}`.
Содержимое кита — в таблице `kits` с `job_id` оканчивающимся на `_k`.

```
GET /api/v1/kits?page=1&page_size=500
```

Алгоритм выдачи:
1. Игрок выбирает профессию `"captain"`
2. `kval_kits["captain"] = 2`
3. Ищем кит: `job_id="captain_k"`, `kit_level=2`
4. Выдаём предметы из `items`

---

## 9. Псевдокод клиента

```lua
local SERVER_NAME = "ASPER1"
local pendingTraps = {}

local function pollActions()
    local url = API_BASE .. "/api/v1/server/actions?server_name=" .. SERVER_NAME
    HttpRequest(url, "GET", headers, function(response)
        for _, action in ipairs(json.decode(response)) do
            processAction(action)
        end
    end)
end

local function processAction(action)
    local t = action.action_type
    if t == "kick" then
        Game.KickPlayer(action.target_steamid, action.reason)
        ackAction(action.id, true)
    elseif t == "ban" then
        Game.BanPlayer(action.target_steamid, action.reason)
        ackAction(action.id, true)
    elseif t == "unban" then
        Game.UnbanPlayer(action.target_steamid)
        ackAction(action.id, true)
    elseif t == "message" then
        Game.SendMessage(action.target_steamid, action.message_text)
        ackAction(action.id, true)
    elseif t == "trap" then
        pendingTraps[action.target_steamid] = action.id
    end
end

local function onPlayerJoined(steamid)
    local actionId = pendingTraps[steamid]
    if actionId then
        ackAction(actionId, true)
        pendingTraps[steamid] = nil
    end
end

local function ackAction(actionId, success, errorMsg)
    local url = API_BASE .. "/api/v1/server/actions/" .. actionId .. "/ack"
    local body = json.encode({
        success = success,
        server_name = SERVER_NAME,
        error_message = errorMsg
    })
    HttpRequest(url, "POST", headers, nil, body)
end

local function reportPlayers()
    local players = {}
    for _, client in ipairs(Game.GetOnlinePlayers()) do
        table.insert(players, {
            steamid = client.SteamID,
            player_name = client.Name,
            job_id = client.Job,
            ping = client.Ping
        })
    end
    local url = API_BASE .. "/api/v1/server/players?server_name=" .. SERVER_NAME
    HttpRequest(url, "POST", headers, json.encode({players = players}), nil)
end
```

---

## 10. Сводная таблица эндпоинтов

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/v1/server/actions?server_name=` | Получить pending задачи |
| POST | `/api/v1/server/actions/{id}/ack` | Подтвердить выполнение |
| POST | `/api/v1/server/players?server_name=` | Отправить онлайн-игроков |
| POST | `/api/v1/server/anticheat` | Сообщить о срабатывании античита |
| POST | `/api/v1/game/traps/queue` | Поставить ловушку в очередь |
| GET | `/api/v1/game/bans` | Список активных банов |
| GET | `/api/v1/game/bans/check?steamid=` | Проверить бан |
| POST | `/api/v1/game/punishments` | Создать наказание |
| POST | `/api/v1/game/unban` | Разбанить игрока |
| GET | `/api/v1/game/privileges/groups` | Группы привилегий |
| GET | `/api/v1/game/privileges/players` | Игроки с привилегиями |
| GET | `/api/v1/game/privileges/player?steamid=` | Привилегии игрока |
| GET | `/api/v1/players/info?type=steamid&identifier=` | Инфо игрока |
| GET | `/api/v1/kits?page=1&page_size=500` | Все киты |
| POST | `/api/v1/events` | Универсальный приём событий |

## 11. Коды ошибок

| Код | Описание |
|---|---|
| 200 | Успешный запрос |
| 400 | Некорректные параметры |
| 401 | Неверный API-ключ |
| 404 | Не найдено |
| 429 | Превышен лимит запросов |
| 500 | Внутренняя ошибка |
