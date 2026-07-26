# ONLYOFFICE Document Server — нативная установка на Ubuntu/Debian (без Docker)

Ставим `onlyoffice-documentserver` из официального apt-репозитория прямо на
VDS, на том же сервере, где уже крутится nginx для NexusData
(`nd.rp-asper.ru`). Чтобы два nginx (системный + тот, что ставит пакет
ONLYOFFICE) не подрались за порты 80/443, Document Server слушает только
`127.0.0.1` на отдельном локальном порту, а наружу отдаётся через ваш уже
существующий nginx по пути `/onlyoffice/`.

## 1. Подключить репозиторий ONLYOFFICE

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates wget gnupg apt-transport-https

sudo mkdir -p /usr/share/keyrings
wget -qO - https://download.onlyoffice.com/GPG-KEY-ONLYOFFICE | sudo gpg --dearmor -o /usr/share/keyrings/onlyoffice.gpg
echo "deb [signed-by=/usr/share/keyrings/onlyoffice.gpg] https://download.onlyoffice.com/repo/debian squeeze main" | sudo tee /etc/apt/sources.list.d/onlyoffice.list
sudo apt-get update
```

**Известная проблема**: если `wget` тихо недокачает ключ (обрыв сети, редирект и
т.п.), `gpg --dearmor` на пустом/битом вводе не выдаст ошибку — просто создаст
пустой `onlyoffice.gpg`. Дальше `apt-get update` упадёт на этом же шаге с
`NO_PUBKEY 8320CA65CB2DE8E5`, а `apt-get install onlyoffice-documentserver` —
с `Unable to locate package` (репозиторий не читается без подписи). Проверьте
перед тем как продолжать:

```bash
gpg --show-keys --keyid-format long /usr/share/keyrings/onlyoffice.gpg
# должен показать 8320CA65CB2DE8E5 — если файл пуст/команда ничего не вывела,
# получите ключ надёжнее — напрямую с keyserver по ID, а не по URL:
sudo rm -f /usr/share/keyrings/onlyoffice.gpg
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/onlyoffice.gpg \
  --keyserver keyserver.ubuntu.com --recv-keys 8320CA65CB2DE8E5
# если keyserver.ubuntu.com недоступен (хостинг режет порт) — запасной:
#   --keyserver hkps://keys.openpgp.org --recv-keys 8320CA65CB2DE8E5
```

## 2. Установить пакет

```bash
sudo apt-get install -y onlyoffice-documentserver
```

Пакет сам подтянет и настроит то, что раньше было «внутри контейнера» —
локальный PostgreSQL, RabbitMQ, Redis — как системные сервисы, плюс
supervisor, который управляет процессами docservice/converter/и т.д. Если
на сервере ещё нет nginx, поставит его тоже; раз он у вас уже есть под
NexusData — apt просто переиспользует установленный пакет и добавит
собственный конфиг сайта.

Установка может занять несколько минут и в конце сама запустит сервисы —
пока трогать ничего не нужно.

## 3. Убрать конфликт портов с основным nginx

Пакет кладёт конфиг сайта Document Server сюда:

```
/etc/onlyoffice/documentserver/nginx/ds.conf
```

Перед тем как выбирать порт — **проверьте, что он ещё ничем не занят**,
особенно самим NexusData (у него часто уже есть внутренний
`proxy_pass http://127.0.0.1:XXXX/;` в конфиге сайта на порт, где живёт сам
бэкенд):

```bash
sudo grep -rn "808[0-9]" /etc/nginx/sites-enabled/
sudo ss -tlnp | grep 808
```

Ниже используется `8083` как пример — если он тоже занят, возьмите любой
свободный (`8090`, что угодно), просто подставьте везде дальше вместо 8083.

Откройте `ds.conf` и найдите строки вида:

```nginx
listen       0.0.0.0:80;
```

