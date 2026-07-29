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
import base64
import logging
import os
import re
import subprocess
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from config import get_settings

logger = logging.getLogger(__name__)

# Пул времени на шаг. git pull по медленной сети и npm ci на слабой машине
# легально идут долго, но висеть вечно тоже нельзя — консоль в это время
# ничего не отвечает.
_GIT_TIMEOUT = 300
_BUILD_TIMEOUT = 900

# Вместо токена в выводе консоли.
_MASK = "***"


def _redact(text: str) -> str:
    """Убирает из текста токен во всех видах, в которых он мог бы всплыть.

    Вывод команд уходит в консоль Pterodactyl, а она пишется в лог файлом на
    диске: даже маловероятная утечка токена в тексте ошибки git означала бы
    ключ от репозитория в логах."""
    token = (get_settings().git_update_token or "").strip()
    if not token:
        return text
    creds = base64.b64encode(f"{_token_username()}:{token}".encode()).decode()
    for secret in (token, creds):
        if secret:
            text = text.replace(secret, _MASK)
    # Токен, случайно оказавшийся в URL (например, если его вписали прямо в
    # remote руками до появления этой настройки).
    return re.sub(r"(https?://)[^/\s@]+@", r"\1" + _MASK + "@", text)


def _token_username() -> str:
    return (get_settings().git_update_token_username or "").strip() or "x-access-token"


