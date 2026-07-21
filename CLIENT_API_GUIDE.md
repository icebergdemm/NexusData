# Client API — внешние read-only интеграции

Этот документ описывает два `GET`-эндпоинта для **внешних клиентов**, которым
нужно читать данные с сайта (не путать с `WORLD_STATE_API_GUIDE.md` — тот
описывает, что **плагин отправляет на сайт**; этот документ — про обратное
направление, чтение). Реализация — `routes/world_state.py` (`client_router`)
и `routes/warehouse.py` (`client_router`).

---

## 1. Аутентификация

Отдельный, выделенный ключ — **не тот**, что использует игровой плагин для
отправки данных (`ApiKey` в `world_state_config.json`). Задаётся в `.env` на
сервере:

```
CLIENT_API_KEY=ваш-секретный-ключ
```

Передавайте его в заголовке (предпочтительно) или query-параметром:

```
X-API-Key: ваш-секретный-ключ
```
```
?api_key=ваш-секретный-ключ
```

Пока `CLIENT_API_KEY` пуст/не задан в `.env` — **оба** эндпоинта ниже всегда
отвечают `401`, вне зависимости от переданного ключа. Это сделано намеренно
(fail closed), чтобы пустой ключ не совпадал с пустым заголовком.

---

## 2. `GET /api/v1/client/world/state` — состояние мира

### Запрос

```
GET {ApiBaseUrl}/client/world/state
GET {ApiBaseUrl}/client/world/state?round_id=<round_id>
```

- Без `round_id` — все **активные** раунды (прислали снапшот за последние
  15 секунд).
- С `round_id` — конкретный раунд (даже неактивный); `404`, если такой
  `round_id` не встречался вообще.

Всегда отдаёт самое свежее состояние на момент запроса — читает напрямую из
таблицы, которая обновляется при каждом `world/snapshot` от плагина (см.
`WORLD_STATE_API_GUIDE.md`), кеша нет.

### Ответ — всегда gzip

Тело **всегда** сжато gzip — заголовок `Content-Encoding: gzip` стоит
независимо от `Accept-Encoding` в запросе. Большинство HTTP-клиентов (curl,
`requests`, `HttpClient` и т.п.) распаковывают такой ответ автоматически по
этому заголовку. Если ваш клиент этого не делает — распакуйте
`gzip.decompress(body)` и распарсите как обычный JSON.

```json
{
  "rounds": [
    {
      "round_id": "3f9a1c2b4e5d4a7c9b0e1f2a3c4d5e6f",
      "server_name": "ASPER1",
      "level_seed": "abc123",
      "level_name": "Europan Ridge",
      "level_type": "LocationConnection",
      "level_w": 100000,
      "level_h": 50000,
      "sub_name": "Orca",

      "submarines": [{ "id": 1, "name": "Orca" }],
      "hulls": [{ "id": 12, "sub": 1, "r": [-500, -300, 200, 150] }],
      "walls": [{ "sub": 1, "r": [-500, -300, 200, 20], "h": true, "p": false }],
      "doors": [{ "id": 87, "sub": 1, "r": [-300, -300, 32, 100], "h": false }],
      "outline_w": 100000,
      "outline_h": 50000,
      "outline": [[[1000, 2000], [1050, 2000], [1050, 2100], [1000, 2100]]],

      "seq": 42,
      "t": 126.7,
      "subs": [{ "id": 1, "x": 15230, "y": -8420 }],
      "hulls_state": [{ "id": 12, "water": 0.0, "oxygen": 98.4, "fire": 0 }],
      "doors_state": [{ "id": 87, "open": 100.0 }],
      "reactors": [
        {
          "id": 55, "sub": 1, "power_on": true,
          "fission": 42.1, "turbine": 45.0, "load": 3200.0,
          "max_output": 10000.0, "temp": 55.2, "fuel": 78.0,
          "critical": false, "melted": false
        }
      ],
      "characters": [
        {
          "id": 3, "name": "Иван Петров", "species": "human", "human": true,
          "team": "Team1", "job": "captain", "steamid": "76561198000000000",
          "x": 15300, "y": -8390, "hp": 100.0, "oxygen": 100.0,
          "dead": false, "water": false, "ragdoll": false, "dir": 1, "hull": 12
        }
      ],
      "items": [
        { "id": 901, "item": "revolver", "x": 15290, "y": -8395, "cond": 87.5, "owner_t": "character", "owner_id": 3 }
      ],

      "last_update_at": "2026-07-21T12:34:56.789012",
      "is_active": true
    }
  ]
}
```

Первая половина полей (`server_name` … `outline`) — статическая геометрия
раунда (не меняется), вторая половина (`seq` … `items`) — динамическое
состояние на текущий момент. Оба блока в этом эндпоинте всегда идут вместе
(в отличие от админ-API «Обзора мира», где они разделены на два отдельных
запроса, чтобы не пересылать геометрию на каждый опрос).

**Координаты**: `hulls`/`walls`/`doors` заданы в **локальных координатах
подлодки** (`r` = `[x, y, w, h]` относительно позиции подлодки на момент
`world/init`). Чтобы получить мировые координаты в любой последующий момент,
нужно прибавить актуальную позицию подлодки из `subs[]` этого же ответа:

```
world_x = r[0] + sub.x
world_y = r[1] + sub.y
```

(где `sub` — элемент `subs[]` с тем же `id`, что и `sub` у элемента геометрии).
`outline` уже в мировых координатах, пересчитывать не нужно.

Полное описание смысла каждого поля (`hp` может быть больше 100 и т.д.) —
в `WORLD_STATE_API_GUIDE.md`, это тот же формат, что там задокументирован.

### Пример — curl

```bash
curl -H "X-API-Key: ваш-секретный-ключ" \
  "https://ваш-домен/api/v1/client/world/state" | jq
```

### Пример — Python

```python
import requests

resp = requests.get(
    "https://ваш-домен/api/v1/client/world/state",
    headers={"X-API-Key": "ваш-секретный-ключ"},
)
resp.raise_for_status()
data = resp.json()  # requests сам распаковывает gzip

for round_ in data["rounds"]:
    print(round_["server_name"], round_["level_name"], len(round_["characters"]), "персонажей")
```

---

## 3. `GET /api/v1/client/warehouse` — содержимое склада

Текущие остатки виртуального склада ресурсов диспетчерской (см. страницу
«Диспетчерская» → вкладка «Склад», `routes/warehouse.py`). Без сжатия
(обычный JSON), без параметров.

### Запрос

```
GET {ApiBaseUrl}/client/warehouse
```

### Ответ

```json
{
  "items": [
    { "identifier": "revolver", "quantity": 12 },
    { "identifier": "oxygentank", "quantity": 40 }
  ]
}
```

`identifier` — идентификатор предмета Barotrauma (тот же формат, что и в
`items[].item` эндпоинта состояния мира выше); человекочитаемое название и
иконку по нему можно получить через `GET /api/v1/admin/items/catalog`
(требует авторизованную сессию администратора, не этот ключ).

### Пример — curl

```bash
curl -H "X-API-Key: ваш-секретный-ключ" \
  "https://ваш-домен/api/v1/client/warehouse" | jq
```

---

## 4. Коды ответов (оба эндпоинта)

| Код | Когда |
|---|---|
| `200` | Успех |
| `401` | Неверный/отсутствующий `X-API-Key`, либо `CLIENT_API_KEY` не настроен на сервере |
| `404` | (только `world/state`) указан `round_id`, но такого раунда не существует |
| `503` | Сайт в режиме технических работ, либо API временно отключено консольной командой (см. `console.py` — `api off`/`maintenance on`) |
