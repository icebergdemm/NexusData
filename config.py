from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import List, Dict, Any
import json
import os


def _str_to_list(value: Any, default: List[str]) -> List[str]:
    """Convert a raw .env string (JSON array or comma-separated) to List[str]."""
    if isinstance(value, list):
        return [str(x) for x in value]
    if not isinstance(value, str) or not value.strip():
        return default
    v = value.strip()
    if v.startswith("["):
        try:
            parsed = json.loads(v)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            pass
    return [x.strip() for x in v.split(",") if x.strip()]


class Settings(BaseSettings):
    app_name: str = "Barotrauma RoundManager API"
    app_version: str = "3.0"
    debug: bool = False

    # Database
    database_url: str = "mysql+pymysql://python_fastapi:pfa0471B-@localhost:3306/barotrauma"

    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    # >1 launches api_server.py in multi-process mode: a dedicated
    # scheduler/background-worker process plus N uvicorn worker processes
    # serving HTTP, so one slow synchronous DB query in one request doesn't
    # stall every other concurrent request site-wide (see main.py's startup
    # hook — gated on WORKER_ROLE so the scheduler never runs more than
    # once). 1 (default) preserves the exact previous single-process behavior.
    uvicorn_workers: int = Field(1)

    # CORS — stored as plain str to avoid pydantic-settings JSON pre-parsing errors;
    # converted to List[str] in __init__
    cors_origins: str = "http://localhost:3000,http://localhost:8000"
    cors_origins_list: List[str] = []          # populated in __init__
    cors_credentials: bool = True
    cors_methods: List[str] = ["GET", "POST", "PUT", "DELETE"]
    cors_headers: List[str] = ["*"]

    # Security
    secret_key: str = Field("your-secret-key-change-in-production")
    api_key: str = Field("your-api-key-change-in-production")
    # Separate dedicated key for read-only external "client" integrations
    # (world-state export, warehouse export — routes/world_state.py,
    # routes/warehouse.py) — intentionally NOT the same secret as `api_key`
    # (used by the Barotrauma server's own ingest calls), so a consumer of
    # these read endpoints never needs the ingest credential. Empty by
    # default = these endpoints stay closed until explicitly configured.
    client_api_key: str = Field("")
    # Optional bridge to AsperAIDispatcher. Every player terminal message is
    # forwarded; the model returns respond=false for traffic unrelated to the
    # dispatch post, otherwise its reply is queued back into the game.
    ai_dispatcher_url: str = Field("")
    ai_dispatcher_api_key: str = Field("")
    ai_dispatcher_timeout_seconds: float = Field(60.0, gt=0, le=180)
    # Legacy WorldStateStreamer builds may omit server_name and even race
    # snapshot ahead of world/init. Used only when neither the payload nor the
    # existing round row identifies the source server.
    world_default_server_name: str = Field("ASPER1", min_length=1, max_length=100)
    # Large-contact alerts are allowlist-based. Any other non-abyss creature is
    # also reported after entering the 200 m Yasa perimeter. Abyss species stay
    # denied even if an operator accidentally includes one in the allowlist.
    ai_large_creature_species: str = (
        "Bonethresher,Tigerthresher,Moloch,Molochblack,Hammerhead,Hammerheadgold,"
        "Hammerheadmatriarch,Watcher,Spineling_giant,Crawlerbroodmother,Carrier"
    )
    ai_abyss_creature_species: str = "Endworm,Charybdis,Latcher,Doomworm"
    # New entity IDs may appear while one creature group is instantiated over
    # several telemetry snapshots. Keep nearby same-species IDs under one
    # tracked sonar contact instead of announcing every member separately.
    ai_creature_alert_tracking_seconds: int = Field(600, ge=0, le=3600)
    ai_creature_alert_tracking_distance_meters: int = Field(300, ge=0, le=5000)
    ai_large_creature_species_list: List[str] = []
    ai_abyss_creature_species_list: List[str] = []
    require_auth: bool = True
    # stored as plain str; converted to List[str] in __init__
    allowed_ips: str = "92.38.222.172"
    allowed_ips_list: List[str] = []           # populated in __init__

    # Sessions
    session_max_age: int = 60 * 60 * 24 * 7  # 7 days
    session_cookie_name: str = "session"

    # CSRF
    csrf_cookie_name: str = "csrf_token"

    # Pagination
    default_page_size: int = 50
    max_page_size: int = 1000

    # Rate Limiting
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 100
    rate_limit_window: int = 60

    # Logging
    log_level: str = "INFO"

    # Discord OAuth
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = "https://nd.rp-asper.ru/auth/discord/callback"
    # Extra hostnames the site is also directly reachable on (e.g. a second
    # entry point bypassing the main proxy) besides the one baked into
    # discord_redirect_uri above. Discord validates redirect_uri byte-for-byte
    # against what's registered on its side, so logging in from one of these
    # extra hosts must round-trip through THAT SAME host's own
    # /auth/discord/callback — see routes/admin.py's _resolve_oauth_redirect_uri.
    # Each one must ALSO be added as a Redirect URI in the Discord Developer
    # Portal, or Discord will reject the login with its own error page.
    extra_allowed_hosts: str = ""
    extra_allowed_hosts_list: List[str] = []   # populated in __init__

    # Discord Bot (discord_bot/bot.py — runs in-process, see supervise_bot())
    discord_bot_token: str = Field("")
    # ВНИМАНИЕ: аргумент env= у Field в pydantic v2 не работает — он молча
    # уходит в json_schema_extra, а имя переменной окружения выводится из
    # ИМЕНИ ПОЛЯ. Для discord_guild_id это означало поиск DISCORD_GUILD_ID,
    # тогда как в .env (и в прежнем discord_bot/.env, откуда настройка
    # переехала) ключ называется GUILD_ID. Значение не читалось, GUILD_ID в
    # боте становился 0, и get_guild(0) всегда возвращал None — роли Discord
    # не выдавались с ошибкой «сервер недоступен боту», хотя всё настроено
    # верно. Правильный способ задать имя переменной — validation_alias.
    discord_guild_id: str = Field(
        "", validation_alias=AliasChoices("GUILD_ID", "DISCORD_GUILD_ID")
    )

    # Обновление кода из git (консольная команда `update`, см.
    # services_git_update.py). Настраивается на странице «Администрирование».
    # Пустой каталог — сам каталог приложения.
    git_update_repo_dir: str = ""
    git_update_remote: str = "origin"
    git_update_branch: str = ""
    # Адрес репозитория. Нужен, чтобы развернуть код на сервере, где ещё нет
    # рабочей копии (`update bootstrap`): на Pterodactyl обычно доступна только
    # консоль, без shell'а, и сделать git clone руками попросту негде. Когда
    # рабочая копия уже есть, адрес берётся из самого remote и это поле не
    # используется.
    git_update_repo_url: str = ""
    # Токен доступа для ПРИВАТНОГО репозитория по HTTPS (GitHub PAT,
    # GitLab/Gitea access token). Пусто — тянем как есть: для публичного
    # репозитория и для ssh-remote токен не нужен. Токен НЕ прописывается ни в
    # remote URL, ни в .git/config: он уходит одним заголовком Authorization
    # только на хост этого remote (см. services_git_update.py).
    git_update_token: str = ""
    # Имя пользователя в паре к токену. Для GitHub годится любое непустое
    # (общепринято x-access-token), для GitLab — oauth2.
    git_update_token_username: str = "x-access-token"
    # Пересобирать фронтенд, если в обновлении менялось что-то во frontend/:
    # dist/ в репозитории нет, без сборки сайт останется на старой версии.
    git_update_build_frontend: bool = True
    # Ветка с ГОТОВОЙ сборкой фронтенда (её наполняет workflow
    # .github/workflows/frontend-dist.yml). Задана — `update` скачивает dist
    # оттуда и не трогает npm вообще. Это единственный рабочий вариант там,
    # где Node.js в контейнере нет и поставить его некуда: собирать SPA рядом
    # с приложением на контейнере с лимитом памяти всё равно плохая идея.
    git_update_frontend_dist_branch: str = ""
    # Явный путь к исполняемому файлу npm. Пусто — ищем сами: сначала в PATH,
    # потом по частым местам установки (nvm и т.п.) — см. services_git_update.
    # _resolve_npm(). Нужен, когда контейнер не login-shell (обычная ситуация
    # на Pterodactyl) и nvm/менеджер версий Node добавляет npm в PATH только
    # для интерактивных сессий, которых у процесса приложения нет — либо
    # когда Node.js в образе контейнера вовсе не установлен.
    git_update_npm_path: str = ""
    # Перезапускать backend после обновления. По умолчанию выключено: рестарт
    # рвёт открытые запросы, и решение «когда именно» лучше оставлять человеку
    # (`update restart` делает это разово, не меняя настройку).
    git_update_restart_backend: bool = False

    # Discord Trap Webhook
    discord_trap_webhook_url: str = ""

    # Sub-projects Discord Webhook (for embed publishing)
    sub_projects_webhook_url: str = ""

    # Sub-projects file storage
    sub_projects_upload_dir: str = "uploads/sub_projects"

    # Round replays (.barodemo) storage
    replays_upload_dir: str = "uploads/replays"
    replays_retention_days: int = 30  # auto-delete demos older than this

    # Steam Web API (avatar auto-fetch) — https://steamcommunity.com/dev/apikey
    steam_api_key: str = ""

    # Pterodactyl panel API (department test-server power/status — item 4)
    pterodactyl_panel_url: str = ""
    pterodactyl_api_key: str = ""
    # Same Pterodactyl panel, a DIFFERENT server — the "Сервер 2"/ASPER2 test
    # server that Sub-projects/Полигон deploy to. Power-controlled from the
    # normal Sub-projects page (routes/sub_projects.py), gated by the
    # site-wide sub_projects:manage_test_server permission — unrelated to any
    # one department's own pterodactyl_server_uuid.
    sub_projects_test_server_uuid: str = ""

    # Тот же Pterodactyl-панель (pterodactyl_panel_url/pterodactyl_api_key), но
    # UUID БОЕВЫХ игровых серверов — для консоли на странице «Модерирование»
    # (routes/moderation.py, права moderation:console_server1/console_server2).
    # Намеренно ОТДЕЛЬНЫЕ от sub_projects_test_server_uuid — та переменная про
    # тестовый сервер деплоя саб-карт, смешивать с боевыми серверами нельзя.
    pterodactyl_server1_uuid: str = ""
    pterodactyl_server2_uuid: str = ""

    # Document library file storage (real .docx/.xlsx files edited via ONLYOFFICE)
    documents_upload_dir: str = "uploads/documents"

    # ONLYOFFICE Document Server integration
    onlyoffice_document_server_url: str = "https://nd.rp-asper.ru/onlyoffice"  # public browser-facing URL, e.g. https://nd.rp-asper.ru:8082
    onlyoffice_callback_base_url: str = "https://nd.rp-asper.ru"    # our own base URL, reachable BY the Document Server (fetch + callback)
    onlyoffice_jwt_secret: str = Field("")

    # Our own public base URL, reachable BY Discord's servers (same idea as
    # onlyoffice_callback_base_url above, different consumer). Used to build
    # the image URL embedded in sub-project publish embeds
    # (services_sub_projects.py's publish_to_discord) — that URL is fetched
    # SERVER-SIDE by Discord to render the embed image, so it can't be built
    # from request.base_url: behind nginx/proxy_server.py that often resolves
    # to an internal scheme/host/port Discord can't reach, which is exactly
    # why the image silently failed to attach some of the time while the
    # rest of the embed (text) still posted fine.
    public_site_url: str = "https://nd.rp-asper.ru"

    # SFTP Server 1
    sftp_server_1_host: str = ""
    sftp_server_1_port: int = 22
    sftp_server_1_username: str = ""
    sftp_server_1_password: str = ""
    sftp_server_1_key_path: str = ""
    sftp_server_1_remote_dir: str = ""
    sftp_server_1_name: str = "Сервер 1"

    # SFTP Server 2
    sftp_server_2_host: str = ""
    sftp_server_2_port: int = 22
    sftp_server_2_username: str = ""
    sftp_server_2_password: str = ""
    sftp_server_2_key_path: str = ""
    sftp_server_2_remote_dir: str = ""
    sftp_server_2_name: str = "Сервер 2"

    # SFTP Logs directories (for server log download)
    sftp_server_1_logs_dir: str = ""
    sftp_server_2_logs_dir: str = ""
    # Barotrauma text logs contain server-local wall-clock timestamps without
    # an offset. Replay and round timestamps, on the other hand, are stored as
    # naive UTC. Keep this conversion explicit so replay markers do not move
    # when NexusData is deployed in a container with a different process TZ.
    logs_default_timezone: str = "Europe/Moscow"
    sftp_server_1_logs_timezone: str = ""
    sftp_server_2_logs_timezone: str = ""

    # Discord Bot channel for sub-projects publishing
    sub_projects_discord_channel_id: str = ""

    # Jitsi Meet (self-hosted)
    jitsi_domain: str = "meet.jit.si"  # Replace with your self-hosted domain: meet.rp-asper.ru

    # Ranks and Kits mappings (populated from JSON files in __init__)
    ranks_mapping: Dict[str, str] = {}
    kits_mapping: Dict[str, str] = {}
    roles_mapping: Dict[str, List[str]] = {}
    rank_discord_roles: Dict[str, str] = {}  # rank_id -> discord_role_id

    # Server permissions configuration
    server_permissions_mapping: Dict[str, Dict] = Field(default_factory=lambda: {
        "mute":             {"name": "Мут",                      "description": "Возможность выдавать мут"},
        "kick":             {"name": "Кик",                      "description": "Возможность кикать игроков"},
        "ban":              {"name": "Бан",                      "description": "Возможность банить игроков"},
        "manage_whitelist": {"name": "Управление вайтлистом",    "description": "Возможность управлять вайтлистом"},
        "manage_moderators":{"name": "Управление модераторами",  "description": "Возможность добавлять/удалять модераторов"},
        "manage_admins":    {"name": "Управление администраторами","description": "Возможность управлять администраторами"},
    })

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Convert str fields to lists
        object.__setattr__(self, "cors_origins_list", _str_to_list(
            self.cors_origins,
            ["http://localhost:3000", "http://localhost:8000"],
        ))
        object.__setattr__(self, "allowed_ips_list", _str_to_list(
            self.allowed_ips,
            ["92.38.222.172"],
        ))
        object.__setattr__(self, "extra_allowed_hosts_list", _str_to_list(
            self.extra_allowed_hosts,
            [],
        ))
        object.__setattr__(self, "ai_large_creature_species_list", _str_to_list(
            self.ai_large_creature_species,
            [],
        ))
        object.__setattr__(self, "ai_abyss_creature_species_list", _str_to_list(
            self.ai_abyss_creature_species,
            [],
        ))

        # Load JSON config files
        config_dir = os.path.join(os.path.dirname(__file__), "config")
        for attr, filename in [
            ("ranks_mapping", "ranks.json"),
            ("kits_mapping",  "kits.json"),
            ("roles_mapping",  "roles.json"),
            ("rank_discord_roles", "rank_discord_roles.json"),
        ]:
            path = os.path.join(config_dir, filename)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    object.__setattr__(self, attr, json.load(f))

    model_config = {
        "env_file": ".env",
        "case_sensitive": False,
        "env_ignore_empty": True,
        # A leftover/deprecated key in a deployed server's .env (e.g. from a
        # field removed in a later code update — this has already happened
        # with USE_DISCORD_PROXY/DISCORD_PROXY_URL) must never hard-crash the
        # whole process at import time. pydantic-settings' default here is
        # effectively "forbid", which turns "we cleaned up an old setting" in
        # code into "every server still holding that .env line goes offline".
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()

def reset_settings_cache():
    """Clear the settings cache - useful for testing or reloading config"""
    get_settings.cache_clear()
