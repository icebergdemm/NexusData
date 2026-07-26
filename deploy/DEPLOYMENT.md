# Развёртывание NexusData с нуля / перенос на другую машину

Полная инструкция по установке сайта NexusData (FastAPI + React SPA + MySQL +
in-process Discord-бот) на чистый Linux-сервер (Ubuntu/Debian) и по переносу
на другую машину.

Смежные документы:
- [`PROXY_DEPLOYMENT.md`](PROXY_DEPLOYMENT.md) — как устроен always-up прокси и
  режим технических работ (нужно прочитать).
- [`onlyoffice/NATIVE_INSTALL.md`](onlyoffice/NATIVE_INSTALL.md) — опциональный
  ONLYOFFICE Document Server для редактирования документов.

---

## 0. Архитектура (что мы разворачиваем)

```
                         (HTTPS 443)
   Браузер / игровой сервер ──► nginx ──► proxy_server.py ──► api_server.py
                                (:443)     (публичный :8000)   (внутренний :8001)
                                                                     │
                                                       ┌─────────────┼───────────────┐
                                              in-process       фоновые воркеры   Discord-бот
                                              FastAPI (SPA+API)  (планировщик)   (discord.py)
                                                                     │
                                                                  MySQL
```

- **`proxy_server.py`** — всегда живой обратный прокси, держит публичный порт
  (8000). Показывает страницу техработ, когда бэкенд остановлен/упал, и сам
  перезапускает `api_server.py`. Это **точка запуска приложения**.
- **`api_server.py`** — реальный бэкенд (FastAPI) на внутреннем порту 8001.
  Запускается прокси как дочерний процесс.
- **Discord-бот** работает **внутри** процесса бэкенда (не отдельный процесс) —
  см. `discord_bot/bot.py`. Токен берётся из `.env`.
- **nginx** терминирует HTTPS и проксирует на `proxy_server.py` (:8000).

---

## 1. Требования

| Компонент | Версия | Зачем |
|---|---|---|
| Linux | Ubuntu 22.04+/Debian 12+ | ОС |
| Python | 3.11+ (3.12 рекомендуется) | бэкенд |
| Node.js | 20 LTS+ | сборка фронтенда (Vite/React 19) |
| MySQL / MariaDB | 8.0+ / 10.6+ | БД |
| nginx | любой актуальный | HTTPS-прокси |
| git | — | получение кода |

Опционально: ONLYOFFICE Document Server (редактирование документов), доступ по
SFTP к игровым серверам (тейл логов), Pterodactyl (управление тест-серверами).

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git nginx \
    mysql-server build-essential libmysqlclient-dev curl
# Node.js 20 LTS
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

---

## 2. Получить код

```bash
sudo mkdir -p /opt/nexusdata && sudo chown "$USER" /opt/nexusdata
cd /opt/nexusdata
git clone <URL_РЕПОЗИТОРИЯ> .
# структура: proxy_server.py, api_server.py, main.py, frontend/, deploy/, ...
```

> ⚠️ Файл `.env` **намеренно не в репозитории** (в `.gitignore`). Его нужно
> создать вручную — см. шаг 5.

---

## 3. Python-бэкенд

```bash
cd /opt/nexusdata
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` уже включает `discord.py` (бот встроен в приложение) и
`starlette`/`watchfiles` для прокси.

---

## 4. Сборка фронтенда

React-SPA собирается в `frontend/dist/`, откуда его раздаёт бэкенд
(`main.py` монтирует `/admin/assets` и отдаёт `frontend/dist/index.html`).

```bash
cd /opt/nexusdata/frontend
npm ci
npm run build            # tsc -b && vite build → frontend/dist
cd ..
```

Пересобирать фронтенд нужно при каждом изменении в `frontend/`.

---

## 5. Файл `.env`

```bash
cd /opt/nexusdata
cp .env.example .env
```

Сгенерировать надёжные секреты:

```bash
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
python3 -c "import secrets; print('API_KEY='    + secrets.token_urlsafe(32))"
```

Заполнить `.env` (ключевые поля):

| Переменная | Значение |
|---|---|
| `DATABASE_URL` | `mysql+pymysql://nexus:ПАРОЛЬ@127.0.0.1:3306/barotrauma` |
| `DEBUG` | `false` (обязательно в проде — иначе не-Secure cookie, autoreload, открытый Swagger) |
| `SERVER_PORT` | `8001` (внутренний порт бэкенда; прокси всё равно переопределяет) |
| `UVICORN_WORKERS` | `1` (встроенный бот должен жить в одном процессе с веб-роутами) |
| `CORS_ORIGINS` | `["https://ВАШ_ДОМЕН"]` — **никогда не `"*"` вместе с credentials** |
| `SECRET_KEY`, `API_KEY` | сгенерированные значения выше |
| `DISCORD_CLIENT_ID/SECRET`, `DISCORD_REDIRECT_URI` | из Discord Developer Portal (шаг 9) |
| `DISCORD_BOT_TOKEN`, `GUILD_ID` | токен бота и ID сервера Discord (шаг 9) |

