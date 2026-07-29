"""
Обновление кода на сервере из git — то, что раньше делалось руками по SSH.

Вызывается консольной командой `update` (console.py). Консоль читает stdin
процесса — это вкладка «Консоль» в Pterodactyl, доступная только тому, у кого
есть доступ к панели, поэтому отдельной авторизации здесь нет и HTTP-эндпоинта
специально не заводится: выполнение произвольного кода из веба — совсем другой
уровень риска, чем правка настроек.

Порядок намеренно такой:
  1. git pull --ff-only — только перемотка вперёд. Merge-коммит или конфликт на
     боевом сервере разрешать некому, поэтому расхождение веток должно падать
     с ошибкой, а не создавать состояние, из которого не выбраться.
  2. Пересборка фронтенда — ТОЛЬКО если менялось что-то во frontend/. Сборка
     занимает минуты и жрёт память, гонять её на каждый бэкенд-фикс незачем.
     Зато при изменениях она обязательна: dist/ в репозитории нет.
  3. Перезапуск backend — по флагу. Python-код перечитывается только при
     рестарте, так что без него обновление бэкенда не вступит в силу.

Настройки живут в .env и правятся на странице «Администрирование».
"""
import logging
import os
import subprocess
from typing import Callable, List, Optional, Tuple

from config import get_settings

logger = logging.getLogger(__name__)

# Пул времени на шаг. git pull по медленной сети и npm ci на слабой машине
# легально идут долго, но висеть вечно тоже нельзя — консоль в это время
# ничего не отвечает.
_GIT_TIMEOUT = 300
_BUILD_TIMEOUT = 900