(может быть ещё отдельный `listen 0.0.0.0:443 ssl;` в соседнем файле —
проверьте `/etc/onlyoffice/documentserver/nginx/` целиком). Замените порт 80
на выбранный локальный порт и уберите/закомментируйте SSL-блок — TLS будет
терминировать ваш основной nginx:

```nginx
listen       127.0.0.1:8083;
```

Проверьте и перезапустите nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Так Document Server больше не претендует на 80/443 и не виден снаружи
напрямую — только через прокси на шаге 4.

## 4. Прокинуть путь через основной nginx NexusData

В конфиге сайта `nd.rp-asper.ru` (там же, где уже настроен TLS-сертификат)
добавьте location внутри существующего `server { listen 443 ssl; ... }`:

```nginx
location /onlyoffice/ {
    proxy_pass http://127.0.0.1:8083/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    client_max_body_size 100m;
}
```

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 5. Настроить JWT

Секрет прописывается в `/etc/onlyoffice/documentserver/local.json` (файл уже
существует после установки, JSON нужно аккуратно смержить, а не
перезаписать целиком). Проще и безопаснее сделать это скриптом, чем руками:

```bash
sudo python3 - "$(openssl rand -hex 32)" << 'PYEOF'
import json, sys

secret = sys.argv[1]
path = "/etc/onlyoffice/documentserver/local.json"

try:
    with open(path) as f:
        data = json.load(f)
except FileNotFoundError:
    data = {}

data.setdefault("services", {}).setdefault("CoAuthoring", {})
data["services"]["CoAuthoring"]["token"] = {
    "enable": {"browser": True, "request": {"inbox": True, "outbox": True}},
    "inbox": {"header": "Authorization"},
    "outbox": {"header": "Authorization"},
}
data["services"]["CoAuthoring"]["secret"] = {
    "inbox": {"string": secret},
    "outbox": {"string": secret},
    "session": {"string": secret},
}

with open(path, "w") as f:
    json.dump(data, f, indent=2)

print("Secret written. SAVE THIS VALUE for NexusData's config:")
print(secret)
PYEOF
```

Сохраните секрет, который скрипт выведет в конце — он понадобится в шаге 7.

Перезапустите сервисы Document Server, чтобы подхватить новый конфиг:

```bash
sudo supervisorctl restart all
```

## 6. Проверить

```bash
# Локально, напрямую
curl http://127.0.0.1:8083/healthcheck
# → true

# Через основной nginx и домен
curl https://nd.rp-asper.ru/onlyoffice/healthcheck
# → true
```

Если второй curl не проходит, а первый проходит — проблема в шаге 4
(конфиг основного nginx). Если не проходит даже первый — смотрите
`sudo supervisorctl status` и логи в `/var/log/onlyoffice/documentserver/`.

## 7. Настроить NexusData

В `.env`/окружении NexusData:

```
ONLYOFFICE_JWT_SECRET=<секрет из шага 5>
```

В `config.py` (или как переменные окружения с этими же именами):

```python
onlyoffice_document_server_url = "https://nd.rp-asper.ru/onlyoffice"
onlyoffice_callback_base_url = "https://nd.rp-asper.ru"
```

Перезапустите бэкенд NexusData после этого.

## 8. Проверка целиком

1. Откройте любой документ в разделе «Документы».
2. Должен загрузиться редактор ONLYOFFICE вместо старой страницы.
3. Наберите текст, подождите несколько секунд или закройте документ —
   в «Истории версий» документа должна появиться новая запись.
4. Не грузится — смотрите консоль браузера (mixed content/CORS → шаг 4) и
   логи NexusData на 401 у `/api/v1/onlyoffice/callback/{id}` (несовпадение
   секрета между шагом 5 и шагом 7).

## Обслуживание

- Логи: `/var/log/onlyoffice/documentserver/`.
- Статус процессов: `sudo supervisorctl status`.
- Обновление: `sudo apt-get update && sudo apt-get upgrade onlyoffice-documentserver`
  (конфиг из шага 3 при обновлении пакета иногда перезаписывается —
  проверяйте `ds.conf` после апдейта).
