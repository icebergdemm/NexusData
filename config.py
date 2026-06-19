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

    # Discord Proxy
    use_discord_proxy: bool = False
    discord_proxy_url: str = "http://localhost:8001"

    # Discord Trap Webhook
    discord_trap_webhook_url: str = ""

    # Sub-projects Discord Webhook (for embed publishing)
    sub_projects_webhook_url: str = ""

    # Sub-projects file storage
    sub_projects_upload_dir: str = "uploads/sub_projects"

    # Round replays (.barodemo) storage
    replays_upload_dir: str = "uploads/replays"
    replays_retention_days: int = 30  # auto-delete demos older than this

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
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()

def reset_settings_cache():
    """Clear the settings cache - useful for testing or reloading config"""
    get_settings.cache_clear()