Остальные (`STEAM_API_KEY`, `SFTP_*`, `ONLYOFFICE_*`, `PTERODACTYL_*`) —
опциональны, можно оставить пустыми.

```bash
chmod 600 .env      # секреты — только владельцу
```

---

## 6. База данных

```bash
sudo mysql
```
```sql
CREATE DATABASE barotrauma CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'nexus'@'127.0.0.1' IDENTIFIED BY 'СИЛЬНЫЙ_ПАРОЛЬ';
GRANT ALL PRIVILEGES ON barotrauma.* TO 'nexus'@'127.0.0.1';
FLUSH PRIVILEGES;
```

Схема и все миграции **создаются автоматически** при первом старте
(`init_db()` в `database.py` вызывает `Base.metadata.create_all` +
идемпотентные миграции/индексы). Отдельного шага миграции нет — просто
запустите приложение с корректным `DATABASE_URL`.

> При **переносе** существующей БД — восстановите дамп ДО первого запуска (см.
> раздел «Перенос»).

---

## 7. Первый запуск (проверка вручную)

```bash
cd /opt/nexusdata
source venv/bin/activate
python proxy_server.py
```

Ожидаемо: прокси слушает `0.0.0.0:8000`, поднимает бэкенд на `127.0.0.1:8001`,
`init_db()` создаёт таблицы, стартует Discord-бот (если задан токен).

Проверка (в другом терминале):
```bash
curl -s http://127.0.0.1:8000/healthz    # {"status":"ok",...}
```

Остановить: `Ctrl+C`. Дальше настроим автозапуск (шаг 10).

Консоль прокси понимает команды (см. `PROXY_DEPLOYMENT.md`): `status`,
`backend on|off|restart`, `maintenance on <текст>|off`, `db check`.

---

## 8. nginx (HTTPS + правильный IP клиента)

Получите TLS-сертификат (например `certbot --nginx -d ВАШ_ДОМЕН`), затем
site-config `/etc/nginx/sites-available/nexusdata`:

```nginx
server {
    listen 443 ssl http2;
    server_name ВАШ_ДОМЕН;

    ssl_certificate     /etc/letsencrypt/live/ВАШ_ДОМЕН/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ВАШ_ДОМЕН/privkey.pem;

    client_max_body_size 64m;          # загрузка документов/реплеев

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        # ВАЖНО для безопасности: ставим реальный IP клиента ЗАМЕНОЙ, а не
        # $proxy_add_x_forwarded_for (который дописывает к присланному клиентом
        # значению) — иначе клиент сможет подделать свой IP в X-Forwarded-For,
        # что ломает rate-limit и аудит. Бэкенд доверяет этому заголовку только
        # от localhost (forwarded_allow_ips=127.0.0.1 в api_server.py).
        proxy_set_header X-Forwarded-For   $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket/SSE, если используются
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 120s;
    }
}

server {                                # HTTP → HTTPS
    listen 80;
    server_name ВАШ_ДОМЕН;
    return 301 https://$host$request_uri;
}
```

```bash
sudo ln -s /etc/nginx/sites-available/nexusdata /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

> Если разворачиваете ONLYOFFICE — добавьте `location /onlyoffice/ { ... }` по
> инструкции [`onlyoffice/NATIVE_INSTALL.md`](onlyoffice/NATIVE_INSTALL.md).

---

## 9. Discord (OAuth-вход + бот)

**Приложение и OAuth** (https://discord.com/developers/applications):
1. New Application → скопировать **Client ID** и **Client Secret** → в `.env`
   (`DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`).
2. OAuth2 → Redirects: добавить `https://ВАШ_ДОМЕН/auth/discord/callback`
   (в точности как `DISCORD_REDIRECT_URI`).

**Бот**:
3. Вкладка Bot → Add Bot → **Reset Token** → скопировать в `DISCORD_BOT_TOKEN`.
4. Включить **Privileged Gateway Intents**: `SERVER MEMBERS INTENT` и
   `MESSAGE CONTENT INTENT` (бот их использует — синк участников, пересылка).