def _run(cmd: List[str], cwd: str, timeout: int) -> Tuple[int, str]:
    """Запускает команду, возвращает (код возврата, объединённый вывод)."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        return proc.returncode, (proc.stdout or "").strip()
    except FileNotFoundError:
        return 127, f"Команда не найдена: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"Превышено время ожидания ({timeout} с): {' '.join(cmd)}"


def repo_dir() -> str:
    """Каталог репозитория. Пусто в настройках — сам каталог приложения."""
    configured = (get_settings().git_update_repo_dir or "").strip()
    return configured or os.path.dirname(os.path.abspath(__file__))


def check_repo(path: str) -> Optional[str]:
    """Текст ошибки, если каталог не годится как репозиторий, иначе None."""
    if not os.path.isdir(path):
        return f"Каталог не найден: {path}"
    if not os.path.exists(os.path.join(path, ".git")):
        return f"В каталоге нет репозитория git: {path}"
    return None


def current_revision(path: str) -> str:
    code, out = _run(["git", "rev-parse", "--short", "HEAD"], path, 30)
    if code != 0:
        return "?"
    rev = out.strip()
    code, out = _run(["git", "log", "-1", "--pretty=%s"], path, 30)
    return f"{rev} — {out.strip()}" if code == 0 else rev


def status_text() -> str:
    settings = get_settings()
    path = repo_dir()
    error = check_repo(path)
    lines = [
        f"Каталог:          {path}",
        f"Remote / ветка:   {settings.git_update_remote or 'origin'} / "
        f"{settings.git_update_branch or '(текущая)'}",
        f"Сборка фронтенда: {'да' if settings.git_update_build_frontend else 'нет'}",
        f"Перезапуск после: {'да' if settings.git_update_restart_backend else 'нет'}",
    ]
    if error:
        lines.append(f"ОШИБКА: {error}")
    else:
        code, branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], path, 30)
        lines.append(f"Текущая ветка:    {branch if code == 0 else '?'}")
        lines.append(f"Текущая ревизия:  {current_revision(path)}")
    return "\n".join(lines)


def run_update(emit: Callable[[str], None], force_restart: Optional[bool] = None) -> bool:
    """Выполняет обновление, построчно отдавая ход работы в `emit`.

    Возвращает True, если обновление прошло без ошибок. `force_restart`
    перекрывает настройку перезапуска (для `update restart` / `update norestart`).
    """
    settings = get_settings()
    path = repo_dir()

    error = check_repo(path)
    if error:
        emit(f"✗ {error}")
        emit("  Укажите каталог репозитория на странице «Администрирование».")
        return False

    emit(f"Каталог: {path}")
    emit(f"Было:    {current_revision(path)}")

    # Незакоммиченные правки на боевом сервере — почти всегда чья-то ручная
    # горячая правка. Перезаписать её молча нельзя, но и мешать обновлению
    # она не должна, поэтому предупреждаем и идём дальше: --ff-only всё равно
    # откажется трогать файлы, которые затрагивает обновление.
    code, dirty = _run(["git", "status", "--porcelain"], path, 60)
    if code == 0 and dirty:
        changed = [ln for ln in dirty.splitlines() if ln.strip()]
        emit(f"! В рабочем каталоге есть незакоммиченные изменения ({len(changed)} файлов):")
        for line in changed[:10]:
            emit(f"    {line}")
        if len(changed) > 10:
            emit(f"    … и ещё {len(changed) - 10}")

    code, before = _run(["git", "rev-parse", "HEAD"], path, 30)
    if code != 0:
        emit(f"✗ Не удалось прочитать текущую ревизию: {before}")
        return False

    remote = (settings.git_update_remote or "origin").strip()
    branch = (settings.git_update_branch or "").strip()
    pull_cmd = ["git", "pull", "--ff-only", remote] + ([branch] if branch else [])
    emit(f"→ {' '.join(pull_cmd)}")
    code, out = _run(pull_cmd, path, _GIT_TIMEOUT)
    for line in out.splitlines():
        emit(f"    {line}")
    if code != 0:
        emit("✗ git pull не прошёл. Если ветки разошлись, разберитесь вручную —")
        emit("  автоматический merge на боевом сервере намеренно не делается.")
        return False

    code, after = _run(["git", "rev-parse", "HEAD"], path, 30)
    if code == 0 and after.strip() == before.strip():
        emit("Обновлений нет — код уже актуален.")
        return True

    emit(f"Стало:   {current_revision(path)}")

    # Что именно поменялось — по этому решаем, нужна ли пересборка фронтенда.
    code, changed_files = _run(
        ["git", "diff", "--name-only", f"{before.strip()}..{after.strip()}"], path, 60
    )
    frontend_touched = code == 0 and any(
        line.startswith("frontend/") for line in changed_files.splitlines()
    )

    if frontend_touched and settings.git_update_build_frontend:
        frontend_dir = os.path.join(path, "frontend")
        if not os.path.isdir(frontend_dir):
            emit("! Менялся frontend/, но каталога нет — сборка пропущена.")
        else:
            npm = "npm.cmd" if os.name == "nt" else "npm"
            for cmd, label in ((["ci"], "установка зависимостей"), (["run", "build"], "сборка")):
                emit(f"→ npm {' '.join(cmd)} ({label}, это надолго)…")
                code, out = _run([npm] + cmd, frontend_dir, _BUILD_TIMEOUT)
                if code != 0:
                    for line in out.splitlines()[-20:]:
                        emit(f"    {line}")
                    emit(f"✗ npm {' '.join(cmd)} завершилась с ошибкой — фронтенд остался старым.")
                    return False
            emit("✓ Фронтенд пересобран.")
    elif frontend_touched:
        emit("! Менялся frontend/, но пересборка отключена в настройках —")
        emit("  сайт продолжит отдавать старую сборку.")

    restart = settings.git_update_restart_backend if force_restart is None else force_restart
    if restart:
        emit("→ Перезапуск backend…")
        _restart_backend(emit)
    else:
        emit("! Backend не перезапущен — изменения в Python-коде вступят в силу")
        emit("  после команды `backend restart`.")

    emit("✓ Обновление завершено.")
    return True


def _restart_backend(emit: Callable[[str], None]) -> None:
    """Тот же механизм, что у консольной команды `backend restart`: гасим
    флаг, ждём, пока прокси убьёт процесс, поднимаем обратно."""
    import time

    import runtime_control
    from console import _BACKEND_RESTART_WAIT_SECONDS

    runtime_control.write_state(backend_enabled=False)
    emit(f"    остановка… (~{int(_BACKEND_RESTART_WAIT_SECONDS)} с)")
    time.sleep(_BACKEND_RESTART_WAIT_SECONDS)
    runtime_control.write_state(backend_enabled=True)
    emit("    backend снова запускается")
