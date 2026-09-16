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
     Готовая CI-сборка проверяется и при неизменном main: workflow может
     закончиться уже после первого `update`, и повторная команда должна уметь
     забрать догнавший её frontend-dist.
  3. Перезапуск backend — по флагу. Python-код перечитывается только при
     рестарте, так что без него обновление бэкенда не вступит в силу.

Настройки живут в .env и правятся на странице «Администрирование».
"""
import base64
import logging
import os
import re
import subprocess
import sys
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


def _install_runtime_requirements(emit: Callable[[str], None], path: str) -> bool:
    """Install the Python runtime from the repository before restarting it.

    Pterodactyl runs ``update`` through this module rather than the standalone
    deploy script.  Without this step a requirements change reaches the disk,
    the process restarts, and optional features quietly keep running without
    their newly declared packages.  That is exactly how ONNX ended up in
    rules-only mode while ``requirements.txt`` already listed tokenizers.
    """
    requirements = os.path.join(path, "requirements.txt")
    if not os.path.isfile(requirements):
        emit("! requirements.txt не найден — Python-зависимости не обновлены.")
        return False

    emit("→ Устанавливаю Python-зависимости из requirements.txt…")
    code, out = _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "-r",
            requirements,
        ],
        path,
        _BUILD_TIMEOUT,
        env=_base_env(),
    )
    for line in out.splitlines()[-40:]:
        emit(f"    {line}")
    if code != 0:
        emit("✗ Python-зависимости не установились — backend будет перезапущен, но часть функций останется degraded.")
        return False
    emit("✓ Python-зависимости обновлены.")
    return True


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


def configured_url() -> str:
    """Адрес репозитория из настроек (для `update bootstrap`)."""
    return (get_settings().git_update_repo_url or "").strip()


def is_repo(path: str) -> bool:
    return os.path.isdir(path) and os.path.exists(os.path.join(path, ".git"))


def looks_like_url(value: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9+.\-]*://", value, re.IGNORECASE)) or value.startswith("git@")


def check_repo(path: str) -> Optional[str]:
    """Текст ошибки, если каталог не годится как репозиторий, иначе None."""
    # Частая путаница: в поле «Каталог репозитория» вписывают URL репозитория.
    # Общее «каталог не найден» описывает симптом, а не причину, и человек
    # идёт искать несуществующий каталог вместо того, чтобы поправить поле.
    if looks_like_url(path):
        return (
            f"В настройках указан адрес репозитория, а не каталог: {path}\n"
            "  Поле «Каталог репозитория» — путь в файловой системе СЕРВЕРА.\n"
            "  Адрес репозитория вписывается в соседнее поле «Адрес\n"
            "  репозитория», а этот каталог проще оставить пустым — тогда\n"
            "  используется каталог приложения."
        )
    if not os.path.isdir(path):
        return f"Каталог не найден: {path}"
    if not is_repo(path):
        hint = (
            "  Задайте «Адрес репозитория» на странице «Администрирование» и\n"
            "  выполните `update bootstrap` — код развернётся прямо отсюда."
            if not configured_url() else
            "  Выполните `update bootstrap` — код развернётся по указанному адресу."
        )
        return f"В каталоге нет репозитория git: {path}\n{hint}"
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
    if configured_url():
        lines.insert(1, f"Адрес репозитория: {_redact(configured_url())}")

    if error:
        lines.append(f"ОШИБКА: {error}")
        # Рабочей копии нет, но адрес есть — сразу проверяем, пустит ли он,
        # чтобы не выяснять это уже в процессе разворачивания.
        if not is_repo(path) and configured_url() and not looks_like_url(path):
            url = configured_url()
            args, env = _git_auth(path, url)
            code, out = _run(["git"] + args + ["ls-remote", "--heads", url], path, 60, env=env)
            lines.append(f"Доступ по адресу: {'есть — можно `update bootstrap`' if code == 0 else 'НЕТ'}")
            if code != 0:
                for line in out.splitlines()[:5]:
                    lines.append(f"    {line}")
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

    dist_branch = _frontend_dist_branch(settings)
    if dist_branch:
        configured_dist_branch = (settings.git_update_frontend_dist_branch or "").strip()
        source_note = "" if configured_dist_branch else " (автовыбор: npm не найден)"
        lines.append(
            f"Фронтенд:         готовая сборка из ветки «{dist_branch}»"
            f" (npm не нужен){source_note}"
        )
        code, out = _run(["git"] + args + ["ls-remote", "--heads", remote, dist_branch], path, 60, env=env)
        if code == 0 and out.strip():
            lines.append("    ветка на месте")
        elif code == 0:
            lines.append(f"    ВНИМАНИЕ: ветки «{dist_branch}» в репозитории нет —")
            lines.append("    запустите workflow «Build frontend dist» на GitHub.")
    else:
        npm, checked = _resolve_npm()
        if npm:
            lines.append(f"Фронтенд:         локальная сборка, npm: {npm}")
        else:
            lines.append("Фронтенд:         НЕ СОБЕРЁТСЯ — npm не найден")
            lines.append("    Проверено: " + "; ".join(checked))
            lines.append("    Проще всего — брать готовую сборку из CI: задайте ветку")
            lines.append("    в настройке «Ветка с готовой сборкой фронтенда».")
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
        for line in f"✗ {error}".splitlines():
            emit(line)
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
        emit("Обновлений в коде нет — main уже актуален.")

        # GitHub Actions собирает frontend-dist асинхронно. Первый `update`
        # нередко успевает забрать main раньше, чем workflow опубликует SPA.
        # Раньше повторный `update` завершался прямо здесь и уже никогда не
        # проверял готовую сборку, поэтому сервер мог бесконечно отдавать
        # старый интерфейс при совершенно актуальном HEAD.
        frontend_ok = True
        dist_branch = _frontend_dist_branch(settings)
        if settings.git_update_build_frontend and dist_branch:
            frontend_ok = _fetch_prebuilt_frontend(
                emit,
                path,
                remote,
                dist_branch,
            )

        restart = settings.git_update_restart_backend if force_restart is None else force_restart
        if restart:
            emit("→ Перезапуск backend…")
            _restart_backend(emit)

        if frontend_ok:
            emit("✓ Код и фронтенд актуальны.")
        return frontend_ok

    emit(f"Стало:   {current_revision(path)}")

    # Что именно поменялось — по этому решаем, нужна ли пересборка фронтенда.
    code, changed_files = _run(
        ["git", "diff", "--name-only", f"{before.strip()}..{after.strip()}"], path, 60
    )
    frontend_touched = code == 0 and any(
        line.startswith("frontend/") for line in changed_files.splitlines()
    )

    requirements_touched = "requirements.txt" in changed_files.splitlines()
    return _finish(
        emit,
        path,
        frontend_touched,
        force_restart,
        requirements_touched=requirements_touched,
    )


def _resolve_npm() -> Tuple[Optional[str], List[str]]:
    """Путь к исполняемому npm и список мест, где его поискали (для
    диагностики, если не нашли).

    Явно заданный путь в настройках — приоритет. Дальше PATH этого процесса,
    и только потом частые места установки Node.js помимо PATH: контейнер
    Pterodactyl обычно НЕ login-shell, и если Node.js на нём стоит через nvm,
    PATH процесса приложения его не увидит — nvm прописывает его в
    ~/.bashrc/~/.profile, которые читает только интерактивный login-shell."""
    import glob
    import shutil

    checked: List[str] = []

    configured = (get_settings().git_update_npm_path or "").strip()
    if configured:
        checked.append(configured)
        if os.path.isfile(configured) and os.access(configured, os.X_OK):
            return configured, checked
        return None, checked

    for name in (("npm.cmd", "npm.exe", "npm") if os.name == "nt" else ("npm",)):
        found = shutil.which(name)
        checked.append(f"PATH ({name})")
        if found:
            return found, checked

    home = os.path.expanduser("~")
    candidates = [
        "/usr/local/bin/npm", "/usr/bin/npm", "/opt/nodejs/bin/npm",
        os.path.join(home, ".nvm", "versions", "node", "*", "bin", "npm"),
        os.path.join(home, ".volta", "bin", "npm"),
        os.path.join(home, "n", "bin", "npm"),
    ]
    for pattern in candidates:
        checked.append(pattern)
        for match in sorted(glob.glob(pattern), reverse=True):  # новые версии nvm первыми
            if os.path.isfile(match) and os.access(match, os.X_OK):
                return match, checked
    return None, checked


def _frontend_dist_branch(settings=None) -> str:
    """Return the configured prebuilt branch or a safe Pterodactyl fallback.

    An empty setting historically meant "build locally". That remains true
    when npm exists. In a Python-only container, however, local compilation is
    impossible, while this repository already publishes the conventional
    ``frontend-dist`` branch. Selecting it automatically keeps ``update``
    operational even when the administration page or environment variable is
    unavailable.
    """
    settings = settings or get_settings()
    configured = (settings.git_update_frontend_dist_branch or "").strip()
    if configured:
        return configured
    npm, _checked = _resolve_npm()
    return "" if npm else "frontend-dist"


def _git_dir(path: str) -> str:
    """Настоящий каталог .git — он не всегда лежит рядом (worktree, submodule
    кладут туда файл-указатель)."""
    code, out = _run(["git", "rev-parse", "--absolute-git-dir"], path, 30, env=_base_env())
    return out.strip() if code == 0 else os.path.join(path, ".git")


def _fetch_prebuilt_frontend(emit: Callable[[str], None], path: str, remote: str, branch: str) -> bool:
    """Забрать ГОТОВУЮ сборку фронтенда из отдельной ветки вместо локальной.

    Ветку наполняет .github/workflows/frontend-dist.yml: в ней лежит
    содержимое frontend/dist одним orphan-коммитом, который каждый раз
    перезаписывается force-push'ем.

    Извлечение идёт во ВРЕМЕННЫЙ индекс (GIT_INDEX_FILE): обычный
    `git checkout FETCH_HEAD -- .` записал бы все файлы сборки в основной
    индекс репозитория, и рабочая копия осталась бы с сотней "staged"
    файлов, которых там быть не должно.
    """
    import shutil

    dist = os.path.join(path, "frontend", "dist")
    url = remote_url(path, remote)
    args, env = _git_auth(path, url)

    emit(f"→ git fetch {remote} {branch} (готовая сборка, npm не нужен)")
    code, out = _run(["git"] + args + ["fetch", "--depth=1", remote, branch], path, _GIT_TIMEOUT, env=env)
    for line in out.splitlines():
        emit(f"    {line}")
    if code != 0:
        emit(f"✗ Не удалось забрать ветку «{branch}» с готовой сборкой.")
        if _looks_like_auth_failure(out):
            emit("  Репозиторий не пустил — проверьте токен (`update status`).")
        else:
            emit("  Проверьте, что ветка существует и её наполняет workflow")
            emit("  «Build frontend dist» (вкладка Actions на GitHub).")
        return False

    git_dir = _git_dir(path)
    index_file = os.path.join(git_dir, "nd-dist-index")
    shutil.rmtree(dist, ignore_errors=True)
    os.makedirs(dist, exist_ok=True)

    extract_env = _base_env()
    extract_env["GIT_INDEX_FILE"] = index_file
    code, out = _run(
        ["git", f"--git-dir={git_dir}", f"--work-tree={dist}", "checkout", "FETCH_HEAD", "--", "."],
        path, _GIT_TIMEOUT, env=extract_env,
    )
    try:
        os.remove(index_file)
    except OSError:
        pass
    if code != 0:
        for line in out.splitlines()[-10:]:
            emit(f"    {line}")
        emit("✗ Не удалось распаковать готовую сборку.")
        return False

    _warn_if_stale_build(emit, path, dist)
    emit(f"✓ Готовая сборка фронтенда взята из ветки «{branch}».")
    return True


def _warn_if_stale_build(emit: Callable[[str], None], path: str, dist: str) -> None:
    """Предупредить, если сборку собрали ДО последних правок во frontend/.

    Гонка реальная: `update` может приехать раньше, чем workflow досчитает
    сборку, и тогда сайт молча получит старый фронтенд под новым бэкендом.
    Сравнивается не сам SHA (после frontend-коммита обычно идут ещё
    backend-коммиты, и простое несовпадение ничего не значит), а наличие
    правок именно во frontend/ между сборкой и текущим HEAD.
    """
    marker = os.path.join(dist, ".build-sha")
    if not os.path.isfile(marker):
        return
    try:
        with open(marker, "r", encoding="utf-8") as fh:
            build_sha = fh.read().strip()
    except OSError:
        return
    if not build_sha:
        return

    code, out = _run(
        ["git", "diff", "--name-only", f"{build_sha}..HEAD", "--", "frontend/"],
        path, 60, env=_base_env(),
    )
    if code != 0:
        # Коммита сборки нет в истории (обычное дело на shallow-копии) —
        # сказать что-то определённое нельзя, молчим.
        return
    changed = [line for line in out.splitlines() if line.strip()]
    if changed:
        emit(f"! Готовая сборка отстаёт: после неё во frontend/ поменялось {len(changed)} файлов.")
        emit(f"  Собрана на {build_sha[:7]}. Дождитесь workflow «Build frontend dist»")
        emit("  и повторите `update`, иначе сайт останется на прошлой версии.")


def _finish(
    emit: Callable[[str], None], path: str, frontend_touched: bool,
    force_restart: Optional[bool], requirements_touched: bool = False,
) -> bool:
    """Общий хвост обновления и разворачивания: пересборка фронтенда и
    перезапуск backend.

    Провал сборки фронтенда НЕ должен отменять перезапуск backend — раньше
    отменял (ранний `return False` до шага перезапуска), и это тихо съедало
    все бэкенд-фиксы: git pull отрабатывал, но процесс с новым Python-кодом
    так и не поднимался, пока на сервере не появлялся npm или ветка с
    готовой сборкой. У фронтенда и бэкенда разное время жизни изменений —
    первый можно донастроить отдельно, второй ждать не должен."""
    settings = get_settings()
    dependencies_ok = True
    frontend_ok = True

    if requirements_touched:
        dependencies_ok = _install_runtime_requirements(emit, path)

    if frontend_touched and settings.git_update_build_frontend:
        frontend_dir = os.path.join(path, "frontend")
        if not os.path.isdir(frontend_dir):
            emit("! Менялся frontend/, но каталога нет — сборка пропущена.")
        else:
            dist_branch = _frontend_dist_branch(settings)
            if dist_branch:
                # Готовая сборка из CI имеет приоритет над локальной: она
                # быстрее, детерминированнее и не требует Node.js на сервере.
                remote = (settings.git_update_remote or "origin").strip()
                frontend_ok = _fetch_prebuilt_frontend(emit, path, remote, dist_branch)
            else:
                npm, checked = _resolve_npm()
                if not npm:
                    emit("✗ Не найден исполняемый npm — фронтенд остался старым.")
                    emit("  Проверено: " + "; ".join(checked))
                    emit("  Варианты:")
                    emit("   1. Брать готовую сборку из CI — задайте ветку в настройке")
                    emit("      «Ветка с готовой сборкой фронтенда» (обычно frontend-dist).")
                    emit("      Собирать SPA на сервере тогда не нужно вообще.")
                    emit("   2. Указать полный путь к npm в настройке «npm: путь к")
                    emit("      исполняемому файлу», если Node.js в контейнере всё-таки есть.")
                    emit("   3. Установить Node.js/npm в контейнер.")
                    frontend_ok = False
                else:
                    for cmd, label in ((["ci"], "установка зависимостей"), (["run", "build"], "сборка")):
                        emit(f"→ {npm} {' '.join(cmd)} ({label}, это надолго)…")
                        code, out = _run([npm] + cmd, frontend_dir, _BUILD_TIMEOUT)
                        if code != 0:
                            for line in out.splitlines()[-20:]:
                                emit(f"    {line}")
                            emit(f"✗ npm {' '.join(cmd)} завершилась с ошибкой — фронтенд остался старым.")
                            frontend_ok = False
                            break
                    else:
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

    if dependencies_ok and frontend_ok:
        emit("✓ Обновление завершено.")
    elif not dependencies_ok and restart:
        emit("! Обновление завершено НЕ полностью: Python-зависимости не установились, backend перезапущен (см. выше).")
    elif restart:
        emit("! Обновление завершено НЕ полностью: backend перезапущен, фронтенд — нет (см. выше).")
    else:
        emit("! Обновление завершено НЕ полностью: фронтенд не собран (см. выше), backend не перезапускался.")
    return dependencies_ok and frontend_ok


def run_bootstrap(emit: Callable[[str], None], force_restart: Optional[bool] = None) -> bool:
    """Развернуть код по адресу репозитория в каталоге, где рабочей копии ещё
    нет (или где remote указывает не туда).

    Зачем отдельной командой, а не молча внутри `update`: приведение каталога
    к состоянию удалённой ветки перезаписывает отслеживаемые файлы, и делать
    такое неожиданно для человека нельзя. Отдельная команда и есть
    подтверждение.

    Файлы вне репозитория (.env, uploads/, data/, frontend/dist,
    node_modules) не трогаются: git reset --hard приводит в порядок только то,
    что есть в самом коммите, а всё перечисленное в репозитории отсутствует.
    """
    settings = get_settings()
    path = repo_dir()
    url = configured_url()
    remote = (settings.git_update_remote or "origin").strip()
    branch = (settings.git_update_branch or "").strip() or "main"

    if looks_like_url(path):
        emit(f"✗ В поле «Каталог репозитория» указан адрес: {path}")
        emit("  Туда нужен путь на сервере (или пусто — каталог приложения),")
        emit("  а адрес — в поле «Адрес репозитория».")
        return False
    if not url:
        emit("✗ Не задан адрес репозитория.")
        emit("  «Администрирование» → «Обновление кода из git» → «Адрес репозитория».")
        return False
    if not looks_like_url(url):
        emit(f"✗ «{url}» не похоже на адрес репозитория.")
        emit("  Ожидается https://… или git@host:owner/repo.git")
        return False
    if not os.path.isdir(path):
        emit(f"✗ Каталог не найден: {path}")
        return False

    emit(f"Каталог: {path}")
    emit(f"Адрес:   {_redact(url)}")
    emit(f"Ветка:   {branch}")

    if not is_repo(path):
        emit("→ git init (рабочей копии здесь ещё нет)")
        code, out = _run(["git", "init"], path, 60, env=_base_env())
        if code != 0:
            emit(f"✗ git init не прошёл: {out}")
            return False

    # Remote может уже быть — тогда просто переставляем адрес: bootstrap
    # используется и для переезда на другой репозиторий.
    existing = remote_url(path, remote)
    action = "set-url" if existing else "add"
    code, out = _run(["git", "remote", action, remote, url], path, 60, env=_base_env())
    if code != 0:
        emit(f"✗ Не удалось настроить remote: {out}")
        return False
    if existing and existing != url:
        emit(f"    remote {remote}: {_redact(existing)} → {_redact(url)}")

    auth_args, env = _git_auth(path, url)
    if _auth_config(url):
        emit(f"Доступ:  по токену ({_token_username()})")

    emit(f"→ git fetch {remote} {branch}")
    code, out = _run(["git"] + auth_args + ["fetch", "--depth=1", remote, branch], path, _GIT_TIMEOUT, env=env)
    for line in out.splitlines():
        emit(f"    {line}")
    if code != 0:
        if _looks_like_auth_failure(out):
            emit("✗ Репозиторий не пустил. Если он приватный — задайте токен доступа")
            emit("  на странице «Администрирование» и проверьте `update status`.")
        else:
            emit("✗ git fetch не прошёл — проверьте адрес и название ветки.")
        return False

    # Сколько отслеживаемых файлов будет перезаписано — по этому видно
    # масштаб происходящего до того, как оно случится.
    code, tracked = _run(["git", "ls-tree", "-r", "--name-only", "FETCH_HEAD"], path, 60, env=_base_env())
    if code == 0:
        existing_files = [f for f in tracked.splitlines() if f and os.path.exists(os.path.join(path, f))]
        if existing_files:
            emit(f"! Будет приведено к состоянию {remote}/{branch}: {len(existing_files)} файлов на диске.")
            emit("  Файлы вне репозитория (.env, uploads/, data/) не затрагиваются.")

    emit(f"→ git reset --hard FETCH_HEAD")
    code, out = _run(["git", "reset", "--hard", "FETCH_HEAD"], path, _GIT_TIMEOUT, env=_base_env())
    for line in out.splitlines():
        emit(f"    {line}")
    if code != 0:
        emit("✗ Не удалось привести каталог к состоянию удалённой ветки.")
        return False

    # Чтобы последующие `update` работали обычным git pull.
    _run(["git", "branch", "-f", branch, "FETCH_HEAD"], path, 60, env=_base_env())
    _run(["git", "checkout", branch], path, 60, env=_base_env())
    _run(["git", "config", f"branch.{branch}.remote", remote], path, 30, env=_base_env())
    _run(["git", "config", f"branch.{branch}.merge", f"refs/heads/{branch}"], path, 30, env=_base_env())

    emit(f"Стало:   {current_revision(path)}")
    emit("✓ Репозиторий развёрнут. Дальше достаточно обычного `update`.")

    return _finish(
        emit,
        path,
        frontend_touched=True,
        force_restart=force_restart,
        requirements_touched=True,
    )


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
