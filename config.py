from pydantic import BaseModel, Field
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
    uvicorn_workers: int = Field(1, env="UVICORN_WORKERS")

    # CORS — stored as plain str to avoid pydantic-settings JSON pre-parsing errors;
    # converted to List[str] in __init__
    cors_origins: str = "http://localhost:3000,http://localhost:8000"
    cors_origins_list: List[str] = []          # populated in __init__
    cors_credentials: bool = True
    cors_methods: List[str] = ["GET", "POST", "PUT", "DELETE"]
    cors_headers: List[str] = ["*"]

    # Security
    secret_key: str = Field("your-secret-key-change-in-production", env="SECRET_KEY")
    api_key: str = Field("your-api-key-change-in-production", env="API_KEY")
    # Separate dedicated key for read-only external "client" integrations
    # (world-state export, warehouse export — routes/world_state.py,
    # routes/warehouse.py) — intentionally NOT the same secret as `api_key`
    # (used by the Barotrauma server's own ingest calls), so a consumer of
    # these read endpoints never needs the ingest credential. Empty by
    # default = these endpoints stay closed until explicitly configured.
    client_api_key: str = Field("", env="CLIENT_API_KEY")
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
    discord_bot_token: str = Field("", env="DISCORD_BOT_TOKEN")
    discord_guild_id: str = Field("", env="GUILD_ID")

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

    # Document library file storage (real .docx/.xlsx files edited via ONLYOFFICE)
    documents_upload_dir: str = "uploads/documents"

    # ONLYOFFICE Document Server integration
    onlyoffice_document_server_url: str = "https://nd.rp-asper.ru/onlyoffice"  # public browser-facing URL, e.g. https://nd.rp-asper.ru:8082
    onlyoffice_callback_base_url: str = "https://nd.rp-asper.ru"    # our own base URL, reachable BY the Document Server (fetch + callback)
    onlyoffice_jwt_secret: str = Field("", env="ONLYOFFICE_JWT_SECRET")

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