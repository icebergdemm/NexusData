# Автодеплой: push в GitHub → автоматически на сервер

Каждый `git push` в ветку `main` автоматически выкатывается на сервер:
GitHub Actions заходит по SSH и запускает [`deploy.sh`](deploy.sh), который
подтягивает код, при необходимости пересобирает зависимости/фронтенд и
перезапускает бэкенд с новым кодом — прокси при этом остаётся жив и показывает
страницу техработ на пару секунд, пока бэкенд поднимается (без простоя
публичного порта, без `sudo`).

```
git push  ──►  GitHub Actions  ──SSH──►  сервер: deploy/deploy.sh
                                          ├─ git reset --hard origin/main
                                          ├─ pip install   (если менялся requirements.txt)
                                          ├─ npm run build (если менялся frontend/)
                                          └─ backend off→on (новый код) → ждём healthz=ok
```

Компоненты (все уже в репозитории):
- [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) — триггер по push.
- [`deploy/deploy.sh`](deploy.sh) — сам деплой, запускается на сервере.

---

## Разовая настройка

### 1. На сервере: дать сервисному пользователю возможность заходить по SSH

Деплой должен идти от имени пользователя, которому принадлежит `/opt/nexusdata`
и под которым работает сервис (в инструкции это `nexus`). При установке он был
создан с `nologin` — дадим ему оболочку и каталог `.ssh`:

```bash
sudo usermod -s /bin/bash nexus
sudo -u nexus mkdir -p /home/nexus/.ssh
sudo -u nexus chmod 700 /home/nexus/.ssh
# если у nexus нет домашнего каталога:
sudo mkhomedir_helper nexus 2>/dev/null || sudo mkdir -p /home/nexus && sudo chown nexus:nexus /home/nexus
```

> Sudo для деплоя не нужен: перезапуск бэкенда идёт через `runtime_control`
> (файл состояния), а не через `systemctl`. Пользователь `nexus` уже владеет
> всеми нужными файлами (код, `venv/`, `data/runtime_state.json`).

### 2. На своей машине: сгенерировать отдельный SSH-ключ для деплоя

Не используйте свой личный ключ — заведите выделенный только под деплой:

```bash
ssh-keygen -t ed25519 -f nexus_deploy_key -N "" -C "github-actions-deploy"
# создаст nexus_deploy_key (приватный) и nexus_deploy_key.pub (публичный)
```

### 3. Публичный ключ — на сервер

```bash
# содержимое nexus_deploy_key.pub добавить в authorized_keys пользователя nexus
cat nexus_deploy_key.pub | ssh ВАШ_ЮЗЕР@СЕРВЕР "sudo tee -a /home/nexus/.ssh/authorized_keys && sudo chown nexus:nexus /home/nexus/.ssh/authorized_keys && sudo chmod 600 /home/nexus/.ssh/authorized_keys"
```

Проверьте, что заходит:
```bash
ssh -i nexus_deploy_key nexus@СЕРВЕР "cd /opt/nexusdata && git rev-parse --short HEAD"
```

### 4. Приватный ключ и параметры — в GitHub Secrets

Репозиторий на GitHub → **Settings → Secrets and variables → Actions → New
repository secret**. Добавьте:

| Secret | Значение |
|---|---|
| `DEPLOY_SSH_KEY` | всё содержимое файла `nexus_deploy_key` (приватный ключ, с `-----BEGIN...` до `...END-----`) |
| `DEPLOY_HOST` | IP или домен сервера |
| `DEPLOY_USER` | `nexus` |
| `DEPLOY_PORT` | порт SSH (можно не добавлять — по умолчанию `22`) |

После этого приватный ключ можно удалить со своей машины — он больше не нужен
локально (`rm nexus_deploy_key nexus_deploy_key.pub`).

### 5. Закоммитить workflow и скрипт

```bash
git add .github/workflows/deploy.yml deploy/deploy.sh deploy/AUTODEPLOY.md
git commit -m "Автодеплой: GitHub Actions + deploy.sh"
git push
```

Этот же push уже запустит первый автодеплой. Откройте вкладку **Actions** на
GitHub и наблюдайте за логами джобы «Deploy to production».

---

## Как пользоваться дальше

Просто работайте как обычно:
```bash
# ...правки...
git commit -am "..."
git push          # ← через ~1–2 минуты изменения уже на проде
```

Запустить деплой вручную (без нового коммита): вкладка **Actions → Deploy to
production → Run workflow**.

## Проверка и откат

- **Лог деплоя**: вкладка Actions на GitHub (по шагам, с выводом `deploy.sh`).
- **Логи сервера**: `journalctl -u nexusdata -f`.
- **Откат** на предыдущую версию: `git revert` + push (запустит деплой отката),
  либо на сервере вручную `git reset --hard <старый_коммит> && bash deploy/deploy.sh`.

## Заметки по безопасности

- Ключ `DEPLOY_SSH_KEY` — выделенный, используется только Actions; при
  компрометации просто удалите его строку из `authorized_keys` на сервере.
- Включите защиту ветки `main` (Settings → Branches) — чтобы деплоил только
  проверенный код.
- Секреты GitHub Actions не видны в логах и в PR из форков.
- Приватный ключ храните только в GitHub Secrets, не коммитьте в репозиторий.
- (Опционально, жёстче) в `authorized_keys` можно ограничить ключ форс-командой
  `command="cd /opt/nexusdata && bash deploy/deploy.sh",no-port-forwarding,no-pty`
  — тогда этим ключом нельзя выполнить ничего, кроме деплоя.