5. Пригласить бота на сервер с правами Manage Roles / Manage Channels / Send
   Messages. `GUILD_ID` = ID вашего Discord-сервера (вкл. Developer Mode →
   правый клик по серверу → Copy Server ID).

Если `DISCORD_BOT_TOKEN` пуст — бот просто не стартует (приложение работает).

---

## 10. Автозапуск и автоподъём (systemd)

Один сервис на `proxy_server.py` — он сам держит бэкенд и перезапускает его.
Нам нужно только перезапускать **сам прокси** при падении.

`/etc/systemd/system/nexusdata.service`:
```ini
[Unit]
Description=NexusData (proxy + backend + Discord bot)
After=network.target mysql.service

[Service]
Type=simple
User=nexus
WorkingDirectory=/opt/nexusdata
ExecStart=/opt/nexusdata/venv/bin/python proxy_server.py
Restart=on-failure
RestartSec=5
# Ограничивает число арен glibc → бэкенд не раздувает RSS на простое (главная
# причина «утечки на пустом месте» у многопоточных Python-сервисов). Прокси и
# так проставляет это дочернему процессу, но задаём и здесь на всякий случай.
# Дополняется фоновой задачей malloc_trim (workers.task_memory_maintenance).
Environment=MALLOC_ARENA_MAX=2
# Прокси читает stdin-консоль; для systemd она не нужна — команды можно слать,
# запустив прокси интерактивно, либо управлять через backend on/off вручную.

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -s /usr/sbin/nologin nexus 2>/dev/null || true
sudo chown -R nexus:nexus /opt/nexusdata
sudo systemctl daemon-reload
sudo systemctl enable --now nexusdata
sudo systemctl status nexusdata
journalctl -u nexusdata -f          # логи
```

> На **Pterodactyl** вместо systemd используйте egg с `python proxy_server.py`
> как командой запуска и включённым перезапуском при падении — консоль панели
> тогда напрямую управляет прокси (`maintenance on`, `backend restart` и т.д.).
>
> ⚠️ **Частая ошибка на Pterodactyl**: если в `.env` контейнера не задан (или
> пуст) `DATABASE_URL`, приложение подхватывает дефолт из `config.py` —
> `...@localhost:3306/...`. Внутри контейнера `localhost` — это **сам
> контейнер**, а не хост с MySQL, поэтому будет `Connection refused`
> (приложение при этом не падает, а уходит в degraded-режим — см. диагностику
> ниже). Указывайте реальный адрес БД: IP хост-машины/docker-моста (часто
> `172.17.0.1`), адрес отдельного MySQL-сервиса в панели, либо `host.docker.internal`,
> в зависимости от того, как Pterodactyl настроен на вашем узле — уточните у
> хостинг-провайдера/в настройках ноды, если не уверены.

Многоуровневый автоподъём уже встроен: прокси поднимает бэкенд; бэкенд в
multi-worker режиме поднимает планировщик; Discord-бот сам переподключается с
backoff; периодический `SELECT 1` следит за БД. systemd/Pterodactyl закрывает
последний уровень — сам прокси. Подробности — в `PROXY_DEPLOYMENT.md`.

---

## 11. Игровой сервер Barotrauma (плагин)

Плагины (`MorePlugins`) обращаются к API по ключу:
- Базовый URL: `https://ВАШ_ДОМЕН`
- Ключ: значение `API_KEY` из `.env`, передаётся заголовком `X-API-Key`
  (или `?api_key=`). **IP-allowlist-обхода больше нет** (убран как небезопасный
  за прокси) — ключ обязателен.

Настройте эти значения в конфиге плагина на игровом сервере. Второй ключ
`CLIENT_API_KEY` (если задан) — для read-only внешних интеграций (состояние
мира/склад).

---

## 12. Перенос на другую машину

На **старой** машине сохраните состояние:
```bash
# 1. Дамп БД
mysqldump -u nexus -p --single-transaction --routines barotrauma > barotrauma.sql
# 2. Загруженные файлы и runtime-состояние (НЕ в git)
tar czf nexus-state.tgz uploads/ data/ .env
```

На **новой** машине (после шагов 1–6, но ДО первого запуска):
```bash
cd /opt/nexusdata
# Восстановить файлы/секреты
tar xzf nexus-state.tgz               # вернёт uploads/, data/, .env
# Восстановить БД
mysql -u nexus -p barotrauma < barotrauma.sql
```
Затем — обычный запуск (шаг 7/10). `init_db()` при старте лишь до-накатит
недостающие миграции/индексы на восстановленную БД (идемпотентно), данные не
трогает.

