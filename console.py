"""
Interactive stdin console for a running NexusData process — type commands
into the process's console (this is exactly what Pterodactyl's "Console" tab
sends to a game server's stdin; the same mechanism works here) to toggle
API/web/workers or maintenance mode live, without restarting the process.

Started once, from api_server.py's __main__ block, BEFORE uvicorn.run() is
called — that's the one process that actually owns stdin in both
single-process and multi-worker mode (in multi-worker mode, uvicorn's own
worker subprocesses and the separate scheduler_process.py do NOT read stdin
themselves).

"api"/"web" changes take effect almost immediately: every process serving
HTTP re-reads runtime_control's state file on every request (with a ~1s
cache — see runtime_control.read_state()). "workers" changes are picked up
by workers.py's watch_runtime_state() watcher thread, wherever the workers
actually live (in-process, or the dedicated scheduler_process.py in
multi-worker mode) — this module never touches the scheduler directly, it
only ever writes the shared state file, so the same command works
identically regardless of deployment mode.
"""

import logging
import threading
import time

import runtime_control

logger = logging.getLogger(__name__)

# Comfortably longer than workers.py's watch_runtime_state() default 5s poll
# interval, so a "workers restart" is guaranteed to be observed as an actual
# off-then-on transition instead of possibly never being seen at all if it
# happened to land between two polls.
_WORKERS_RESTART_WAIT_SECONDS = 7.0

_HELP = """
Доступные команды:
  status                       — текущее состояние подсистем
  api on|off|restart           — вкл/выкл/перезапуск API (/api/*)
  web on|off|restart           — вкл/выкл/перезапуск веб-морды (сайта/SPA)
  workers on|off|restart       — вкл/выкл/перезапуск фоновых воркеров
  maintenance on [текст]|off   — вкл/выкл режим технических работ
  db check                     — принудительно проверить подключение к БД
  help                         — эта справка
""".strip()


def _print_status():
    s = runtime_control.read_state(force=True)
    print(
        f"api={'on' if s.api_enabled else 'OFF'}  "
        f"web={'on' if s.web_enabled else 'OFF'}  "
        f"workers={'on' if s.workers_enabled else 'OFF'}  "
        f"db={'ok' if s.db_healthy else 'DOWN'}  "
        f"maintenance={('ON — ' + s.maintenance_message) if s.maintenance_mode else 'off'}"
    )


def _db_check():
    try:
        from database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        runtime_control.set_db_healthy(True)
        print("БД доступна.")
    except Exception as e:
        runtime_control.set_db_healthy(False)
        print(f"БД недоступна: {e}")


def _toggle(flag: str, label: str, action: str):
    if action == "on":
        runtime_control.write_state(**{flag: True})
        print(f"{label}: включено")
    elif action == "off":
        runtime_control.write_state(**{flag: False})
        print(f"{label}: отключено")
    elif action == "restart":
        runtime_control.write_state(**{flag: False})
        print(f"{label}: перезапуск…")
        time.sleep(1.0)
        runtime_control.write_state(**{flag: True})
        print(f"{label}: снова включено")
    else:
        print(f"Использование: {label} on|off|restart")


def _workers_cmd(action: str):
    if action == "on":
        runtime_control.write_state(workers_enabled=True)
        print("workers: включаются (в течение нескольких секунд)")
    elif action == "off":
        runtime_control.write_state(workers_enabled=False)
        print("workers: останавливаются (в течение нескольких секунд)")
    elif action == "restart":
        runtime_control.write_state(workers_enabled=False)
        print(f"workers: остановка… (~{int(_WORKERS_RESTART_WAIT_SECONDS)} сек)")
        time.sleep(_WORKERS_RESTART_WAIT_SECONDS)
        runtime_control.write_state(workers_enabled=True)
        print("workers: снова запускаются")
    else:
        print("Использование: workers on|off|restart")


def _maintenance_cmd(rest: str):
    parts = rest.strip().split(maxsplit=1)
    action = parts[0].lower() if parts else ""
    if action == "off":
        runtime_control.write_state(maintenance_mode=False, maintenance_message="")
        print("Режим техработ выключен.")
    elif action == "on":
        message = parts[1] if len(parts) > 1 else ""
        runtime_control.write_state(maintenance_mode=True, maintenance_message=message)
        print("Режим техработ включён." + (f" Сообщение: {message}" if message else ""))
    else:
        print("Использование: maintenance on [текст]|off")


def _handle(line: str):
    parts = line.strip().split(maxsplit=1)
    if not parts:
        return
    cmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if cmd in ("help", "?"):
        print(_HELP)
    elif cmd == "status":
        _print_status()
    elif cmd in ("api", "web"):
        _toggle(f"{cmd}_enabled", cmd, rest.strip().lower())
    elif cmd == "workers":
        _workers_cmd(rest.strip().lower())
    elif cmd == "maintenance":
        _maintenance_cmd(rest)
    elif cmd == "db" and rest.strip().lower() == "check":
        _db_check()
    else:
        print(f"Неизвестная команда: {line!r}. 'help' — список команд.")


def start_console_thread():
    """Reads commands from stdin in a loop until EOF. Safe to call even when
    stdin isn't interactive (no console attached) — input() just raises
    EOFError right away and the thread exits quietly instead of spinning."""

    def _loop():
        print("NexusData: консоль управления готова. 'help' — список команд.")
        while True:
            try:
                line = input()
            except EOFError:
                logger.info("[Console] stdin closed — console thread exiting")
                return
            except Exception:
                logger.exception("[Console] Unexpected error reading stdin")
                return
            try:
                _handle(line)
            except Exception:
                logger.exception(f"[Console] Command failed: {line!r}")
                print("Ошибка выполнения команды — см. логи.")

    threading.Thread(target=_loop, daemon=True, name="AdminConsole").start()