def _run(cmd: List[str], cwd: str, timeout: int, env: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    """Запускает команду, возвращает (код возврата, объединённый вывод)."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            env=env,
        )
        return proc.returncode, _redact((proc.stdout or "").strip())
    except FileNotFoundError:
        return 127, f"Команда не найдена: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"Превышено время ожидания ({timeout} с): {_redact(' '.join(cmd))}"


# ── Доступ к приватному репозиторию ──────────────────────────────────────────

def remote_url(path: str, remote: str) -> str:
    """URL remote'а или пустая строка, если его нет."""
    code, out = _run(["git", "remote", "get-url", remote], path, 30, env=_base_env())
    return out.strip() if code == 0 else ""


def _base_env() -> Dict[str, str]:
    """Окружение для git без авторизации.

    GIT_TERMINAL_PROMPT=0 обязателен: без него git на приватном репозитории
    пытается СПРОСИТЬ логин и пароль, а спрашивать его в консоли Pterodactyl
    некого — команда просто висела бы до таймаута в 5 минут и падала без
    внятной причины. С этим флагом git сразу отвечает «terminal prompts
    disabled», и в консоли видно, что дело в доступе."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.pop("GIT_ASKPASS", None)
    env.pop("SSH_ASKPASS", None)
    return env


def _git_supports_config_env(path: str) -> bool:
    """Умеет ли git читать -c-настройки из переменных окружения (>= 2.31)."""
    code, out = _run(["git", "--version"], path, 30)
    if code != 0:
        return False
    match = re.search(r"(\d+)\.(\d+)", out)
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= (2, 31)


def _auth_config(url: str) -> Optional[Tuple[str, str]]:
    """(ключ, значение) настройки git с заголовком авторизации — или None,
    если токен не нужен/не применим.

    Заголовок привязывается к КОНКРЕТНОМУ хосту remote'а: `http.<url>.
    extraHeader` действует только для запросов по этому префиксу, поэтому при
    редиректе на чужой домен токен туда не уедет."""
    token = (get_settings().git_update_token or "").strip()
    if not token or not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        # ssh-remote (git@host:owner/repo) авторизуется ключом, а не токеном.
        return None
    creds = base64.b64encode(f"{_token_username()}:{token}".encode()).decode()
    scope = f"{parts.scheme}://{parts.hostname}/"
    return f"http.{scope}.extraHeader", f"Authorization: Basic {creds}"


def _git_auth(path: str, url: str) -> Tuple[List[str], Dict[str, str]]:
    """Префикс аргументов и окружение для git-команды, ходящей в сеть.

    Токен предпочтительно передаётся ЧЕРЕЗ ОКРУЖЕНИЕ (GIT_CONFIG_*): аргументы
    командной строки видны в `ps` любому процессу контейнера, окружение —
    только самому процессу и root. На git старше 2.31 такого механизма нет,
    там остаётся `-c`."""
    config = _auth_config(url)
    env = _base_env()
    if not config:
        return [], env
    key, value = config
    if _git_supports_config_env(path):
        # Уважаем уже выставленные GIT_CONFIG_*, если кто-то их задал снаружи.
        index = int(env.get("GIT_CONFIG_COUNT") or 0)
        env["GIT_CONFIG_KEY_%d" % index] = key
        env["GIT_CONFIG_VALUE_%d" % index] = value
        env["GIT_CONFIG_COUNT"] = str(index + 1)
        return [], env
    return ["-c", f"{key}={value}"], env


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
    remote = (settings.git_update_remote or "origin").strip()
    lines = [
        f"Каталог:          {path}",
        f"Remote / ветка:   {remote} / {settings.git_update_branch or '(текущая)'}",
        f"Сборка фронтенда: {'да' if settings.git_update_build_frontend else 'нет'}",
        f"Перезапуск после: {'да' if settings.git_update_restart_backend else 'нет'}",
    ]
    if error:
        lines.append(f"ОШИБКА: {error}")
        return "\n".join(lines)

    code, branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], path, 30)
    lines.append(f"Текущая ветка:    {branch if code == 0 else '?'}")
    lines.append(f"Текущая ревизия:  {current_revision(path)}")

    url = remote_url(path, remote)
    lines.append(f"URL remote:       {_redact(url) or '(не найден)'}")
    lines.append(f"Токен доступа:    {_token_state(url)}")

    # Живая проверка доступа: «токен задан» само по себе ничего не говорит о
    # том, пустит ли он на самом деле, и без этой строки выяснялось бы только
    # в момент обновления — то есть в самый неподходящий момент.
    args, env = _git_auth(path, url)
    code, out = _run(["git"] + args + ["ls-remote", "--heads", remote], path, 60, env=env)
    if code == 0:
        lines.append("Доступ к remote:  есть")
    else:
        lines.append("Доступ к remote:  НЕТ")
        for line in out.splitlines()[:5]:
            lines.append(f"    {line}")
    return "\n".join(lines)


_AUTH_FAILURE_MARKERS = (
    "authentication failed", "could not read username", "could not read password",
    "terminal prompts disabled", "invalid username or password", "403",
    "repository not found", "permission denied", "access denied",
)


def _looks_like_auth_failure(output: str) -> bool:
    """Отличает «не пустили» от «ветки разошлись».

    Подсказка про токен, выданная на конфликт веток, только сбивает с толку —
    и наоборот, «разберитесь вручную» на 403 отправляет чинить не то."""
    low = output.lower()
    return any(marker in low for marker in _AUTH_FAILURE_MARKERS)


def _token_state(url: str) -> str:
    settings = get_settings()
    if not (settings.git_update_token or "").strip():
        return "не задан (для приватного репозитория по HTTPS он обязателен)"
    if url and not url.startswith(("http://", "https://")):
        return "задан, но remote не HTTPS — токен не используется (ssh ходит по ключу)"
    return f"задан, пользователь {_token_username()}"


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

    url = remote_url(path, remote)
    auth_args, env = _git_auth(path, url)
    if _auth_config(url):
        emit(f"Доступ:  по токену ({_token_username()})")
    elif (settings.git_update_token or "").strip() and url and not url.startswith("http"):
        emit("! Токен задан, но remote не HTTPS — ssh авторизуется ключом, токен не применяется.")

    pull_cmd = ["git"] + auth_args + ["pull", "--ff-only", remote] + ([branch] if branch else [])
    # В консоль печатается команда БЕЗ аргументов авторизации: на старом git
    # токен уезжает в `-c`, и выводить его в лог нельзя.
    emit(f"→ git pull --ff-only {remote}{' ' + branch if branch else ''}")
    code, out = _run(pull_cmd, path, _GIT_TIMEOUT, env=env)
    for line in out.splitlines():
        emit(f"    {line}")
    if code != 0:
        if _looks_like_auth_failure(out):
            emit("✗ git pull не прошёл: репозиторий не пустил.")
            emit("  Если репозиторий приватный — задайте токен доступа на странице")
            emit("  «Администрирование» → «Обновление кода из git» и проверьте")
            emit("  командой `update status`.")
        else:
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
