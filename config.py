from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_core import PydanticUseDefault
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import List, Dict, Any
import json
import os
import secrets
import shutil
from datetime import datetime
from pathlib import Path


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


def snapshot_env_file(path: str | os.PathLike) -> Path | None:
    """Make a private point-in-time copy before an in-place config write."""
    source = Path(path)
    if not source.is_file():
        return None
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
    snapshot = source.with_name(f"{source.name}.backup-{timestamp}-{secrets.token_hex(4)}")
    shutil.copyfile(source, snapshot)
    os.chmod(snapshot, 0o600)
    return snapshot


def update_env_file(path: str | os.PathLike, updates: Dict[str, Any]) -> Path | None:
    """Update only named .env keys without rewriting unrelated file content.

    Keep comments, blank lines, unknown settings, ordering, and existing line
    endings intact. The snapshot is made before the atomic replacement so a
    failed admin save never leaves a half-written environment file behind.
    """
    target = Path(path)
    original = target.read_text(encoding="utf-8") if target.is_file() else ""
    newline = "\r\n" if "\r\n" in original else "\n"
    normalized: Dict[str, str] = {}
    for key, value in updates.items():
        name = str(key).strip()
        text_value = "" if value is None else str(value)
        if not name or "\r" in name or "\n" in name or "\r" in text_value or "\n" in text_value:
            raise ValueError(".env keys and values cannot contain line breaks")
        normalized[name] = text_value

    lines = original.splitlines(keepends=True)
    output: list[str] = []
    replaced: set[str] = set()
    for line in lines:
        content = line.rstrip("\r\n")
        if not content.lstrip().startswith("#") and "=" in content:
            key, _, _ = content.partition("=")
            key = key.strip()
            if key in normalized:
                output.append(f"{key}={normalized[key]}{line[len(content):]}")
                replaced.add(key)
                continue
        output.append(line)

    missing = [key for key in normalized if key not in replaced]
    if missing:
        if output and not output[-1].endswith(("\n", "\r")):
            output[-1] += newline
        output.extend(f"{key}={normalized[key]}{newline}" for key in missing)

    snapshot = snapshot_env_file(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp-{secrets.token_hex(8)}")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            stream.write("".join(output))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return snapshot


class Settings(BaseSettings):
    app_name: str = "Barotrauma RoundManager API"
    app_version: str = "3.1.0"
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
    # Server-side voice transcription credential. It is stored in the
    # NexusData .env and exposed only to authenticated game-server requests;
    # the admin UI always masks it.
    deepgram_api_key: str = Field("")
    deepgram_model: str = Field("nova-3")
    deepgram_language: str = Field("ru")
    # One ASR configuration, distributed to game servers by /communications/config.
    deepgram_segment_gap_ms: int = Field(5000, ge=500, le=5000)
    deepgram_max_segment_seconds: int = Field(60, ge=5, le=120)
    deepgram_finalize_timeout_seconds: int = Field(8, ge=2, le=30)
    deepgram_io_timeout_seconds: int = Field(10, ge=2, le=30)
    deepgram_audio_queue_frames: int = Field(512, ge=64, le=2048)
    deepgram_endpointing_ms: int = Field(600, ge=100, le=2000)
    # Optional bridge to AsperAIDispatcher. Every player terminal message is
    # forwarded; the model returns respond=false for traffic unrelated to the
    # dispatch post, otherwise its reply is queued back into the game.
    ai_dispatcher_url: str = Field("")
    ai_dispatcher_api_key: str = Field("")
    # Верхняя граница снята: столько, сколько реально думает выбранная модель,
    # решает владелец стенда. Значение должно быть не меньше AGENT_TIMEOUT_SECONDS
    # диспетчера, иначе NexusData бросит запрос раньше, чем придёт ответ.
    ai_dispatcher_timeout_seconds: float = Field(60.0, gt=0)
    # Dedicated player-dossier bridge. It deliberately has its own endpoint
    # and credential: the radio dispatcher and moderator dossier have
    # different data sensitivity and failure domains.
    ai_dossier_url: str = Field("")
    ai_dossier_api_key: str = Field("")
    ai_dossier_tls_ca_file: str = Field("")
    ai_dossier_timeout_seconds: float = Field(240.0, gt=0, le=600)
    ai_dossier_model: str = Field("qwen3-14b-iq3_m", min_length=1, max_length=255)
    ai_dossier_active_days: int = Field(30, ge=1, le=365)
    ai_dossier_daily_limit: int = Field(10, ge=1, le=10)
    ai_dossier_schedule_hour: int = Field(4, ge=0, le=23)
    ai_dossier_max_log_events: int = Field(120, ge=1, le=500)
    ai_dossier_max_input_chars: int = Field(30000, ge=4000, le=100000)
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

    # LLM assistant for MonsterSpawnController. Transport is ALWAYS the shared
    # AI aggregator (AI_DOSSIER_URL + AI_DOSSIER_API_KEY, TLS via
    # AI_DOSSIER_TLS_CA_FILE — see services_ai_client.py): the assistant is a
    # client of the same queue as player dossiers, so generations never run
    # concurrently on the Qwen/KoboldCpp host. The fields below are only the
    # monster-specific generation params; there is no separate endpoint.
    # Per-server runtime state (enabled/auto_apply/last run) lives in
    # AppSetting key "monster_spawn_llm_config_v1" and is editable from the
    # admin UI; these fields are just the deployment defaults.
    monster_llm_model: str = Field("qwen3-4b-instruct")
    monster_llm_timeout_seconds: float = Field(240.0, gt=0, le=300)
    # Anti-hallucination defaults: low temperature, small top_p and a hard
    # JSON-schema validation on every response (see services_llm_monster.py).
    monster_llm_temperature: float = Field(0.15, ge=0.0, le=2.0)
    monster_llm_top_p: float = Field(0.8, gt=0.0, le=1.0)
    # Auto-cycle interval in minutes (0 = manual runs only).
    monster_llm_auto_interval_minutes: int = Field(0, ge=0, le=1440)

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

    # Address detector. Empty channel whitelist means no Discord traffic is
    # monitored; DB values from Discord Bot → Detector override these defaults.
    moderation_address_detector_enabled: bool = False
    moderation_signals_channel_id: str = ""
    moderator_role_id: str = ""
    redadmin_emoji_id: str = ""
    monitored_channel_ids: str = ""
    monitored_category_ids: str = ""
    excluded_channel_ids: str = ""
    address_context_messages: int = Field(4, ge=1, le=5)
    address_incident_window_seconds: int = Field(120, ge=1, le=86400)
    address_analyzer_mode: str = "hybrid"
    address_likely_threshold: float = Field(0.75, ge=0, le=1)
    address_clean_threshold: float = Field(0.80, ge=0, le=1)
    address_analysis_timeout_seconds: float = Field(15.0, gt=0, le=120)
    onnx_model_path: str = ""
    onnx_tokenizer_path: str = ""
    address_queue_size: int = Field(128, ge=1, le=10000)
    address_worker_count: int = Field(1, ge=1, le=16)

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

    # Replay-independent voice profile processing. Disabled until enabled in
    # the admin UI.
    voice_profiles_enabled: bool = False
    voice_profiles_process_new_replays: bool = False
    voice_profile_decoder_path: str = ""
    # Persistent, release-pinned ONNX model. The worker never downloads it.
    voiceprint_model_dir: str = "data/voiceprint-models"
    # Audio bytes are sent only from the background voice worker to this
    # separately hosted classifier service. Credentials are never exposed in UI.
    voice_synthetic_api_url: str = Field("")
    voice_synthetic_api_key: str = Field("")
    voice_synthetic_tls_ca_file: str = Field("")
    voice_synthetic_timeout_seconds: float = Field(30.0, gt=0, le=120)

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

    # Ticket evidence is mirrored locally before a Discord ticket channel is
    # deleted.  `uploads` is part of the default encrypted SFTP backup set, so
    # closed complaints keep their screenshots/videos without relying on
    # expiring Discord CDN URLs.  Limits are deliberately configurable: large
    # video evidence is useful, but one message must not exhaust the server.
    ticket_attachments_dir: str = "uploads/ticket_attachments"
    ticket_attachment_max_bytes: int = Field(200 * 1024 * 1024, ge=1)
    ticket_attachment_message_max_bytes: int = Field(400 * 1024 * 1024, ge=1)
    # Hard storage budgets cover both Discord mirroring and files uploaded
    # from the web panel. Set a scope to 0 only when an external filesystem
    # quota is intentionally responsible for that limit.
    ticket_attachment_ticket_quota_bytes: int = Field(2 * 1024 * 1024 * 1024, ge=0)
    ticket_attachment_global_quota_bytes: int = Field(50 * 1024 * 1024 * 1024, ge=0)
    ticket_attachment_download_timeout_seconds: float = Field(120.0, gt=0, le=900)
    ticket_attachment_orphan_grace_seconds: int = Field(24 * 60 * 60, ge=300, le=30 * 86400)
    ticket_web_upload_max_bytes: int = Field(25 * 1024 * 1024, ge=1)
    ticket_web_upload_max_files: int = Field(10, ge=1, le=25)
    # Open ticket channels are incrementally re-read so pre-existing CDN-only
    # rows become durable before a moderator ever attempts to close them.
    ticket_history_backfill_enabled: bool = True
    ticket_history_backfill_interval_seconds: int = Field(15 * 60, ge=60, le=86400)
    ticket_history_backfill_channels_per_run: int = Field(4, ge=1, le=100)
    ticket_history_backfill_messages_per_channel: int = Field(250, ge=1, le=5000)
    ticket_history_backfill_pause_seconds: float = Field(0.10, ge=0, le=5)
    # Unanswered tickets used to be closed and their Discord channel deleted
    # after `ticket_auto_close_hours`. That surprised players — a complaint
    # nobody answered simply vanished, with no warning and no DM — so the
    # timer is off by default and an open ticket now stays open until a human
    # closes it. The task itself is still registered: set this to true to
    # bring the deadline back without redeploying different code.
    ticket_auto_close_enabled: bool = False
    ticket_auto_close_hours: int = Field(72, ge=1, le=8760)

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

    # Encrypted off-site backups. Dedicated BACKUP_* names are preferred, but
    # the compact SFTP_IP/SFTP_USER/SFTP_PASSWORD aliases are supported for a
    # single simple backup destination.
    backup_enabled: bool = False
    backup_interval_hours: int = Field(24, ge=1, le=720)
    backup_retention_count: int = Field(14, ge=1, le=365)
    backup_sftp_host: str = ""
    backup_sftp_port: int = Field(22, ge=1, le=65535)
    backup_sftp_username: str = ""
    backup_sftp_password: str = ""
    backup_sftp_private_key_path: str = ""
    backup_sftp_private_key_passphrase: str = ""
    # An explicit file can be pre-populated for strict verification. If empty,
    # the backup service uses a private default file and pins on first use.
    backup_sftp_known_hosts_path: str = ""
    backup_sftp_remote_dir: str = "/data"
    # URL-safe or regular base64 encoding of exactly 32 random bytes.  The key
    # is intentionally not included in backups and must be escrowed separately.
    backup_encryption_key: str = ""
    backup_include_database: bool = True
    # Optional dedicated least-privilege dump account. Empty reuses
    # DATABASE_URL, which preserves existing deployments.
    backup_database_url: str = ""
    backup_include_log_index: bool = True
    # Project-relative or absolute paths.  Missing paths are recorded in the
    # encrypted manifest instead of silently creating an empty backup.
    backup_include_paths: str = "config,uploads,logs"
    backup_include_paths_list: List[str] = []
    backup_mysqldump_binary: str = "mysqldump"
    backup_command_timeout_seconds: int = Field(1800, ge=30, le=7200)
    # Empty means the OS private temporary directory.  Nothing is retained
    # here after a successful or failed run.
    backup_temp_dir: str = ""

    # Compact/simple backup credentials. A non-empty SFTP_PASSWORD selects this
    # coherent profile unless BACKUP_SFTP_PASSWORD is explicitly configured.
    sftp_ip: str = ""
    sftp_user: str = ""
    sftp_password: str = ""
    sftp_port: int = Field(22, ge=1, le=65535)

    # Discord Bot channel for sub-projects publishing
    sub_projects_discord_channel_id: str = ""

    # Jitsi Meet (self-hosted)
    jitsi_domain: str = "meet.jit.si"  # Replace with your self-hosted domain: meet.rp-asper.ru

    # Ranks and Kits mappings (populated from JSON files in __init__)
    ranks_mapping: Dict[str, str] = {}
    kits_mapping: Dict[str, str] = {}
    roles_mapping: Dict[str, List[str]] = {}
    rank_discord_roles: Dict[str, str] = {}  # rank_id -> discord_role_id

    @field_validator(
        "rate_limit_requests", "rate_limit_window",
        "sftp_server_1_port", "sftp_server_2_port",
        mode="before",
    )
    @classmethod
    def _empty_numeric_env_uses_default(cls, value):
        # The administration API used to turn omitted fields into empty
        # strings. Keep an already damaged .env bootable while the API fix is
        # deployed and prevent new damage in the updated handler.
        if value == "":
            raise PydanticUseDefault
        return value

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
        object.__setattr__(self, "backup_include_paths_list", _str_to_list(
            self.backup_include_paths,
            ["config", "uploads", "logs"],
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
