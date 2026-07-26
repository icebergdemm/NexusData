#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# Автодеплой NexusData.
#
# Запускается НА СЕРВЕРЕ — из GitHub Actions по SSH (см. .github/workflows/
# deploy.yml) или вручную: `bash deploy/deploy.sh`.
#
# Должен запускаться от имени сервисного пользователя, владеющего каталогом
# приложения и файлом data/runtime_state.json (тот же, под которым работает
# systemd-сервис nexusdata) — тогда sudo не нужен вообще.
#
# Что делает:
#   1. git reset --hard origin/main  (untracked .env/uploads/data/venv целы)
#   2. pip install    — только если менялся requirements.txt
#   3. npm run build  — только если менялся frontend/
#   4. backend off→on через runtime_control — прокси остаётся жив и показывает
#      страницу техработ, пока бэкенд поднимается с новым кодом (без sudo,
#      без рестарта самого прокси).
#   5. Ждёт healthz=ok.
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/nexusdata}"
BRANCH="${DEPLOY_BRANCH:-main}"
PORT="${PROXY_PORT:-8000}"
PY="${APP_DIR}/venv/bin/python"
PIP="${APP_DIR}/venv/bin/pip"

log() { echo "[deploy $(date '+%F %T')] $*"; }

cd "$APP_DIR"

# ── 1. Забрать новый код ───────────────────────────────────────────────────
before="$(git rev-parse HEAD)"
log "Текущий коммит: ${before:0:8}"
git fetch --prune origin
git reset --hard "origin/${BRANCH}"
after="$(git rev-parse HEAD)"

if [ "$before" = "$after" ]; then
  log "Изменений нет — деплой не требуется."
  exit 0
fi
log "Обновление до ${after:0:8}"
changed="$(git diff --name-only "$before" "$after")"

# ── 2. Python-зависимости (только при изменении requirements.txt) ──────────
if echo "$changed" | grep -qx 'requirements.txt'; then
  log "requirements.txt изменился → обновляю зависимости"
  "$PIP" install --upgrade -r requirements.txt
fi

# ── 3. Сборка фронтенда (только при изменениях во frontend/) ───────────────
if echo "$changed" | grep -q '^frontend/'; then
  log "frontend/ изменился → пересобираю SPA"
  ( cd frontend && npm ci && npm run build )
fi

# ── 4. Перезапуск бэкенда с новым кодом ────────────────────────────────────
# Пишем backend_enabled=false → watcher прокси останавливает старый процесс
# (и отдаёт страницу техработ), затем true → поднимает новый с новым кодом.
# runtime_control пишет в data/runtime_state.json относительно модуля, т.е. в
# тот же файл, что читает прокси, независимо от cwd.
log "Перезапуск бэкенда (backend off → on)"
"$PY" - <<'PY'
import time
import runtime_control
runtime_control.write_state(backend_enabled=False)
time.sleep(4)   # дать watcher прокси (~2с цикл) остановить старый процесс
runtime_control.write_state(backend_enabled=True)
PY

# ── 5. Дождаться готовности (healthz через публичный прокси) ────────────────
log "Ожидаю healthz=ok..."
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" 2>/dev/null | grep -q '"status": *"ok"'; then
    log "✅ Бэкенд поднялся с новым кодом (${after:0:8}). Деплой завершён."
    exit 0
  fi
  sleep 2
done

log "⚠️ healthz не стал ok за ~60с. Проверьте логи: journalctl -u nexusdata -n 100"
exit 1
