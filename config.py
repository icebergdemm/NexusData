from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import List, Dict
import json
import os


class Settings(BaseSettings):
    app_name: str = "Barotrauma RoundManager API"
    app_version: str = "3.0"
    debug: bool = False
    
    # Database
    database_url: str = "mysql+pymysql://python_fastapi:pfa0471B-@localhost:3306/barotrauma"
    
    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    
    # CORS
    cors_origins: List[str] = ["http://localhost:3000", "http://localhost:8000"]
    cors_credentials: bool = True
    cors_methods: List[str] = ["GET", "POST", "PUT", "DELETE"]
    cors_headers: List[str] = ["*"]
    
    # Security
    secret_key: str = Field("your-secret-key-change-in-production", env="SECRET_KEY")
    api_key: str = Field("your-api-key-change-in-production", env="API_KEY")
    require_auth: bool = True
    allowed_ips: List[str] = ["92.38.222.172"]  # whitelist для пропуска api_key проверки
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
    rate_limit_requests: int = 100  # requests per window
    rate_limit_window: int = 60  # seconds
    
    # Logging
    log_level: str = "DEBUG" if debug else "INFO"
    
    # Discord OAuth
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = "https://nd.rp-asper.ru/auth/discord/callback"
    
    # Discord Proxy
    use_discord_proxy: bool = False
    discord_proxy_url: str = "http://localhost:8001"
    
    # Ranks and Kits mappings
    ranks_mapping: Dict[str, str] = {}
    kits_mapping: Dict[str, str] = {}
    roles_mapping: Dict[str, List[str]] = {}
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        config_dir = os.path.join(os.path.dirname(__file__), 'config')
        ranks_file = os.path.join(config_dir, 'ranks.json')
        kits_file = os.path.join(config_dir, 'kits.json')
        
        if os.path.exists(ranks_file):
            with open(ranks_file, 'r', encoding='utf-8') as f:
                self.ranks_mapping = json.load(f)
        
        if os.path.exists(kits_file):
            with open(kits_file, 'r', encoding='utf-8') as f:
                self.kits_mapping = json.load(f)
        
        roles_file = os.path.join(config_dir, 'roles.json')
        if os.path.exists(roles_file):
            with open(roles_file, 'r', encoding='utf-8') as f:
                self.roles_mapping = json.load(f)
    
    model_config = {
        "env_file": ".env",
        "case_sensitive": False
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()