Что переносить обязательно: **дамп БД**, каталог **`uploads/`** (документы,
реплеи, картинки), **`.env`**, и `data/` (файлы состояния бота/runtime —
`rating_message_ids.json` и т.п., иначе бот перепостит эмбеды заново).
Не переносить: `venv/`, `__pycache__/`, `frontend/node_modules/`,
`frontend/dist/` (пересобрать на новой машине).

Не забудьте на новой машине обновить `DISCORD_REDIRECT_URI` и redirect в Discord
Developer Portal, если сменился домен.

---

## 13. Чеклист безопасности (обязательно к прочтению)

1. **Ротация секретов.** Прежний `.env` был закоммичен в git — все его секреты
   (`SECRET_KEY`, `API_KEY`, пароль БД, `DISCORD_CLIENT_SECRET`,
   `DISCORD_BOT_TOKEN`) считаются скомпрометированными и **должны быть заменены**:
   - сгенерировать новые `SECRET_KEY`/`API_KEY` (ротация `API_KEY` требует
     обновить ключ в плагине игрового сервера);
   - сменить пароль пользователя MySQL и `DATABASE_URL`;
   - Reset Client Secret и Bot Token в Discord Developer Portal.
   Плюс, при возможности, вычистить `.env` из git-истории
   (`git filter-repo`/BFG) — сам файл уже вынут из индекса.
2. **`DEBUG=false`** в проде (Secure-cookie, нет autoreload, скрыт Swagger).
3. **`CORS_ORIGINS`** — явный домен, не `"*"` (иначе любой сайт сможет слать
   аутентифицированные запросы; приложение теперь отключает credentials при `*`).
4. **nginx `X-Forwarded-For $remote_addr`** (замена, не дописывание) — иначе
   клиент подделает свой IP.
5. **`chmod 600 .env`**; бэкенд и MySQL слушают только `127.0.0.1`; наружу
   открыт лишь nginx (443). Файрвол: разрешить 80/443, закрыть 8000/8001/3306.
6. Только HTTPS (redirect с 80). Актуальный TLS-сертификат (certbot renew).
7. Регулярные бэкапы: `mysqldump` по cron + `uploads/`.

---

## 14. Обновление / редеплой

С always-up прокси обновление без «обрыва соединения» у пользователей
(подробно — `PROXY_DEPLOYMENT.md`):

```bash
# в консоли прокси (или через панель Pterodactyl):
maintenance on Обновляемся, вернёмся через пару минут
backend off
# --- на сервере ---
git pull
source venv/bin/activate && pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
# --- обратно в консоли прокси ---
backend on
maintenance off
```

Всё время между `backend off` и `backend on` пользователи видят страницу
техработ, а не ошибку соединения.

---

## 15. Диагностика

| Симптом | Что смотреть |
|---|---|
| `/healthz` не отвечает | прокси не запущен: `systemctl status nexusdata`, `journalctl -u nexusdata` |
| Страница техработ не пропадает | бэкенд не поднимается: в консоли `status`, затем `backend restart`; ищите ошибку в логах |
| `db_healthy=false` в `/healthz` | неверный `DATABASE_URL` / MySQL не запущен / нет прав у пользователя |
| В логах `Can't connect to MySQL server on 'localhost'` / `Connection refused` при старте | `DATABASE_URL` не задан в `.env` этого сервера → подхватился дефолт `localhost` из `config.py`. На Pterodactyl `localhost` — это контейнер, а не хост с БД. Пропишите реальный адрес БД в `.env` (см. предупреждение в шаге 10) — приложение при этом не падает, продолжает работать в degraded-режиме и само переподключится, как только адрес станет верным |
| `discord_bot_token not configured — Discord bot will not start` | ожидаемо, если `DISCORD_BOT_TOKEN` пуст — не ошибка, бот просто не стартует, остальное приложение работает; заполните токен (шаг 9), если бот нужен |
| Discord-вход даёт `state_mismatch` | не совпадает `DISCORD_REDIRECT_URI` или потеряна сессия — залогиньтесь заново со страницы `/login` |
| Бот оффлайн (на странице Discord-бота) | пустой/неверный `DISCORD_BOT_TOKEN`, не включены Gateway Intents |
| В аудите/логах IP всегда `127.0.0.1` | nginx не ставит `X-Forwarded-For` (см. шаг 8) |
| Игровой сервер получает 401 | плагин не шлёт `X-API-Key` или ключ не совпадает с `API_KEY` |
| SPA не грузится (пустая страница) | не собран фронтенд: `cd frontend && npm run build` |
```
