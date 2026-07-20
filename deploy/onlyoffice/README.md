# ONLYOFFICE Document Server — развёртывание

Здесь запускается движок редактора, который теперь встроен в раздел
«Документы». Это отдельный сервис от основного приложения NexusData —
разверните его один раз на том же VPS и укажите на него в конфиге NexusData.

Три способа развернуть, на выбор:

- **[NATIVE_INSTALL.md](./NATIVE_INSTALL.md)** — нативная установка через apt
  прямо на VDS, без Docker (рекомендуется для этого проекта).
- **Этот файл (ниже)** — через Docker Compose.
- **[PTERODACTYL.md](./PTERODACTYL.md)** — через кастомный egg на панели
  Pterodactyl.

## 1. Сгенерировать JWT-секрет

```bash
openssl rand -hex 32
```

Скопируйте `.env.example` в `.env` в этой же папке и вставьте сгенерированное
значение в `ONLYOFFICE_JWT_SECRET`. Это **точно такое же значение** нужно
также задать на бэкенде NexusData (см. шаг 4) — обе стороны подписывают/
проверяют запросы им, поэтому несовпадение означает, что каждая загрузка
редактора и каждое сохранение будут тихо проваливаться.

## 2. Запустить контейнер

```bash
cd deploy/onlyoffice
docker compose up -d
```

Подождите ~30–60 секунд на первый запуск (при первом старте он инициализирует
свои внутренние Postgres/Redis/RabbitMQ), затем проверьте:

```bash
curl http://localhost:8082/healthcheck
# → true
```

## 3. Mixed content / HTTPS — прочитайте перед тем, как продолжить

Браузер сам загружает `api.js` с Document Server и открывает `<iframe>`,
указывающий на него. Если NexusData отдаётся по HTTPS (почти наверняка так и
есть), обычный `http://`-адрес Document Server будет **заблокирован
браузером как mixed content**, и редактор никогда не загрузится. Выберите
один из вариантов:

- **(a) Прокси по пути через существующий HTTPS-листенер** (рекомендуется —
  не нужен новый открытый порт и отдельный сертификат): добавьте location-блок
  туда, где уже терминируется TLS для NexusData (nginx/Caddy/др.), проксируя
  путь, например `https://nd.rp-asper.ru/onlyoffice/` →
  `http://127.0.0.1:8082/`. Пример для nginx:

  ```nginx
  location /onlyoffice/ {
      proxy_pass http://127.0.0.1:8082/;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
      client_max_body_size 100m;
  }
  ```

  Затем задайте `onlyoffice_document_server_url = "https://nd.rp-asper.ru/onlyoffice"`.

- **(b) Терминировать TLS прямо на порту контейнера**: смонтируйте сертификат/
  ключ вашего домена в контейнер и включите SSL в его конфиге (см.
  документацию ONLYOFFICE по переменным `SSL_CERTIFICATE_PATH`/
  `SSL_KEY_PATH`), затем укажите `onlyoffice_document_server_url` как
  `https://nd.rp-asper.ru:8082`.

Я не вижу вашу реальную конфигурацию реверс-прокси отсюда, так что выбирайте
вариант под свою инфраструктуру — (a) проще, если у вас уже есть nginx/Caddy
перед приложением.

## 4. Настроить NexusData

В собственном `.env` / окружении NexusData:

```
ONLYOFFICE_JWT_SECRET=<то же значение, что и в шаге 1>
```

И в `config.py` (или как переменные окружения с этими же именами полей):

```python
onlyoffice_document_server_url = "https://nd.rp-asper.ru/onlyoffice"  # из шага 3
onlyoffice_callback_base_url = "https://nd.rp-asper.ru"               # собственный базовый URL NexusData — сюда стучится Document Server
```

После задания этих значений перезапустите бэкенд NexusData.

## 5. Проверить работу целиком

1. Откройте любой документ в разделе «Документы».
2. Вместо старой страницы должен загрузиться редактор ONLYOFFICE.
3. Наберите что-нибудь, подождите несколько секунд (или закройте документ),
   чтобы запустилось сохранение — проверьте, что в диалоге «История версий»
   документа появилась новая запись.
4. Если редактор не загружается: проверьте консоль браузера на ошибку
   mixed content или CORS (значит, дело в шаге 3), и проверьте логи
   NexusData на 401 по `/api/v1/onlyoffice/callback/{id}` (значит,
   не совпадают JWT-секреты из шага 1 и шага 4).
