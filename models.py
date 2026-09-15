from sqlalchemy import Column, Integer, String, ForeignKey, JSON, DateTime, func, Float, Boolean, Index, UniqueConstraint, Text
from sqlalchemy.orm import relationship
from database import Base


class Kit(Base):
    __tablename__ = "kits"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String(50), index=True)
    kit_level = Column(Integer, default=0)
    items = Column(JSON)
    # Kit variations (item 15) — `items` above IS variation 1/the default, so
    # old clients that only ever read `items` are unaffected. If variation
    # 2/3 aren't set, the client falls back to variation 1's `items`.
    variation_2_items = Column(JSON, nullable=True)
    variation_3_items = Column(JSON, nullable=True)
    # "Special" kits (item 15) — tied to this job_id + exactly one variation;
    # a player holding the matching kval_kit AND on that profession+variation
    # gets ONLY this kit's items as a full override (resolved client-side).
    is_special = Column(Boolean, nullable=False, default=False)
    special_variation = Column(Integer, nullable=True)


class PlayerData(Base):
    __tablename__ = "players_data"
    
    player_id = Column(Integer, primary_key=True, index=True)
    player_last_time_name = Column(String(255), index=True)
    player_steamid = Column(String(50), unique=True, index=True)
    last_login_time = Column(DateTime, default=func.now(), index=True)
    play_time = Column(Integer, default=0)  # in seconds
    rank_id = Column(Integer, default=0)
    kit_level = Column(Integer, default=0)
    achievements = Column(JSON, default=list)
    last_ip = Column(String(45), nullable=True)  # Last IP address
    last_subnet = Column(String(45), nullable=True)  # Subnet (e.g., 192.168.1.0/24)
    first_login = Column(DateTime, nullable=True)  # First login time
    avg_ping = Column(Integer, nullable=True)  # Average ping
    discord_id = Column(String(25), nullable=True, index=True)  # Discord user ID
    discord_id_confirmed = Column(Integer, default=0)  # 1 = confirmed by admin, 0 = auto-matched
    is_secret = Column(Integer, default=0)  # 1 = profile hidden from non-superusers
    labels = Column(JSON, default=lambda: [])   # list of label ids assigned to this player

    # Superuser-managed link between this in-game character and an ND admin
    # account, plus a name that overrides player_last_time_name everywhere
    # it's shown in ratings/punishments — regardless of whether the
    # underlying data (e.g. a punishment) came from the game-server API or
    # the admin panel, since this is resolved at display time, not baked in.
    linked_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True, index=True)
    forced_display_name = Column(String(255), nullable=True)
    disguise_enabled = Column(Boolean, nullable=False, default=False)
    # Once a fixed alias has actually been used, its ownership remains visible
    # even while the owner temporarily disables the mask.
    disguise_ever_enabled = Column(Boolean, nullable=False, default=False)
    disguise_mode = Column(String(16), nullable=False, default="random")
    disguise_fixed_alias = Column(String(32), nullable=True)
    disguise_version = Column(Integer, nullable=False, default=0)
    disguise_profile_id = Column(Integer, nullable=True, index=True)
    # True when the link was created by the confirmed-Discord-ID auto-match
    # worker (task_auto_link_admins, every 10 min) rather than a superuser —
    # the worker only ever fills in an empty link, never overwrites a manual one.
    linked_admin_auto = Column(Boolean, nullable=False, default=False)

    # Cached Steam avatar, refreshed periodically by task_fetch_steam_avatars.
    steam_avatar_url = Column(String(500), nullable=True)
    steam_avatar_fetched_at = Column(DateTime, nullable=True, index=True)

    stats = relationship("PlayerStats", backref="player", uselist=False)
    # Named distinctly from the "linked_admin" local variable elsewhere (the
    # auto-detected discord_id match used for profile classification) to
    # avoid confusion — this is the explicit, superuser-managed link.
    linked_admin_user = relationship("AdminUser", foreign_keys=[linked_admin_id])
    disguise_profiles = relationship(
        "DisguiseProfile",
        back_populates="player",
        cascade="all, delete-orphan",
        order_by="DisguiseProfile.sort_order, DisguiseProfile.id",
    )

    @property
    def display_name(self) -> str:
        return self.forced_display_name or self.player_last_time_name
    
    @property
    def play_time_hours(self) -> float:
        """Convert play_time (in seconds) to hours"""
        if not self.play_time:
            return 0.0
        return self.play_time / 3600.0


class DisguiseProfile(Base):
    """One public identity available to a linked superuser."""

    __tablename__ = "disguise_profiles"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=False, index=True)
    alias = Column(String(32), nullable=False)
    public_steam_id = Column(String(50), nullable=True, index=True)
    enabled = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    player = relationship("PlayerData", back_populates="disguise_profiles")

    __table_args__ = (
        UniqueConstraint("player_id", "alias", name="uq_disguise_profiles_player_alias"),
    )


class PlayerStats(Base):
    __tablename__ = "players_stats"
    
    player_id = Column(Integer, ForeignKey("players_data.player_id"), primary_key=True)
    successful_missions_captain = Column(Integer, default=0)
    total_missions_captain = Column(Integer, default=0)
    saved_players = Column(Integer, default=0)
    killed_monsters = Column(Integer, default=0)
    killed_players = Column(Integer, default=0)
    killed_npc = Column(Integer, default=0)


class AdminUser(Base):
    __tablename__ = "admin_users"
    
    id = Column(Integer, primary_key=True, index=True)
    discord_id = Column(String(50), unique=True, index=True)
    discord_username = Column(String(255))
    discord_avatar = Column(String(255), nullable=True)
    role = Column(String(50), default="admin")  # "Администратор", "Управляющий", "Смотритель"
    # Legacy flat permissions list: no longer used, kept for backwards compatibility
    permissions = Column(JSON, default=lambda: [])
    # Server-side permissions for managing moderator/admin privileges
    server_permissions = Column(JSON, default=lambda: [])
    # --- Privilege system ---
    privilege_group_id = Column(Integer, ForeignKey("admin_privilege_groups.id"), nullable=True)
    # Individual permission overrides — dict {"page": {"view": True/False, ...}}
    individual_permissions = Column(JSON, nullable=True)
    is_superuser = Column(Boolean, default=False)
    is_deleted = Column(Integer, default=0)  # 1 = soft-deleted, blocks login
    created_at = Column(DateTime, default=func.now())
    last_login = Column(DateTime, nullable=True)
    # True when this account was auto-provisioned by task_sync_department_roles
    # (a matching Discord role was found on the guild member) rather than
    # created via the normal "add admin" flow — surfaced in the UI so it can
    # be reviewed, same rationale as PlayerData.linked_admin_auto.
    created_by_worker = Column(Boolean, nullable=False, default=False)

    privilege_group = relationship("AdminPrivilegeGroup", back_populates="members", foreign_keys=[privilege_group_id])


class AdminPrivilegeGroup(Base):
    """Named group of permissions that can be assigned to admin users."""
    __tablename__ = "admin_privilege_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True)
    description = Column(String(500), nullable=True)
    # Dict: {"pages": {"players":{"view":True,"edit":False}, ...}, ...}
    permissions = Column(JSON, default=lambda: {})
    # Rate limiting: max actions per window (0 = unlimited)
    rate_limit_count = Column(Integer, default=0)
    rate_limit_window_secs = Column(Integer, default=3600)
    created_at = Column(DateTime, default=func.now())

    members = relationship("AdminUser", back_populates="privilege_group", foreign_keys="AdminUser.privilege_group_id")


class AdminRateLimit(Base):
    """Tracks action counts per admin for rate limiting."""
    __tablename__ = "admin_rate_limits"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id"), index=True)
    action = Column(String(100), default="general")
    count = Column(Integer, default=0)
    window_start = Column(DateTime, default=func.now())


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer)
    token = Column(String(128), unique=True, index=True)
    created_at = Column(DateTime, default=func.now())
    expires_at = Column(DateTime, nullable=True)
    revoked = Column(Integer, default=0)


class AdminLoginKey(Base):
    """Ключ входа на сайт без Discord (страница /slogin).

    Заводится суперпользователем для конкретного администратора — на случай,
    когда Discord недоступен: блокировки, потеря доступа к аккаунту, вход с
    машины без браузерной сессии Discord.

    Хранится ТОЛЬКО хеш: утечка таблицы не должна давать вход. Сам ключ
    показывается один раз при создании и больше нигде не восстановим. Хеш —
    обычный sha256 без «медленной» KDF, и это осознанно: ключ генерируем мы
    сами, в нём 160 бит случайности, перебирать там нечего, а bcrypt на
    каждой попытке входа дал бы только удобную точку для DoS.
    """
    __tablename__ = "admin_login_keys"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True)
    key_hash = Column(String(64), unique=True, nullable=False, index=True)
    # Первые символы ключа — чтобы в списке можно было отличить один от другого.
    key_prefix = Column(String(16), nullable=False)
    label = Column(String(200), nullable=True)
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())
    expires_at = Column(DateTime, nullable=True)
    revoked = Column(Boolean, nullable=False, default=False)
    last_used_at = Column(DateTime, nullable=True)
    last_used_ip = Column(String(45), nullable=True)
    use_count = Column(Integer, nullable=False, default=0)

    admin = relationship("AdminUser", foreign_keys=[admin_id])
    created_by = relationship("AdminUser", foreign_keys=[created_by_admin_id])


class AdminActionLog(Base):
    __tablename__ = "admin_action_logs"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, index=True)
    action = Column(String(255))
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)


class ErrorLogEntry(Base):
    """Персистентный журнал ERROR/CRITICAL-логов сайта и API — заполняется
    services_error_log.DatabaseErrorHandler, подключённым к корневому
    логгеру (см. logging_config.setup_logging). Отдельно от AdminActionLog
    выше (там — действия админов, тут — сбои кода) и от файлового
    logs/error.log (та же информация, но без поиска/фильтров в админке —
    файл остаётся как есть, для ручного tail и на случай недоступности БД).

    Доступ к странице просмотра — только суперпользователям (см.
    routes/error_log.py), поэтому здесь нет отдельной ACL-обвязки в духе
    permissions_registry."""
    __tablename__ = "error_log_entries"

    id = Column(Integer, primary_key=True, index=True)
    level = Column(String(10), nullable=False, index=True)          # ERROR / CRITICAL
    logger_name = Column(String(255), nullable=True, index=True)
    message = Column(Text, nullable=False)
    traceback = Column(Text, nullable=True)
    # Путь/метод текущего запроса, если ошибка случилась внутри обработки
    # HTTP-запроса (см. services_error_log.set_request_context) — для фоновых
    # потоков/воркеров остаются NULL, contextvars туда не наследуются.
    path = Column(String(500), nullable=True, index=True)
    method = Column(String(10), nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)


class PerfAnomaly(Base):
    """Персистентный журнал аномалий производительности — заполняется
    services_perf_monitor.py: три независимых детектора, каждый пишет свой
    kind сюда, когда что-то ведёт себя ненормально.
      - "event_loop_lag" — event loop процесса (uvicorn) не отвечал X секунд
        подряд. САМЫЙ прямой индикатор "сайт полностью завис": пока event
        loop заблокирован синхронным кодом где угодно в приложении, НИ ОДИН
        запрос от НИКОГО не обслуживается — неважно, какой конкретно
        обработчик виноват.
      - "slow_request" — конкретный HTTP-запрос (see request_timing_
        middleware в main.py) занял дольше порога. path/method заполнены.
      - "high_cpu" / "high_memory" — периодический сэмплер (services_perf_
        monitor.sample_resources, BACKGROUND-приоритет в планировщике)
        поймал процесс в момент аномального потребления ресурсов.

    Доступ — только суперпользователям (routes/perf_monitor.py), по той же
    причине, что и у ErrorLogEntry: детали о внутреннем состоянии сервера."""
    __tablename__ = "perf_anomalies"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(30), nullable=False, index=True)
    value = Column(Float, nullable=False)          # секунды (lag/duration) или проценты (cpu/mem)
    path = Column(String(500), nullable=True)       # только для slow_request
    method = Column(String(10), nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)


class PlayerKill(Base):
    __tablename__ = "player_kills"
    
    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=False, index=True)
    monster_type = Column(String(100), nullable=False, index=True)
    kills_count = Column(Integer, default=0)


class PlayerRoleAction(Base):
    __tablename__ = "player_role_actions"
    
    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=False, index=True)
    role = Column(String(50), nullable=False, index=True)
    action_type = Column(String(100), nullable=False)
    value = Column(Float, default=1)


class PlayerIP(Base):
    __tablename__ = "player_ips"
    
    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=False, index=True)
    ip = Column(String(45), nullable=False, index=True)
    subnet = Column(String(45), nullable=False, index=True)
    first_seen = Column(DateTime, nullable=False)
    last_seen = Column(DateTime, nullable=False)


class PlayerName(Base):
    __tablename__ = "player_names"
    
    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=False, index=True)
    nickname = Column(String(255), nullable=False, index=True)
    first_seen = Column(DateTime, nullable=False)
    last_seen = Column(DateTime, nullable=False)


class PlayerRoleStat(Base):
    __tablename__ = "player_role_stats"
    
    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=False, index=True)
    role = Column(String(50), nullable=False, index=True)
    rounds_played = Column(Integer, default=0)
    mvp_score = Column(Float, default=0)


class PlayerSession(Base):
    __tablename__ = "player_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=False, index=True)
    session_number = Column(Integer, nullable=False)
    login_time = Column(DateTime, nullable=False, index=True)
    logout_time = Column(DateTime, nullable=True)
    session_duration = Column(Integer, nullable=True)
    avg_ping = Column(Integer, nullable=True)


class ServerPrivilegeGroup(Base):
    """Named server privilege group (DB-managed, shown in UI)."""
    __tablename__ = "server_privilege_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(String(500), nullable=True)
    # Letter/string ID used by the game server (e.g. "moderator", "event_manager")
    group_key = Column(String(64), unique=True, nullable=False)
    commands = Column(JSON, default=lambda: [])           # e.g. ["kick", "ban"]
    allowed_action_ids = Column(JSON, default=lambda: []) # e.g. ["ManageRound", "Kick"]
    # Which NexusData admin privilege groups can assign this server group to players
    # List of AdminPrivilegeGroup IDs. Empty = only superuser can assign.
    assignable_by_groups = Column(JSON, default=lambda: [])
    created_at = Column(DateTime, default=func.now())


class ServerPlayerPrivilege(Base):
    """Server-side privileges for regular players (by SteamID)"""
    __tablename__ = "server_player_privileges"
    
    id = Column(Integer, primary_key=True, index=True)
    # NOT unique — a player can have several rows at once (one manual grant
    # per server_scope + one auto/department grant), which are UNIONED at
    # read time (see privilege_resolution.py). Uniqueness per (steamid,
    # server_scope, is_auto) is enforced at the application level (find-or-
    # create in routes/admin.py's assign endpoint / workers.py's auto-sync),
    # not via a DB constraint — see database.py's
    # _relax_server_player_privileges_unique_steamid for the migration that
    # dropped the old UNIQUE(steamid) index this column used to have.
    steamid = Column(String(50), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=True, index=True)
    # FK to ServerPrivilegeGroup (nullable — can have only individual commands)
    server_group_id = Column(Integer, ForeignKey("server_privilege_groups.id"), nullable=True, index=True)
    # Legacy string group_id from config/privilege_groups.json
    group_id = Column(String(64), nullable=True, index=True)

    # Directly assigned commands and action IDs for the player (overrides/extends group)
    commands = Column(JSON, default=lambda: [])
    allowed_action_ids = Column(JSON, default=lambda: [])

    privileges = Column(JSON, default=lambda: [])  # legacy list of privilege keys
    reason = Column(String(500), nullable=True)
    granted_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    granted_at = Column(DateTime, default=func.now())
    expires_at = Column(DateTime, nullable=True)
    # NULL = all servers, 'ASPER1' = only ASPER1, 'ASPER2' = only ASPER2
    server_scope = Column(String(20), nullable=True, index=True)
    # True when this row is owned by task_sync_auto_server_privileges (derived
    # from a department access level/head role) rather than a human via the
    # assign endpoint. Manual and auto grants for the same player now live in
    # SEPARATE rows (see the steamid comment above) and are summed together
    # at read time, rather than one clobbering the other — the worker only
    # ever touches rows where is_auto=True, the assign endpoint only ever
    # touches rows where is_auto=False.
    is_auto = Column(Boolean, nullable=False, default=False)
    # Which department's access level/head role produced the current
    # server_group_id when is_auto is True — drives the "От отдела: X" label.
    auto_source_department_id = Column(Integer, ForeignKey("departments.id"), nullable=True)


class AnticheatEvent(Base):
    """Anticheat trigger events reported by the game server."""
    __tablename__ = "anticheat_events"

    id = Column(Integer, primary_key=True, index=True)
    player_steamid = Column(String(50), nullable=False, index=True)
    player_name = Column(String(255), nullable=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=True, index=True)
    event_type = Column(String(128), nullable=False)
    details = Column(JSON, nullable=True)
    occurred_at = Column(DateTime, default=func.now())
    reviewed = Column(Integer, default=0, index=True)  # 0 = not reviewed, 1 = reviewed


class ServerActionQueue(Base):
    """LEGACY — kept for backward compat. Use ServerAction instead."""
    __tablename__ = "server_action_queue"

    id = Column(Integer, primary_key=True, index=True)
    action_type = Column(String(32), nullable=False)
    target_steamid = Column(String(50), nullable=False, index=True)
    reason = Column(String(500), nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    message_text = Column(String(1000), nullable=True)
    server_name = Column(String(100), nullable=True, index=True)
    queued_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    queued_at = Column(DateTime, default=func.now())
    executed = Column(Integer, default=0)
    executed_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="pending")
    error_message = Column(String(500), nullable=True)
    ack_asper1 = Column(Integer, default=0, nullable=False)
    ack_asper2 = Column(Integer, default=0, nullable=False)


class ServerAction(Base):
    """Server action v2 — one record, per-server execution tracking."""
    __tablename__ = "server_actions"

    id = Column(Integer, primary_key=True, index=True)
    action_type = Column(String(32), nullable=False)  # kick|ban|unban|message|trap|dispatch
    target_steamid = Column(String(50), nullable=False, index=True)
    reason = Column(String(500), nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    message_text = Column(String(2000), nullable=True)
    target_server = Column(String(20), nullable=True)  # ASPER1|ASPER2|NULL=both
    queued_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())

    # Per-server status: 0=pending, 1=done, 2=failed — indexed, polled by
    # every connected game server every ~10s (GET /server/actions).
    status_asper1 = Column(Integer, default=0, nullable=False, index=True)
    status_asper2 = Column(Integer, default=0, nullable=False, index=True)
    error_asper1 = Column(String(500), nullable=True)
    error_asper2 = Column(String(500), nullable=True)
    executed_at_asper1 = Column(DateTime, nullable=True)
    executed_at_asper2 = Column(DateTime, nullable=True)
    # Structured data for action types that don't fit the columns above
    # (torpedo_strike: {"x", "y"}; cargo_spawn: {"items": [{"identifier",
    # "quantity"}]}; supply_shuttle_send/recall: no payload needed) — see
    # routes/dispatch.py's cargo/torpedo/shuttle endpoints.
    payload = Column(JSON, nullable=True)


class PlayerNote(Base):
    """Admin notes / personal file for a player."""
    __tablename__ = "player_notes"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    content = Column(String(4000), nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class LabelFolder(Base):
    """Folder grouping for label categories."""
    __tablename__ = "label_folders"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())


class LabelCategory(Base):
    """Admin-defined label categories (e.g. 'Подозрение на читы')."""
    __tablename__ = "label_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), unique=True, nullable=False)
    color = Column(String(16), default="#888888")  # hex color for UI
    icon = Column(String(8), default="🏷")         # emoji icon
    is_secret = Column(Boolean, nullable=False, default=False, index=True)
    description = Column(String(500), nullable=True)
    folder_id = Column(Integer, ForeignKey("label_folders.id", ondelete="SET NULL"), nullable=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())


class PlayerLabel(Base):
    """Label assigned to a player by an admin."""
    __tablename__ = "player_labels"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("label_categories.id"), nullable=False, index=True)
    note = Column(String(500), nullable=True)  # optional short note about why
    assigned_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    assigned_at = Column(DateTime, default=func.now())


class ModerationActionTemplate(Base):
    """Reusable text for player-facing moderation actions."""
    __tablename__ = "moderation_action_templates"

    id = Column(Integer, primary_key=True, index=True)
    action_type = Column(String(20), nullable=False, index=True)  # message|warning|kick|ban
    name = Column(String(128), nullable=False)
    content = Column(String(2000), nullable=False)
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("action_type", "name", name="uq_moderation_action_template_name"),
    )


class PlayerTrap(Base):
    """Trap set on a player — fires Discord webhook when they join the server."""
    __tablename__ = "player_traps"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=False, index=True)
    target_steamid = Column(String(50), nullable=False, index=True)
    reason = Column(String(500), nullable=True)
    set_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    set_at = Column(DateTime, default=func.now())
    active = Column(Integer, default=1)       # 1 = active, 0 = triggered / disabled
    triggered_at = Column(DateTime, nullable=True)


class DiscordMember(Base):
    """Discord server member synced by the bot."""
    __tablename__ = "discord_members"

    id = Column(Integer, primary_key=True, index=True)
    discord_id = Column(String(25), unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)  # Discord username
    display_name = Column(String(255), nullable=True)  # Server nickname
    avatar = Column(String(255), nullable=True)
    joined_at = Column(DateTime, nullable=True)
    last_synced_at = Column(DateTime, default=func.now())
    # Discord role IDs currently held by this member (list of strings), used by
    # task_sync_department_roles to auto-provision department membership.
    role_ids = Column(JSON, nullable=False, default=lambda: [])


class DiscordBotAction(Base):
    """Action queue for the Discord bot (similar to ServerActionQueue)."""
    __tablename__ = "discord_bot_actions"

    id = Column(Integer, primary_key=True, index=True)
    action_type = Column(String(50), nullable=False)  # send_rating, send_message, kick_member, etc.
    payload = Column(JSON, default=lambda: {})
    queued_by_admin_id = Column(Integer, ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    queued_at = Column(DateTime, default=func.now())
    executed = Column(Integer, default=0)  # 0=pending, 1=done, 2=failed, 3=running
    executed_at = Column(DateTime, nullable=True)
    error_message = Column(String(500), nullable=True)
    # Structured response data for request/response-style actions (e.g.
    # "check_channel_category") — the backend polls this same row for a short
    # window after queuing, giving the frontend a near-synchronous answer
    # without the bot needing an inbound HTTP server of its own.
    result = Column(JSON, nullable=True)


class DiscordBotConfig(Base):
    """Bot configuration stored in DB (channels, settings)."""
    __tablename__ = "discord_bot_config"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class DiscordAntiRaidLog(Base):
    """Anti-raid event log."""
    __tablename__ = "discord_antiraid_log"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(50), nullable=False)  # mass_join, spam, suspicious_account
    discord_id = Column(String(25), nullable=True)
    username = Column(String(255), nullable=True)
    details = Column(JSON, nullable=True)
    occurred_at = Column(DateTime, default=func.now())
    resolved = Column(Integer, default=0)


class Ticket(Base):
    """Support ticket linked to a Discord channel."""
    __tablename__ = "tickets"
    __table_args__ = (
        Index(
            "ix_tickets_status_deleted_activity",
            "status",
            "deleted_at",
            "last_message_at",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    ticket_type = Column(String(50), nullable=False)  # help, player_report, bug, admin_report, department_report
    title = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="open", index=True)  # open, waiting, closed
    # Evidence state is deliberately separate from the workflow status.  A
    # Discord channel can disappear outside NexusData before its history and
    # attachments have been archived; that must remain an explicit exception,
    # never look like a successfully closed ticket.
    archive_state = Column(String(20), nullable=True, index=True)
    discord_channel_id = Column(String(25), nullable=True, index=True)
    creator_discord_id = Column(String(25), nullable=False)
    creator_username = Column(String(255), nullable=True)
    creator_display_name = Column(String(255), nullable=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=True, index=True)
    assigned_role_ids = Column(JSON, default=lambda: [])  # Discord role IDs with access
    assigned_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True, index=True)
    closed_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=func.now())
    closed_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True, index=True)
    deleted_by = Column(String(255), nullable=True)
    last_message_at = Column(DateTime, nullable=True)
    awaiting_admin = Column(Integer, default=1, index=True)  # 1 = waiting for admin reply


class TicketMessage(Base):
    """Message in a ticket (synced from Discord)."""
    __tablename__ = "ticket_messages"
    __table_args__ = (
        Index(
            "ix_ticket_messages_ticket_created_id",
            "ticket_id",
            "created_at",
            "id",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False, index=True)
    discord_message_id = Column(String(25), nullable=True)
    author_discord_id = Column(String(25), nullable=False)
    author_username = Column(String(255), nullable=True)
    author_display_name = Column(String(255), nullable=True)
    author_avatar_url = Column(String(500), nullable=True)
    content = Column(String(4000), nullable=True)
    attachments = Column(JSON, default=lambda: [])  # [{url, filename, content_type}]
    # Immutable snapshot for a Discord reply/forward. Native forwards keep
    # their content, embeds and locally archived media here because Discord's
    # outer message is normally empty and the source can later disappear.
    reference_snapshot = Column(JSON, nullable=True)
    is_bot = Column(Integer, default=0)  # 1 = sent from web panel via bot
    delivery_status = Column(String(20), nullable=False, default="received")
    delivery_error = Column(String(1000), nullable=True)
    created_at = Column(DateTime, default=func.now())


class TicketCooldown(Base):
    """Cooldown record for repeated ticket creation per user+ticket_type."""
    __tablename__ = "ticket_cooldowns"

    id = Column(Integer, primary_key=True, index=True)
    creator_discord_id = Column(String(25), nullable=False, index=True)
    ticket_type = Column(String(50), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=func.now())


class PlayerRating(Base):
    """Per-role rating for a player. Logarithmic scale with time decay."""
    __tablename__ = "player_ratings"

    id = Column(Integer, primary_key=True, index=True)
    steam_id = Column(String(20), nullable=False, index=True)
    role = Column(String(20), nullable=False, index=True)  # captain, mechanic, medic, security, engineer
    cumulative_points = Column(Float, nullable=False, default=0.0)
    current_rating = Column(Float, nullable=False, default=0.0)
    last_activity_date = Column(DateTime, nullable=True)
    last_decay_applied_at = Column(DateTime, nullable=True)
    # Persistent decay multiplier (0.3-1.0), survives full recalculation
    decay_factor = Column(Float, nullable=False, default=1.0)
    last_daily_decay_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('steam_id', 'role', name='uq_player_ratings_steam_role'),
    )


class RatingHistory(Base):
    """Audit log for rating changes."""
    __tablename__ = "rating_history"

    id = Column(Integer, primary_key=True, index=True)
    steam_id = Column(String(20), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    event_type = Column(String(30), nullable=False)  # session_update, penalty, decay
    points_delta = Column(Float, nullable=False)
    cumulative_points_before = Column(Float, nullable=False)
    cumulative_points_after = Column(Float, nullable=False)
    rating_before = Column(Float, nullable=False)
    rating_after = Column(Float, nullable=False)
    source_server = Column(String(20), nullable=True)
    session_trace_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)


class ServerRestartSchedule(Base):
    """Durable daily restart state for each production Barotrauma server."""

    __tablename__ = "server_restart_schedules"

    id = Column(Integer, primary_key=True, index=True)
    server_name = Column(String(20), unique=True, nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=False)
    daily_time = Column(String(5), nullable=False, default="05:30")
    message_text = Column(
        Text,
        nullable=False,
        default="Сервер будет перезапущен через 15 секунд.",
    )
    require_no_online = Column(Boolean, nullable=False, default=True)
    require_round_finished = Column(Boolean, nullable=False, default=True)

    # All timestamps are stored as naive UTC, matching the existing database
    # convention. The API adds the explicit Z suffix before returning them.
    scheduled_for = Column(DateTime, nullable=True, index=True)
    warning_sent_at = Column(DateTime, nullable=True)
    execution_due_at = Column(DateTime, nullable=True, index=True)
    processing_until = Column(DateTime, nullable=True, index=True)
    processing_token = Column(String(64), nullable=True)

    last_status = Column(String(30), nullable=False, default="disabled")
    last_action = Column(String(30), nullable=True)
    last_action_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    updated_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class LobbyAdvertisementConfig(Base):
    """Per-server settings for the one-shot lobby Discord advertisement."""

    __tablename__ = "lobby_advertisement_configs"

    id = Column(Integer, primary_key=True, index=True)
    server_name = Column(String(20), unique=True, nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=False)
    delay_seconds = Column(Integer, nullable=False, default=120)
    message_text = Column(
        Text,
        nullable=False,
        default="[Администрация]: Наш discord сервер: {discord_link} (прямая ссылка) — Вводить в браузере",
    )
    fallback_url = Column(String(255), nullable=False, default="ds-asper.ru")
    keywords = Column(JSON, nullable=False, default=lambda: ["устав", "правила", "дискорд", "сервер"])
    early_trigger_enabled = Column(Boolean, nullable=False, default=True)
    updated_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class LobbyAdvertisementCycle(Base):
    """Durable state for one round-end -> lobby advertisement cycle."""

    __tablename__ = "lobby_advertisement_cycles"

    id = Column(Integer, primary_key=True, index=True)
    server_name = Column(String(20), nullable=False, index=True)
    round_end_event_id = Column(Integer, ForeignKey("round_events.id", ondelete="SET NULL"), nullable=True, unique=True, index=True)
    due_at = Column(DateTime, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    trigger_reason = Column(String(30), nullable=True)
    discord_link = Column(String(500), nullable=True)
    discord_source = Column(String(30), nullable=True)
    last_error = Column(Text, nullable=True)
    attempted_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    skipped_at = Column(DateTime, nullable=True)
    processing_until = Column(DateTime, nullable=True, index=True)
    processing_token = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    round_end_event = relationship("RoundEvent", foreign_keys=[round_end_event_id])


class RatingEventLedger(Base):
    """Immutable v2 rating input accepted from an external game source."""
    __tablename__ = "rating_event_ledger"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(128), nullable=False, unique=True, index=True)
    version = Column(Integer, nullable=False)
    steam_id = Column(String(50), nullable=False, index=True)
    mode = Column(String(16), nullable=False, index=True)
    role = Column(String(32), nullable=False, index=True)
    event_type = Column(String(64), nullable=False)
    raw_value = Column(Float, nullable=False, default=0.0)
    points_delta = Column(Float, nullable=False)
    mapping_status = Column(String(16), nullable=False, default="mapped")
    formula_version = Column(String(64), nullable=False, default="rating-v2-ledger-v1")
    config_version = Column(String(64), nullable=False, default="default")
    occurred_at = Column(DateTime, nullable=False, index=True)
    source_context = Column(JSON, nullable=False)
    round_context = Column(JSON, nullable=False)
    event_metadata = Column("metadata", JSON, nullable=False, default=dict)
    # Stable semantic fingerprint used to reject accidental replays that carry
    # a fresh event_id. It is nullable for old/imported rows whose provenance
    # must remain untouched.
    dedup_key = Column(String(64), nullable=True, index=True)
    received_at = Column(DateTime, default=func.now(), nullable=False, index=True)
    projected_at = Column(DateTime, nullable=True, index=True)


class RatingProjectionState(Base):
    """Materialized v2 rating total for one player, mode, and role."""
    __tablename__ = "rating_projection_states"

    id = Column(Integer, primary_key=True, index=True)
    steam_id = Column(String(50), nullable=False, index=True)
    mode = Column(String(16), nullable=False, index=True)
    role = Column(String(32), nullable=False, index=True)
    cumulative_points = Column(Float, nullable=False, default=0.0)
    current_rating = Column(Float, nullable=False, default=0.0)
    last_event_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    action_counts = Column(JSON, nullable=False, default=dict)
    round_tokens = Column(JSON, nullable=False, default=list)
    qualified_rounds = Column(Integer, nullable=False, default=0)
    unmapped_events = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("steam_id", "mode", "role", name="uq_rating_projection_state_key"),
    )


class RatingRankHistory(Base):
    """Periodic leaderboard snapshots used to explain position changes."""
    __tablename__ = "rating_rank_history"

    id = Column(Integer, primary_key=True, index=True)
    steam_id = Column(String(50), nullable=False, index=True)
    mode = Column(String(16), nullable=False, index=True)
    role = Column(String(32), nullable=False, index=True)
    rank = Column(Integer, nullable=False)
    rating = Column(Float, nullable=False)
    cumulative_points = Column(Float, nullable=False)
    captured_at = Column(DateTime, default=func.now(), nullable=False, index=True)


class RatingCalculationRun(Base):
    """Reproducibility record for a projection/rebuild calculation."""
    __tablename__ = "rating_calculation_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(64), nullable=False, unique=True, index=True)
    mode = Column(String(16), nullable=False, index=True)
    formula_version = Column(String(64), nullable=False)
    config_version = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="completed")
    event_count = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime, default=func.now(), nullable=False)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(String(1000), nullable=True)


class RatingConfigVersion(Base):
    """Append-only configuration history; saving never destroys a prior config."""
    __tablename__ = "rating_config_versions"

    id = Column(Integer, primary_key=True, index=True)
    mode = Column(String(16), nullable=False, index=True)
    version = Column(String(64), nullable=False, index=True)
    config = Column(JSON, nullable=False)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class RatingAdjustment(Base):
    """Auditable manual adjustment, kept outside raw game events."""
    __tablename__ = "rating_adjustments"

    id = Column(Integer, primary_key=True, index=True)
    steam_id = Column(String(50), nullable=False, index=True)
    mode = Column(String(16), nullable=False, index=True)
    role = Column(String(32), nullable=False, index=True)
    delta = Column(Float, nullable=False)
    reason = Column(String(1000), nullable=False)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class RatingDeliveryLog(Base):
    """Per-event ingest status for delivery latency and rejection diagnostics."""
    __tablename__ = "rating_delivery_log"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(128), nullable=True, index=True)
    mode = Column(String(16), nullable=True, index=True)
    role = Column(String(32), nullable=True, index=True)
    status = Column(String(16), nullable=False, index=True)
    reason = Column(String(1000), nullable=True)
    received_at = Column(DateTime, default=func.now(), nullable=False, index=True)


class RatingRewardHolder(Base):
    """Текущий держатель награды за место в рейтинге — по одной строке на
    (rule_id, place). Это то, чего не хватало старой системе: без записи
    "кому и что выдано за это место" реконсиляция не может ни отличить
    "уже выдано в этом цикле" от "выдать заново", ни понять, у кого
    награду нужно забрать, когда место в рейтинге сменится.

    rule_id — стабильный идентификатор правила из data/rating_rewards.json
    (не role, т.к. на одну профессию может быть несколько правил с разным
    расписанием — см. services_playtime_stats.load_rating_rewards).
    """
    __tablename__ = "rating_reward_holders"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(String(64), nullable=False, index=True)
    place = Column(Integer, nullable=False)
    steam_id = Column(String(50), nullable=True, index=True)  # null = место никем не занято на момент выдачи
    reward_group_id = Column(String(64), nullable=True)  # какая именно группа выдана — для точного revoke
    granted_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('rule_id', 'place', name='uq_rating_reward_holder_rule_place'),
    )


class RatingRewardScheduleState(Base):
    """Когда правило награды по рейтингу последний раз реально сработало по
    расписанию (день недели + время МСК) — без этого воркер, проверяющий
    расписание каждые несколько минут, не смог бы отличить "ещё не наступило
    назначенное время" от "уже сработало в этом цикле, не срабатывать снова
    в том же окне". cycle_key — ISO-неделя (напр. "2026-W33"), в которую
    правило уже было применено."""
    __tablename__ = "rating_reward_schedule_state"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(String(64), unique=True, nullable=False, index=True)
    last_cycle_key = Column(String(16), nullable=True)
    last_run_at = Column(DateTime, nullable=True)


class SubProjectCategory(Base):
    """Flexible category system for sub-projects (manageable by superusers/assigned roles)."""
    __tablename__ = "sub_project_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    slug = Column(String(128), unique=True, nullable=False)
    description = Column(String(500), nullable=True)
    color = Column(String(16), default="#1a68de")
    icon = Column(String(16), default="🚢")
    sort_order = Column(Integer, default=0)
    parent_id = Column(Integer, ForeignKey("sub_project_categories.id"), nullable=True)
    # Auto-assigned to every new NORMAL-zone project on create (routes/
    # sub_projects.py's api_sub_project_create). Only one category should
    # ever be True at a time — enforced in services_sub_projects.py.
    is_default = Column(Boolean, nullable=False, default=False)
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    children = relationship("SubProjectCategory", backref="parent", remote_side=[id], foreign_keys=[parent_id])


class SubProjectCategoryLink(Base):
    """Many-to-many link between sub-projects and categories."""
    __tablename__ = "sub_project_category_links"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("sub_projects.id"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("sub_project_categories.id"), nullable=False, index=True)
    assigned_at = Column(DateTime, default=func.now())

    __table_args__ = (
        UniqueConstraint('project_id', 'category_id', name='uq_spcl_project_cat'),
    )


class SubProject(Base):
    """Sub-map project (submarine/ship) with metadata."""
    __tablename__ = "sub_projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    project_type = Column(String(100), nullable=True)  # Корабль, Станция, etc.
    crew = Column(String(100), nullable=True)  # e.g. "7-10-12"
    weapons = Column(String(1000), nullable=True)
    has_reactor = Column(String(10), nullable=True)  # Да/Нет
    fabricator_medfab = Column(String(50), nullable=True)  # Да/Да, Нет/Нет, etc.
    has_deconstructor = Column(String(10), nullable=True)
    docking_ports = Column(String(200), nullable=True)
    decks = Column(String(50), nullable=True)
    max_speed = Column(String(50), nullable=True)
    extra_equipment = Column(String(1000), nullable=True)
    features = Column(String(4000), nullable=True)  # Особенности
    description = Column(String(4000), nullable=True)  # Краткое описание
    author = Column(String(255), nullable=False)
    image_filename = Column(String(500), nullable=True)  # uploaded image file
    # Discord embed tracking
    discord_message_id = Column(String(64), nullable=True)
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    # NULL = normal zone (site-wide sub_projects permissions). Set = a
    # "Тестовый полигон ASPER" project belonging to that department — access
    # then runs ENTIRELY through the department's own "test_server" module
    # permission (check_department_permission), never through the site-wide
    # sub_projects permission registry.
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    # Soft-delete
    is_deleted = Column(Integer, default=0)  # 0=active, 1=deleted
    deleted_at = Column(DateTime, nullable=True)
    # Manual drag-and-drop ordering on the Sub-projects grid (routes/
    # sub_projects.py's reorder endpoint) — lower sorts first.
    sort_order = Column(Integer, nullable=False, default=0)

    versions = relationship("SubProjectVersion", back_populates="project", order_by="SubProjectVersion.version_number.desc()")
    department = relationship("Department")
    category_links = relationship("SubProjectCategoryLink", backref="project", cascade="all, delete-orphan")


class SubProjectVersion(Base):
    """Version of a .sub file for a project."""
    __tablename__ = "sub_project_versions"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("sub_projects.id"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    filename = Column(String(500), nullable=False)  # original filename
    stored_filename = Column(String(500), nullable=False)  # filename on disk
    file_size = Column(Integer, nullable=True)
    changelog = Column(String(2000), nullable=True)
    # Deployment status per server
    deployed_server_1 = Column(Integer, default=0)  # 0=not deployed, 1=deployed
    deployed_server_2 = Column(Integer, default=0)
    uploaded_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    uploaded_at = Column(DateTime, default=func.now())
    # Soft-delete fields (policy: maximum data availability)
    is_deleted = Column(Integer, default=0)  # 0=active, 1=soft-deleted
    deleted_at = Column(DateTime, nullable=True)
    deleted_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)

    project = relationship("SubProject", back_populates="versions")


class PlayerPunishment(Base):
    """Unified punishment record: warning, reprimand, kick, ban."""
    __tablename__ = "player_punishments"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=False, index=True)
    player_steamid = Column(String(50), nullable=True, index=True)
    player_ip = Column(String(64), nullable=True)          # IP at time of punishment
    type = Column(String(20), nullable=False, index=True)  # warning / reprimand / kick / ban
    reason = Column(String(1000), nullable=True)
    issued_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    issued_by_name = Column(String(255), nullable=True)    # denormalised for history
    # Whether the issuing admin was a superuser AT THE TIME of issuing — snapshotted
    # here since is_superuser can change later and shouldn't rewrite history.
    issued_by_was_superuser = Column(Boolean, nullable=False, default=False)
    issued_at = Column(DateTime, default=func.now(), index=True)
    expires_at = Column(DateTime, nullable=True)           # NULL = permanent
    is_active = Column(Integer, default=1, index=True)      # 1 = active, 0 = void/overridden
    notes = Column(String(2000), nullable=True)            # internal admin notes
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class PlayerPunishmentDiscordMessage(Base):
    """Durable link between one web punishment and one Discord embed."""
    __tablename__ = "player_punishment_discord_messages"
    __table_args__ = (
        UniqueConstraint("punishment_id", "channel_id", name="uq_punishment_discord_channel"),
        Index("ix_punishment_discord_message", "channel_id", "message_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    punishment_id = Column(Integer, ForeignKey("player_punishments.id", ondelete="CASCADE"), nullable=False, index=True)
    channel_id = Column(String(50), nullable=False)
    message_id = Column(String(64), nullable=False)
    state_hash = Column(String(64), nullable=True)
    sync_status = Column(String(16), nullable=False, default="synced")  # synced | error | missing
    last_attempt_at = Column(DateTime, nullable=True)
    synced_at = Column(DateTime, nullable=True)
    last_error = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class DispatchMessage(Base):
    """Radio dispatch messages (game terminal ↔ web dispatcher)."""
    __tablename__ = "dispatch_messages"

    id = Column(Integer, primary_key=True, index=True)
    steamid = Column(String(50), nullable=True, index=True)
    player_name = Column(String(255), nullable=True)
    text = Column(String(2000), nullable=False)
    sender_type = Column(String(20), nullable=False, default="player")  # player | dispatcher
    server_name = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)


class RoundEvent(Base):
    """Round start/end events from game servers."""
    __tablename__ = "round_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(20), nullable=False, index=True)  # start | end
    server_name = Column(String(100), nullable=True, index=True)
    submarine_name = Column(String(255), nullable=True)
    mission = Column(String(500), nullable=True)
    location = Column(String(255), nullable=True)
    mission_completed = Column(Integer, nullable=True)
    reached_station = Column(Integer, nullable=True)
    players = Column(JSON, default=lambda: [])
    created_at = Column(DateTime, default=func.now(), index=True)
    # Set on "end" events only — points back at the "start" event that opened
    # this round, so a finished round can be looked up in one query instead
    # of pairing start/end rows in Python on every request.
    paired_start_id = Column(Integer, ForeignKey("round_events.id"), nullable=True)
    paired_start = relationship("RoundEvent", remote_side=[id], foreign_keys=[paired_start_id])
    # Stable identifiers supplied by the game-server plugin.  Timestamp-only
    # pairing is not safe when a lobby/round transition is retried or repeated.
    server_session_id = Column(String(64), nullable=True, index=True)
    round_token = Column(String(64), nullable=True, index=True)


class RoundReplay(Base):
    """Uploaded .barodemo round replay file (stored compressed as .barodemo.zip)."""
    __tablename__ = "round_replays"

    id = Column(Integer, primary_key=True, index=True)
    server_name = Column(String(100), nullable=True, index=True)
    submarine_name = Column(String(255), nullable=True)
    level_name = Column(String(255), nullable=True)
    filename = Column(String(500), nullable=False)        # original filename
    stored_filename = Column(String(500), nullable=False) # filename on disk (.barodemo.zip)
    file_size = Column(Integer, nullable=True)            # size in bytes
    duration_ms = Column(Integer, nullable=True)          # round duration if known
    has_voice = Column(Integer, default=0)                # 1 = contains voice
    recorded_at = Column(DateTime, nullable=True)         # round start time
    uploaded_at = Column(DateTime, default=func.now(), index=True)
    notes = Column(String(1000), nullable=True)
    # Stream lifecycle is durable so a process restart does not turn an
    # interrupted live demo into an invisible orphan.
    stream_id = Column(String(100), nullable=True, unique=True, index=True)
    is_broken = Column(Boolean, nullable=False, default=False, index=True)
    broken_at = Column(DateTime, nullable=True)
    replay_kind = Column(String(16), nullable=False, default="round", index=True)
    after_round_stream_id = Column(String(100), nullable=True, index=True)
    dump_action_id = Column(Integer, nullable=True, index=True)
    dumped_by_admin_id = Column(Integer, nullable=True, index=True)
    dumped_by_name = Column(String(255), nullable=True)
    # NewWorldStreamer archives are version-gated independently from the old
    # .barodemo pipeline.  The worker only accepts nwdemo-v2 rows here.
    replay_format = Column(String(32), nullable=True, index=True)
    voice_profile_state = Column(String(20), nullable=True, index=True)
    voice_profile_error = Column(String(2000), nullable=True)
    voice_profile_processed_at = Column(DateTime, nullable=True)
    # Manual escape hatch when the automatic header-timestamp/server_name
    # matcher (routes/replays.py's _find_matching_replay) can't find this
    # replay's round — set once by an admin, always wins over the heuristic.
    manual_round_event_id = Column(Integer, ForeignKey("round_events.id", ondelete="SET NULL"), nullable=True)


class VoiceProfileJob(Base):
    """Durable backfill/worker run for replay-independent voice profiles."""

    __tablename__ = "voice_profile_jobs"

    id = Column(Integer, primary_key=True, index=True)
    mode = Column(String(20), nullable=False, default="backfill")
    status = Column(String(20), nullable=False, default="queued", index=True)
    replay_ids = Column(JSON, nullable=True)
    current_replay_id = Column(Integer, nullable=True, index=True)
    total_replays = Column(Integer, nullable=False, default=0)
    processed_replays = Column(Integer, nullable=False, default=0)
    mapped_speakers = Column(Integer, nullable=False, default=0)
    unresolved_speakers = Column(Integer, nullable=False, default=0)
    materialized_clips = Column(Integer, nullable=False, default=0)
    attempt_count = Column(Integer, nullable=False, default=0)
    error_message = Column(String(2000), nullable=True)
    heartbeat_at = Column(DateTime, nullable=True, index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False, index=True)


class VoiceProfileImport(Base):
    """A staged local corpus waiting for validation or idempotent commit."""

    __tablename__ = "voice_profile_imports"

    id = Column(Integer, primary_key=True, index=True)
    import_key = Column(String(120), nullable=False, unique=True, index=True)
    status = Column(String(20), nullable=False, default="staged", index=True)
    staging_path = Column(String(500), nullable=False)
    manifest_sha256 = Column(String(64), nullable=False)
    total_profiles = Column(Integer, nullable=False, default=0)
    total_clips = Column(Integer, nullable=False, default=0)
    imported_profiles = Column(Integer, nullable=False, default=0)
    imported_clips = Column(Integer, nullable=False, default=0)
    error_message = Column(String(2000), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class ReplayVoiceSpeaker(Base):
    """One local speaker ID and its current identity resolution state."""

    __tablename__ = "replay_voice_speakers"

    id = Column(Integer, primary_key=True, index=True)
    replay_id = Column(Integer, ForeignKey("round_replays.id", ondelete="CASCADE"), nullable=False, index=True)
    speaker_id = Column(Integer, nullable=False)
    names = Column(JSON, nullable=False, default=lambda: [])
    candidate_profiles = Column(JSON, nullable=False, default=lambda: [])
    steam_id = Column(String(50), nullable=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id", ondelete="SET NULL"), nullable=True, index=True)
    resolution_status = Column(String(20), nullable=False, default="unresolved", index=True)
    mapping_source = Column(String(32), nullable=True)
    packet_count = Column(Integer, nullable=False, default=0)
    radio_packet_count = Column(Integer, nullable=False, default=0)
    start_ms = Column(Integer, nullable=True)
    end_ms = Column(Integer, nullable=True)
    decided_by_admin_id = Column(Integer, ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    replay = relationship("RoundReplay")

    __table_args__ = (
        UniqueConstraint("replay_id", "speaker_id", name="uq_replay_voice_speaker"),
        Index("ix_replay_voice_speakers_replay_status", "replay_id", "resolution_status"),
    )


class ReplayVoiceSegment(Base):
    """A continuous voice segment discovered in one replay."""

    __tablename__ = "replay_voice_segments"

    id = Column(Integer, primary_key=True, index=True)
    replay_id = Column(Integer, ForeignKey("round_replays.id", ondelete="CASCADE"), nullable=False, index=True)
    speaker_id = Column(Integer, nullable=False)
    steam_id = Column(String(50), nullable=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id", ondelete="SET NULL"), nullable=True, index=True)
    start_ms = Column(Integer, nullable=False)
    end_ms = Column(Integer, nullable=False)
    packet_count = Column(Integer, nullable=False, default=0)
    radio_packet_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    replay = relationship("RoundReplay")

    __table_args__ = (
        UniqueConstraint("replay_id", "speaker_id", "start_ms", "end_ms", name="uq_replay_voice_segment"),
        Index("ix_replay_voice_segments_steam_time", "steam_id", "start_ms"),
    )


class VoiceProfileClip(Base):
    """A durable materialized clip whose source replay may later be deleted."""

    __tablename__ = "voice_profile_clips"

    id = Column(Integer, primary_key=True, index=True)
    archive_key = Column(String(64), nullable=False, unique=True, index=True)
    steam_id = Column(String(50), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id", ondelete="SET NULL"), nullable=True, index=True)
    source_replay_id = Column(Integer, nullable=True, index=True)
    source_replay_filename = Column(String(500), nullable=False)
    source_replay_hash = Column(String(64), nullable=False)
    # Legacy corpus rows still have a local speaker ID. nwdemo-v2 profiles do
    # not: their only identity is the Steam64 recorded per voice packet.
    speaker_id = Column(Integer, nullable=True)
    start_ms = Column(Integer, nullable=False)
    end_ms = Column(Integer, nullable=False)
    packet_count = Column(Integer, nullable=False, default=0)
    radio_packet_count = Column(Integer, nullable=False, default=0)
    ndvc_filename = Column(String(500), nullable=True, unique=True)
    wav_filename = Column(String(500), nullable=True, unique=True)
    storage_format = Column(String(32), nullable=False, default="ndvc")
    storage_size = Column(Integer, nullable=False, default=0)
    archive_version = Column(Integer, nullable=False, default=1)
    source_format = Column(String(32), nullable=True, index=True)
    source_kind = Column(String(16), nullable=True, index=True)
    is_radio = Column(Boolean, nullable=False, default=False, index=True)
    recorded_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    player = relationship("PlayerData", foreign_keys=[player_id])

    __table_args__ = (
        Index("ix_voice_profile_clips_steam_time", "steam_id", "recorded_at", "start_ms"),
        Index("ix_voice_profile_clips_source", "source_replay_id", "speaker_id"),
    )


class VoiceProfileSelection(Base):
    """Current early/middle/latest slots for one stable Steam/EOS identity."""

    __tablename__ = "voice_profile_selections"

    id = Column(Integer, primary_key=True, index=True)
    steam_id = Column(String(50), nullable=False, index=True)
    slot = Column(Integer, nullable=False)
    band = Column(String(16), nullable=False)
    clip_id = Column(Integer, ForeignKey("voice_profile_clips.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    clip = relationship("VoiceProfileClip")

    __table_args__ = (
        UniqueConstraint("steam_id", "slot", name="uq_voice_profile_selection_slot"),
        UniqueConstraint("steam_id", "clip_id", name="uq_voice_profile_selection_clip"),
        Index("ix_voice_profile_selections_steam_slot", "steam_id", "slot"),
    )


class VoiceReplayFingerprint(Base):
    """One non-identifying acoustic feature vector per replay and Steam64."""

    __tablename__ = "voice_replay_fingerprints"

    id = Column(Integer, primary_key=True, index=True)
    replay_id = Column(Integer, nullable=False, index=True)
    steam_id = Column(String(50), nullable=False, index=True)
    algorithm = Column(String(32), nullable=False)
    source_channel = Column(String(16), nullable=False)
    vector = Column(JSON, nullable=False)
    quality = Column(String(16), nullable=False, default="low")
    duration_ms = Column(Integer, nullable=False, default=0)
    packet_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("replay_id", "steam_id", "algorithm", name="uq_voice_replay_fingerprint"),
        Index("ix_voice_replay_fingerprint_steam_algorithm", "steam_id", "algorithm", "source_channel"),
    )


class VoiceProfileSignature(Base):
    """Aggregate of quality-approved replay fingerprints for later review."""

    __tablename__ = "voice_profile_signatures"

    id = Column(Integer, primary_key=True, index=True)
    steam_id = Column(String(50), nullable=False, index=True)
    algorithm = Column(String(32), nullable=False)
    source_channel = Column(String(16), nullable=False)
    vector = Column(JSON, nullable=False)
    sample_count = Column(Integer, nullable=False, default=0)
    weighted_duration_ms = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("steam_id", "algorithm", "source_channel", name="uq_voice_profile_signature"),
    )


class PlayerCommunication(Base):
    """A chat message or one final voice utterance from a game server."""
    __tablename__ = "player_communications"

    id = Column(Integer, primary_key=True, index=True)
    event_uid = Column(String(96), nullable=False, unique=True, index=True)
    source = Column(String(16), nullable=False, index=True)  # chat | voice
    channel = Column(String(16), nullable=False, index=True)
    server_name = Column(String(100), nullable=False, index=True)
    server_session_id = Column(String(64), nullable=False, index=True)
    round_token = Column(String(64), nullable=True, index=True)
    round_event_id = Column(Integer, ForeignKey("round_events.id", ondelete="SET NULL"), nullable=True, index=True)
    player_steamid = Column(String(50), nullable=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id", ondelete="SET NULL"), nullable=True, index=True)
    player_name = Column(String(255), nullable=True, index=True)
    role = Column(String(100), nullable=True)
    submarine_name = Column(String(255), nullable=True)
    hull_id = Column(String(100), nullable=True, index=True)
    hull_name = Column(String(255), nullable=True)
    position_x = Column(Float, nullable=True)
    position_y = Column(Float, nullable=True)
    original_text = Column(Text, nullable=True)
    transcript_text = Column(Text, nullable=True)
    asr_status = Column(String(20), nullable=False, default="not_applicable", index=True)
    asr_confidence = Column(Float, nullable=True)
    asr_duration_ms = Column(Integer, nullable=True)
    asr_error = Column(String(1000), nullable=True)
    occurred_at = Column(DateTime, nullable=False, index=True)
    ended_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    recipients = relationship(
        "PlayerCommunicationRecipient",
        back_populates="communication",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    round_event = relationship("RoundEvent", foreign_keys=[round_event_id])


class PlayerCommunicationRecipient(Base):
    """Aggregated server-level delivery observation for one recipient."""
    __tablename__ = "player_communication_recipients"

    id = Column(Integer, primary_key=True, index=True)
    communication_id = Column(
        Integer,
        ForeignKey("player_communications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    player_steamid = Column(String(50), nullable=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id", ondelete="SET NULL"), nullable=True, index=True)
    player_name = Column(String(255), nullable=True)
    delivered_text = Column(Text, nullable=True)
    server_queued = Column(Boolean, nullable=False, default=True)
    delivery_allowed = Column(Boolean, nullable=False, default=True)
    channel = Column(String(16), nullable=True)
    is_radio = Column(Boolean, nullable=False, default=False)
    is_dead = Column(Boolean, nullable=False, default=False)
    distance_factor = Column(Float, nullable=True)
    packet_count = Column(Integer, nullable=False, default=1)
    first_observed_at = Column(DateTime, nullable=True)
    last_observed_at = Column(DateTime, nullable=True)

    communication = relationship("PlayerCommunication", back_populates="recipients")

    __table_args__ = (
        UniqueConstraint("communication_id", "player_steamid", name="uq_comm_recipient_player"),
        Index("ix_comm_recipient_communication_player", "communication_id", "player_id"),
    )


class RespawnDoll(Base):
    """«Кукла» — заранее подготовленный персонаж для волны респавна.

    Плагин RespawnDolls на игровом сервере работает ДЕКЛАРАТИВНО: раз в ~10 с
    он запрашивает ПОЛНЫЙ список актуальных кукол (GET /game/dolls/pending) и
    приводит своё состояние к нему. Отмена — это просто прекращение выдачи
    куклы в этом списке, отдельной команды «удалить» не существует. Поэтому
    строка живёт дольше самой куклы: отменённые/заспавненные не удаляются, а
    меняют state и перестают попадать в выдачу — история остаётся в админке.
    """
    __tablename__ = "respawn_dolls"

    id = Column(Integer, primary_key=True, index=True)
    # Уходит в JSON как "id" — по нему идёт вся синхронизация и отчёты плагина.
    public_id = Column(String(64), unique=True, nullable=False, index=True)
    server = Column(String(32), nullable=False, index=True)   # ASPER1 | ASPER2
    label = Column(String(200), nullable=True)
    # Тело куклы: identity / job / progression / stats / health / inventory.
    # Хранится как есть, схема плагина шире, чем стоит раскладывать по колонкам.
    spec = Column(JSON, nullable=False, default=dict)

    priority = Column(Integer, nullable=False, default=0)
    one_shot = Column(Boolean, nullable=False, default=True)
    expires_at = Column(DateTime, nullable=True)
    bind_steamids = Column(JSON, default=lambda: [])
    spawn_mode = Column(String(16), nullable=False, default="auto")  # auto|player|bot
    team = Column(Integer, nullable=False, default=0)

    # pending | cancelled | spawned | spawned_with_errors | failed | expired
    state = Column(String(32), nullable=False, default="pending", index=True)
    state_message = Column(String(1000), nullable=True)
    spawned_at = Column(DateTime, nullable=True)
    spawned_steamid = Column(String(32), nullable=True)
    spawned_character_name = Column(String(100), nullable=True)

    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    created_by = relationship("AdminUser", foreign_keys=[created_by_admin_id])


class AchievementDef(Base):
    """Определение ачивки — конструктор из цепочки событий + условий + наград.

    Модель работы аналогична RespawnDoll: сайт — источник правды, плагин
    (Achievements.cs) раз в PollIntervalSeconds забирает ПОЛНЫЙ список
    активных (enabled=True) определений и держит его локально (SQLite),
    сам считает прогресс по игровым событиям и сам выдаёт награды — сеть
    участвует только в синхронизации определений и репорте фактов анлока
    (см. ACHIEVEMENTS_API_GUIDE.md). Строка не привязана к конкретному
    серверу: `servers` (пусто = все) фильтрует, где ачивка активна.
    """
    __tablename__ = "achievement_defs"

    id = Column(Integer, primary_key=True, index=True)
    # Стабильный идентификатор — по нему идёт вся синхронизация, прогресс и
    # цепочки (requires). Меняется только вручную; переименование "рвёт"
    # прогресс игроков (тот привязан к key, не к id) — умышленно просто,
    # без миграции старого прогресса на новый key.
    key = Column(String(64), unique=True, nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(String(1000), nullable=True)
    icon = Column(String(16), nullable=True)  # эмодзи
    category = Column(String(64), nullable=True, index=True)  # группировка в админке/на сайте
    enabled = Column(Boolean, nullable=False, default=True)
    # Скрытая ачивка — не показывается игроку/в публичных списках, пока не
    # разблокирована (эффект чисто на стороне отображения, не на движке).
    hidden = Column(Boolean, nullable=False, default=False)
    # persistent — прогресс копится всегда; round — счётчик обнуляется в
    # начале каждого раунда (для ачивок вида "за один рейс").
    scope = Column(String(16), nullable=False, default="persistent")
    # Пусто — активна на всех серверах; иначе список имён (ASPER1, ASPER2…).
    servers = Column(JSON, nullable=False, default=list)

    # Цепочка событий: {"event": "...", "conditions": {...}, "count": N,
    # "distinct_by": "field"} — полная схема в ACHIEVEMENTS_API_GUIDE.md.
    trigger = Column(JSON, nullable=False)
    # [{"type": "item"|"stat"|"skill"|"talent", ...}, ...]
    rewards = Column(JSON, nullable=False, default=list)
    # Ключи ачивок-предпосылок — эта ачивка не отслеживается плагином, пока
    # все перечисленные не разблокированы этим же игроком. Строит цепочки/
    # деревья без отдельного рантайм-механизма (chain_group ниже — только
    # UI-подсказка при создании следующей ступени, тот же requires).
    requires = Column(JSON, nullable=False, default=list)
    chain_group = Column(String(64), nullable=True, index=True)
    chain_order = Column(Integer, nullable=False, default=0)
    sort_order = Column(Integer, nullable=False, default=0)
    # Слать ли игроку ЛС от бота при разблокировке (см. game_router.report).
    notify_discord = Column(Boolean, nullable=False, default=True)

    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    created_by = relationship("AdminUser", foreign_keys=[created_by_admin_id])


class PlayerAchievementProgress(Base):
    """Прогресс/разблокировка одной ачивки одним игроком.

    Обновляется ТОЛЬКО через POST /api/v1/game/achievements/report (батчем,
    идемпотентно — плагин сам решает, когда прогресс "существенно" изменился
    и стоит репортить) либо ручной выдачей/сбросом из админки. `unlocked`
    — терминальное состояние: реальный прогресс (retrigger после сброса
    ачивки на сайте) начинается заново только если админ явно снял флаг.
    """
    __tablename__ = "player_achievement_progress"

    id = Column(Integer, primary_key=True, index=True)
    player_steamid = Column(String(50), nullable=False, index=True)
    achievement_key = Column(String(64), nullable=False, index=True)

    progress = Column(Integer, nullable=False, default=0)
    # Снимок trigger.count на момент последнего репорта — чтобы прогресс-бар
    # не "убегал", если админ поменяет count в определении уже после того,
    # как игрок начал копить прогресс.
    target = Column(Integer, nullable=False, default=1)
    # Для distinct_by-ачивок — список уже встреченных различных значений
    # (напр. видов монстров), чтобы плагин(перезапуск)/сайт не задваивали счёт.
    distinct_values = Column(JSON, nullable=False, default=list)

    unlocked = Column(Boolean, nullable=False, default=False)
    unlocked_at = Column(DateTime, nullable=True)
    server_name = Column(String(32), nullable=True)
    # Выдана вручную из админки (мимо обычной логики триггеров) — влияет
    # только на отображение (бейдж "выдано вручную"), плагин это поле не читает.
    granted_manually = Column(Boolean, nullable=False, default=False)
    discord_notified = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("player_steamid", "achievement_key", name="uq_player_achievement"),
    )


class SoundFile(Base):
    """Звуковой файл (.wav/.mp3) для ивентов, залитый на игровой сервер по SFTP.

    Строка живёт ДОЛЬШЕ файла на удалённом сервере: при удалении звука с
    сервера запись остаётся с is_deleted_remotely=True, а локальная сжатая
    копия сохраняется — благодаря этому звук можно восстановить обратно.
    Уникальность (server_name, filename) поддерживается на уровне приложения:
    повторная загрузка того же имени обновляет существующую запись.
    """
    __tablename__ = "sound_files"

    id = Column(Integer, primary_key=True, index=True)
    server_name = Column(String(50), nullable=False, index=True)   # ASPER1 | ASPER2
    filename = Column(String(255), nullable=False, index=True)
    size_bytes = Column(Integer, nullable=False, default=0)
    # Имя файла локальной резервной копии (gzip) внутри sounds_backup_dir.
    # Хранится отдельно от filename: имя на сервере можно переименовать, а
    # копия при этом остаётся той же самой.
    backup_filename = Column(String(255), nullable=True)
    uploaded_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    uploaded_at = Column(DateTime, default=func.now())
    # Файл удалён с удалённого сервера, но локальная копия цела — такие
    # записи показываются в разделе «Удалённые» и доступны для восстановления.
    is_deleted_remotely = Column(Boolean, nullable=False, default=False, index=True)
    deleted_at = Column(DateTime, nullable=True)
    deleted_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)

    uploaded_by = relationship("AdminUser", foreign_keys=[uploaded_by_admin_id])
    deleted_by = relationship("AdminUser", foreign_keys=[deleted_by_admin_id])


class AppSetting(Base):
    """Generic runtime key-value settings editable from the admin panel."""
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(String(2000), nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class AIPlayerDossier(Base):
    """Latest AI dossier and its durable generation state for one player."""
    __tablename__ = "ai_player_dossiers"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=False, unique=True, index=True)
    status = Column(String(20), nullable=False, default="queued", index=True)
    result_json = Column(JSON, nullable=True)
    summary_text = Column(Text, nullable=True)
    input_hash = Column(String(64), nullable=True, index=True)
    model = Column(String(255), nullable=True)
    requested_at = Column(DateTime, default=func.now(), nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    generated_at = Column(DateTime, nullable=True, index=True)
    finished_at = Column(DateTime, nullable=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    error_message = Column(String(2000), nullable=True)
    requested_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)


class AIPlayerDossierRun(Base):
    """Metadata for one AI run plus superuser-only bounded debug output."""
    __tablename__ = "ai_player_dossier_runs"

    id = Column(Integer, primary_key=True, index=True)
    dossier_id = Column(Integer, ForeignKey("ai_player_dossiers.id"), nullable=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=True, index=True)
    source = Column(String(20), nullable=False, index=True)  # manual | daily | test | health
    status = Column(String(20), nullable=False, default="queued", index=True)
    requested_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    requested_at = Column(DateTime, default=func.now(), nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    model = Column(String(255), nullable=True)
    input_hash = Column(String(64), nullable=True, index=True)
    error_message = Column(String(2000), nullable=True)
    raw_response = Column(Text, nullable=True)


class OnlineSnapshot(Base):
    """Snapshot of online player count, recorded each time game server reports players."""
    __tablename__ = "online_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    server_name = Column(String(50), nullable=False, index=True)
    player_count = Column(Integer, nullable=False, default=0)
    player_ids = Column(JSON, nullable=True)  # list of steamids
    recorded_at = Column(DateTime, default=func.now(), index=True)


class ServerLiveState(Base):
    """Current live state per game server (submarine health, supply shuttle ETA).

    One row per server_name, upserted on every /server/players report — unlike
    OnlineSnapshot (historical log) this only ever holds the latest known values.
    """
    __tablename__ = "server_live_state"

    id = Column(Integer, primary_key=True, index=True)
    server_name = Column(String(50), unique=True, nullable=False, index=True)
    submarine_health = Column(Integer, nullable=True)  # 0-100
    supply_shuttle_eta_seconds = Column(Integer, nullable=True)  # as last reported
    shuttle_arrival_at = Column(DateTime, nullable=True)  # computed absolute time
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class AdminNotification(Base):
    """Personal notification for an admin user."""
    __tablename__ = "admin_notifications"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False, index=True)
    type = Column(String(50), nullable=False, default="info")  # info|success|warning|error
    category = Column(String(50), nullable=False, default="general")
    title = Column(String(255), nullable=False)
    body = Column(String(1000), nullable=True)
    link = Column(String(500), nullable=True)
    icon = Column(String(16), nullable=True)
    is_read = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now(), index=True)
    expires_at = Column(DateTime, nullable=True)


# ══════════════════════════════════════════════════════════════════════════════
# DOCUMENT EDITOR (shared Google-Docs-like document library)
# ══════════════════════════════════════════════════════════════════════════════

class DocumentFolder(Base):
    """Personal folder for organizing documents. Not shared — sharing is per-document."""
    __tablename__ = "document_folders"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    parent_id = Column(Integer, ForeignKey("document_folders.id", ondelete="SET NULL"), nullable=True, index=True)
    owner_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=func.now())

    owner = relationship("AdminUser", foreign_keys=[owner_admin_id])


class Document(Base):
    """A single document. Content is a real Office file (.docx/.xlsx) edited
    via ONLYOFFICE Document Server — `storage_filename` names the file under
    `{documents_upload_dir}/files/`. Old versions are never deleted on
    overwrite (see routes/onlyoffice.py's callback handler), only superseded,
    so DocumentRevision rows can still serve them."""
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False, default="Без названия")
    icon = Column(String(16), nullable=True)  # emoji
    file_type = Column(String(10), nullable=False, default="word")  # "word" | "cell"
    storage_filename = Column(String(255), nullable=True)
    folder_id = Column(Integer, ForeignKey("document_folders.id", ondelete="SET NULL"), nullable=True, index=True)
    # Page nesting: a document can live "inside" another document as a
    # sub-page, independent of folder placement.
    parent_document_id = Column(Integer, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True)
    # When set, this document belongs to a department's shared documentation
    # space — access is resolved via check_department_permission("documentation")
    # in addition to (not instead of) the normal owner/DocumentPermission checks.
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=True, index=True)
    # Optional department folder (separate namespace/table from the personal
    # `folder_id` above) — see DepartmentDocumentFolder.
    department_folder_id = Column(Integer, ForeignKey("department_document_folders.id", ondelete="SET NULL"), nullable=True, index=True)
    # Set from DocumentTemplate.is_order_template at creation time — lets the
    # department's "Приказы" tab list order/decree documents separately from
    # general documentation without a second table.
    is_order = Column(Boolean, nullable=False, default=False)
    # Same idea for the mandatory "Новости" module (item 6) — a news post is
    # just a Document with this flag set, same as orders.
    is_news = Column(Boolean, nullable=False, default=False)
    # Discord publish tracking — set the first time this document is
    # published; a later publish with the same message_id still present
    # edits that message ("Синхронизировать") instead of posting a new one.
    discord_channel_id = Column(String(50), nullable=True)
    discord_message_id = Column(String(64), nullable=True)
    owner_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False, index=True)
    last_edited_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    version = Column(Integer, nullable=False, default=1)  # bumped by the ONLYOFFICE save callback
    is_archived = Column(Boolean, nullable=False, default=False)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)  # trash
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), index=True)

    owner = relationship("AdminUser", foreign_keys=[owner_admin_id])
    last_editor = relationship("AdminUser", foreign_keys=[last_edited_by_admin_id])
    folder = relationship("DocumentFolder", foreign_keys=[folder_id])
    permissions = relationship("DocumentPermission", back_populates="document", cascade="all, delete-orphan")


class DocumentPermission(Base):
    """Grants view/comment/edit access to a document beyond its owner."""
    __tablename__ = "document_permissions"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    grantee_type = Column(String(20), nullable=False)  # "admin" | "privilege_group" | "access_level" | "everyone"
    grantee_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    grantee_group_id = Column(Integer, ForeignKey("admin_privilege_groups.id"), nullable=True)
    # For a document that belongs to a department (Document.department_id):
    # the key of one of THAT department's DepartmentAccessLevel rows —
    # sharing with "everyone who currently holds this department access
    # level", the department-scoped analogue of grantee_type="privilege_group".
    grantee_access_level_key = Column(String(64), nullable=True)
    level = Column(String(10), nullable=False, default="view")  # "view" | "comment" | "edit"
    granted_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())

    document = relationship("Document", back_populates="permissions")
    grantee_admin = relationship("AdminUser", foreign_keys=[grantee_admin_id])
    grantee_group = relationship("AdminPrivilegeGroup", foreign_keys=[grantee_group_id])


class DepartmentDocumentTag(Base):
    """A department's own tag vocabulary for documents — each department has
    its own independent set of tags (not shared site-wide). Creating a tag
    requires department "documentation":"edit"; assigning an EXISTING tag to
    a document only requires edit access on that one document — see
    routes/documents.py's tag endpoints."""
    __tablename__ = "department_document_tags"

    id = Column(Integer, primary_key=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    color = Column(String(16), default="#5A646E")
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        UniqueConstraint('department_id', 'name', name='uq_dept_doc_tag_name'),
    )


class DocumentTagLink(Base):
    """Many-to-many link between a document and one of its department's tags."""
    __tablename__ = "document_tag_links"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    tag_id = Column(Integer, ForeignKey("department_document_tags.id", ondelete="CASCADE"), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint('document_id', 'tag_id', name='uq_doc_tag_link'),
    )


class DocumentRevision(Base):
    """Point-in-time snapshot of a document's file, for version history —
    `storage_filename` names the archived copy under
    `{documents_upload_dir}/files/`, written whenever the ONLYOFFICE save
    callback supersedes the current version."""
    __tablename__ = "document_revisions"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=True)
    storage_filename = Column(String(255), nullable=True)
    version = Column(Integer, nullable=False)
    saved_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)

    saved_by = relationship("AdminUser", foreign_keys=[saved_by_admin_id])


class DocumentStar(Base):
    """Per-admin favorite/starred document."""
    __tablename__ = "document_stars"
    __table_args__ = (UniqueConstraint("document_id", "admin_id", name="ux_document_star"),)

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=func.now())


class DocumentTemplate(Base):
    """Reusable starting-point file for new documents, managed by any admin
    with documents/edit — a shared, growable library rather than a fixed
    set of hardcoded document types. `storage_filename` names the template
    file under `{documents_upload_dir}/templates/`."""
    __tablename__ = "document_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)
    icon = Column(String(16), nullable=True)
    file_type = Column(String(10), nullable=False, default="word")  # "word" | "cell"
    storage_filename = Column(String(255), nullable=True)
    # Marks this as an order/decree/citation template — documents created from
    # it are flagged Document.is_order=True so a department's "Приказы" tab
    # can list them separately from general documentation.
    is_order_template = Column(Boolean, nullable=False, default=False)
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    created_by = relationship("AdminUser", foreign_keys=[created_by_admin_id])


# ══════════════════════════════════════════════════════════════════════════════
# DEPARTMENTS / WORKSPACE ("Рабочая зона")
# ══════════════════════════════════════════════════════════════════════════════

class Department(Base):
    """A superuser-created department (e.g. "Медицинская служба"). Membership
    and internal permissions are Discord-role-driven — see DepartmentAccessLevel
    and DepartmentMember."""
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(String(2000), nullable=True)
    icon = Column(String(16), nullable=True)  # emoji
    color = Column(String(20), nullable=True)  # hex, e.g. "#1a68de"
    # The base "employee of this department" Discord role — required for
    # task_sync_department_roles to recognize someone as a department member.
    base_discord_role_id = Column(String(50), nullable=True)
    base_discord_role_name = Column(String(255), nullable=True)
    # Which optional workspace tabs are turned on for this department, e.g.
    # ["documentation","roster","rating","orders","test_server","chat"].
    # Superuser-only toggle (item 12 — "modularity").
    enabled_modules = Column(JSON, nullable=False, default=lambda: [])
    # Phase 6 — Pterodactyl test-server link.
    pterodactyl_server_uuid = Column(String(100), nullable=True)
    # Phase 5 — Discord channel orders/decrees get published to.
    orders_channel_id = Column(String(50), nullable=True)
    # Discord channel news posts get published to — separate from orders
    # since a department may want them going to different channels.
    news_channel_id = Column(String(50), nullable=True)
    # Discord channel the bot publishes/updates this department's internal
    # rating leaderboard embed in, hourly — see discord_bot/bot.py's
    # publish_department_ratings_task (mirrors the site-wide per-role rating
    # publish, same 15s-stagger-between-embeds rate-limit precaution).
    rating_channel_id = Column(String(50), nullable=True)
    # Superuser-only: the Discord channel CATEGORY this department "owns" —
    # used purely to validate a manually-entered channel ID when publishing a
    # document to Discord (routes/documents.py's verify-channel endpoint).
    # Left unset = no verification performed.
    discord_category_id = Column(String(50), nullable=True)
    # Superuser-only link: a member who is_head/is_deputy_head auto-receives
    # this server privilege group (task_sync_auto_server_privileges) — a
    # separate lever from DepartmentAccessLevel.server_privilege_group_id
    # since head/deputy status is a boolean flag, not backed by any
    # DepartmentAccessLevel row.
    head_server_privilege_group_id = Column(Integer, ForeignKey("server_privilege_groups.id"), nullable=True)
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    is_archived = Column(Boolean, nullable=False, default=False)

    created_by = relationship("AdminUser", foreign_keys=[created_by_admin_id])
    access_levels = relationship("DepartmentAccessLevel", back_populates="department", cascade="all, delete-orphan")
    members = relationship("DepartmentMember", back_populates="department", cascade="all, delete-orphan")


class DepartmentAccessLevel(Base):
    """A Discord-role-backed access tier within a department: the required
    "base" tier, any number of "qualification" tiers, or a "head" tier.
    Each tier carries its own {"pages": {module: {view, edit}}} permission
    matrix, editable by the department head or a superuser — see
    security.check_department_permission."""
    __tablename__ = "department_access_levels"
    __table_args__ = (UniqueConstraint("department_id", "key", name="ux_dept_access_level_key"),)

    id = Column(Integer, primary_key=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False, index=True)
    key = Column(String(64), nullable=False)  # slug, unique per department
    name = Column(String(255), nullable=False)
    discord_role_id = Column(String(50), nullable=True)
    kind = Column(String(20), nullable=False, default="qualification")  # "base" | "qualification" | "head"
    # Superuser-only link: a member currently holding this level (via
    # held_level_keys) auto-receives this server privilege group — see
    # task_sync_auto_server_privileges. Ranked against other simultaneously
    # held levels by kind (head > qualification > base), then by id desc.
    server_privilege_group_id = Column(Integer, ForeignKey("server_privilege_groups.id"), nullable=True)
    # Разрешает ли суперпользователь привязывать серверную группу к этому уровню (п.5).
    can_bind_server_privilege = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)
    description = Column(String(500), nullable=True)
    permissions = Column(JSON, nullable=False, default=lambda: {})
    # Hybrid permissions (item 3): additive SITE-WIDE {"pages": {page: {action: bool}}}
    # grants, same shape as AdminPrivilegeGroup.permissions — but unlike
    # `permissions` above (department-module-scoped, head-or-superuser
    # editable), this is superuser-only, since it reaches beyond the
    # department. See security.compute_department_site_permissions.
    site_permissions = Column(JSON, nullable=False, default=lambda: {})
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    department = relationship("Department", back_populates="access_levels")
    kit_binding = relationship("DepartmentKitBinding", uselist=False, back_populates="access_level")


class DepartmentMember(Base):
    """An admin's membership in a department: which access-level keys they
    currently hold, and whether the row was created/updated by
    task_sync_department_roles (is_auto) or by a head/superuser manually —
    the worker only ever adds to an auto row, never touches a manual one."""
    __tablename__ = "department_members"
    __table_args__ = (UniqueConstraint("department_id", "admin_id", name="ux_dept_member"),)

    id = Column(Integer, primary_key=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True)
    held_level_keys = Column(JSON, nullable=False, default=lambda: [])
    # is_head is the only superuser-exclusive "crown" — grantable/revocable
    # only by a superuser. is_deputy_head carries the exact same authority
    # (every is_head_or_deputy_or_superuser check below treats them
    # identically) but can be granted by an existing head/deputy too, not
    # just a superuser.
    is_head = Column(Boolean, nullable=False, default=False)
    is_deputy_head = Column(Boolean, nullable=False, default=False)
    is_auto = Column(Boolean, nullable=False, default=False)
    added_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    added_at = Column(DateTime, default=func.now())
    # Bumped whenever held_level_keys/is_head/is_deputy_head actually change
    # (manually or by task_sync_department_roles) — task_sync_auto_server_privileges
    # uses this as the "most recent assignment wins" tie-break when the same
    # admin simultaneously qualifies for auto-grants via multiple departments.
    role_state_updated_at = Column(DateTime, nullable=True)

    department = relationship("Department", back_populates="members")
    admin = relationship("AdminUser", foreign_keys=[admin_id])
    added_by = relationship("AdminUser", foreign_keys=[added_by_admin_id])


class DepartmentKitBinding(Base):
    """Superuser-configured link: holding this DepartmentAccessLevel should
    grant the bound qual-kit (job_id+kit_level) to the member's linked
    in-game player. Enforced/reconciled by workers.task_sync_department_kits
    — see DepartmentKitGrant for the "don't clobber what you didn't grant"
    audit trail that makes safe removal possible."""
    __tablename__ = "department_kit_bindings"
    __table_args__ = (UniqueConstraint("access_level_id", name="ux_dept_kit_binding_level"),)

    id = Column(Integer, primary_key=True, index=True)
    access_level_id = Column(Integer, ForeignKey("department_access_levels.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id = Column(String(50), nullable=False)
    kit_level = Column(Integer, nullable=False)
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())

    access_level = relationship("DepartmentAccessLevel", back_populates="kit_binding")


class DepartmentKitGrant(Base):
    """Audit row for one qual-kit grant the worker itself made — the worker
    only ever retracts a kval_kits entry it can prove it granted via a row
    here (and only if nothing else already changed that entry), mirroring
    the is_auto/linked_admin_auto never-clobber contract used everywhere
    else this session for worker-vs-human ownership."""
    __tablename__ = "department_kit_grants"
    __table_args__ = (UniqueConstraint("binding_id", "player_id", name="ux_dept_kit_grant"),)

    id = Column(Integer, primary_key=True, index=True)
    binding_id = Column(Integer, ForeignKey("department_kit_bindings.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id", ondelete="CASCADE"), nullable=False, index=True)
    granted_at = Column(DateTime, default=func.now())


class DepartmentRatingEntry(Base):
    """A single point-delta entry in a department's internal rating ledger —
    deliberately simpler/more flexible than the site-wide profession-based
    Ratings module: any admin with "rating" edit access can award or dock
    points with a free-form reason and optional category."""
    __tablename__ = "department_rating_entries"

    id = Column(Integer, primary_key=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True)
    delta = Column(Integer, nullable=False)
    reason = Column(String(500), nullable=True)
    category = Column(String(100), nullable=True)
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)

    admin = relationship("AdminUser", foreign_keys=[admin_id])
    created_by = relationship("AdminUser", foreign_keys=[created_by_admin_id])


class DepartmentReprimand(Base):
    """A disciplinary note against a department member. Deliberately
    head-only (NOT deputy-head) to issue — the one place in this whole
    department system where is_deputy_head does NOT carry the same
    authority as is_head, per explicit request."""
    __tablename__ = "department_reprimands"

    id = Column(Integer, primary_key=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True)
    reason = Column(String(1000), nullable=False)
    issued_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)

    admin = relationship("AdminUser", foreign_keys=[admin_id])
    issued_by = relationship("AdminUser", foreign_keys=[issued_by_admin_id])


class DepartmentChatChannel(Base):
    """A Discord channel linked to a department's "Чат" tab. A department may
    link multiple channels (item 6)."""
    __tablename__ = "department_chat_channels"

    id = Column(Integer, primary_key=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False, index=True)
    discord_channel_id = Column(String(50), nullable=False)
    label = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=func.now())


class DepartmentChatMessage(Base):
    """A message mirrored from (or sent to) a DepartmentChatChannel."""
    __tablename__ = "department_chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(Integer, ForeignKey("department_chat_channels.id", ondelete="CASCADE"), nullable=False, index=True)
    discord_message_id = Column(String(50), nullable=True, index=True)
    author_name = Column(String(255), nullable=True)
    author_avatar_url = Column(String(500), nullable=True)
    content = Column(String(4000), nullable=True)
    # Set when this message originated from the site (send box) rather than
    # being mirrored in from Discord.
    sent_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)

    sent_by = relationship("AdminUser", foreign_keys=[sent_by_admin_id])


# ══════════════════════════════════════════════════════════════════════════════
# DEPARTMENT DOCUMENT FOLDERS + PER-FOLDER ACLs (item 5)
# ══════════════════════════════════════════════════════════════════════════════

class DepartmentDocumentFolder(Base):
    """A folder within a department's shared documentation/orders space —
    separate table/namespace from the personal DocumentFolder (which is
    strictly owner-scoped and not shared)."""
    __tablename__ = "department_document_folders"

    id = Column(Integer, primary_key=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    parent_id = Column(Integer, ForeignKey("department_document_folders.id", ondelete="SET NULL"), nullable=True, index=True)
    # Discord channel documents inside this folder publish to by default
    # (routes/documents.py's api_publish_document_to_discord) — a document
    # not in any Discord-configured folder falls back to prompting for a
    # channel ID manually.
    discord_channel_id = Column(String(50), nullable=True)
    # When True, this folder's OWN DepartmentFolderPermission grants also
    # apply to documents nested in ITS SUBFOLDERS, not just documents placed
    # directly in this folder — live inheritance, resolved on every access
    # check (routes/documents.py's get_document_access), never copied.
    auto_apply_permissions = Column(Boolean, nullable=False, default=False)
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())

    created_by = relationship("AdminUser", foreign_keys=[created_by_admin_id])


class DepartmentFolderPermission(Base):
    """Additive ACL on a department folder — grants either an entire
    department access-level tier (by key) or one specific member "view"/
    "edit" on everything inside the folder, on top of the department's
    normal documentation/orders module baseline. Never revokes access,
    only adds — see get_document_access's folder-ACL tier."""
    __tablename__ = "department_folder_permissions"

    id = Column(Integer, primary_key=True, index=True)
    folder_id = Column(Integer, ForeignKey("department_document_folders.id", ondelete="CASCADE"), nullable=False, index=True)
    grantee_type = Column(String(20), nullable=False)  # "access_level" | "member"
    access_level_key = Column(String(64), nullable=True)
    member_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    level = Column(String(10), nullable=False, default="view")  # "view" | "edit"
    created_at = Column(DateTime, default=func.now())

    member = relationship("AdminUser", foreign_keys=[member_admin_id])


# ══════════════════════════════════════════════════════════════════════════════
# Tasks (Notion-style Kanban boards) — a single global "Задачи" page, gated by
# the site-wide "tasks" permission page. A board's department_id is purely an
# organizational tag (filter/label), never an access-control boundary — see
# permissions_registry.py's "tasks" entry.
# ══════════════════════════════════════════════════════════════════════════════

class TaskBoard(Base):
    __tablename__ = "task_boards"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(String(1000), nullable=True)
    icon = Column(String(16), default="📋")
    color = Column(String(16), default="#1a68de")
    # Purely an organizational tag — which department this board "belongs
    # to" for filtering/labeling. NULL = no department tag.
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())
    is_archived = Column(Boolean, nullable=False, default=False)

    department = relationship("Department")
    created_by = relationship("AdminUser", foreign_keys=[created_by_admin_id])
    columns = relationship("TaskColumn", back_populates="board", order_by="TaskColumn.sort_order", cascade="all, delete-orphan")


class TaskColumn(Base):
    """A named, colored status column on a board (e.g. "[NEW] 1. Модификации")."""
    __tablename__ = "task_columns"

    id = Column(Integer, primary_key=True, index=True)
    board_id = Column(Integer, ForeignKey("task_boards.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    color = Column(String(16), default="#5A646E")
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())

    board = relationship("TaskBoard", back_populates="columns")


class TaskBoardPermission(Base):
    """Гибкие права по доске: кто может смотреть/редактировать/удалять."""
    __tablename__ = "task_board_permissions"
    __table_args__ = (UniqueConstraint("board_id", "grantee_type", "grantee_key", name="uq_task_board_perm"),)

    id = Column(Integer, primary_key=True, index=True)
    board_id = Column(Integer, ForeignKey("task_boards.id", ondelete="CASCADE"), nullable=False, index=True)
    grantee_type = Column(String(20), nullable=False)  # group_nd | user | dept_level
    grantee_key = Column(String(128), nullable=False)  # group_id | admin_id | "deptId:levelKey"
    can_view = Column(Boolean, nullable=False, default=True)
    can_create = Column(Boolean, nullable=False, default=False)  # добавлять задачи
    can_edit = Column(Boolean, nullable=False, default=False)  # перемещать/редактировать
    can_delete = Column(Boolean, nullable=False, default=False)  # удалять задачи
    created_at = Column(DateTime, default=func.now())


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    board_id = Column(Integer, ForeignKey("task_boards.id", ondelete="CASCADE"), nullable=False, index=True)
    column_id = Column(Integer, ForeignKey("task_columns.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    description = Column(String(4000), nullable=True)
    deadline = Column(DateTime, nullable=True)
    priority = Column(String(10), nullable=True)  # "low" | "medium" | "high" | "urgent" | "ultra"
    tags = Column(JSON, default=lambda: [])
    # An assignee can be an admin, a department, or both (e.g. "this
    # employee, on behalf of this department") — either may be NULL.
    assignee_admin_id = Column(Integer, ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True, index=True)
    assignee_department_id = Column(Integer, ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    sort_order = Column(Integer, default=0)
    # p.3: статус и скрытость
    status = Column(String(20), nullable=False, default="review")  # review | in_progress | done | rejected
    is_hidden = Column(Boolean, nullable=False, default=False)
    rejection_reason = Column(String(1000), nullable=True)
    # p.2: превью скриншота
    preview_attachment_id = Column(Integer, ForeignKey("task_attachments.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    board = relationship("TaskBoard")
    column = relationship("TaskColumn")
    assignee_admin = relationship("AdminUser", foreign_keys=[assignee_admin_id])
    assignee_department = relationship("Department", foreign_keys=[assignee_department_id])
    created_by = relationship("AdminUser", foreign_keys=[created_by_admin_id])
    comments = relationship("TaskComment", back_populates="task", order_by="TaskComment.created_at", cascade="all, delete-orphan")
    preview_attachment = relationship("TaskAttachment", foreign_keys=[preview_attachment_id])


class TaskComment(Base):
    __tablename__ = "task_comments"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    content = Column(String(2000), nullable=False)
    created_at = Column(DateTime, default=func.now())

    task = relationship("Task", back_populates="comments")
    admin = relationship("AdminUser", foreign_keys=[admin_id])


class TaskAttachment(Base):
    """Скриншот/файл, прикреплённый к задаче. is_preview отмечает превью плашки."""
    __tablename__ = "task_attachments"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    uploader_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(500), nullable=False)
    mime_type = Column(String(128), nullable=True)
    file_size = Column(Integer, nullable=True)
    is_preview = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=func.now())

    task = relationship("Task", foreign_keys=[task_id])
    uploader = relationship("AdminUser", foreign_keys=[uploader_admin_id])


class TaskActivityLog(Base):
    """Лог действий по задаче: кто, что, когда."""
    __tablename__ = "task_activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    action = Column(String(50), nullable=False)  # create, update, move, status_change, hide, unhide, reject, attach, preview
    detail = Column(String(2000), nullable=True)
    created_at = Column(DateTime, default=func.now())

    task = relationship("Task", foreign_keys=[task_id])
    admin = relationship("AdminUser", foreign_keys=[admin_id])


class TaskBoardMessage(Base):
    """Общий чат доски."""
    __tablename__ = "task_board_messages"

    id = Column(Integer, primary_key=True, index=True)
    board_id = Column(Integer, ForeignKey("task_boards.id", ondelete="CASCADE"), nullable=False, index=True)
    author_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    content = Column(String(4000), nullable=False)
    reply_to_id = Column(Integer, ForeignKey("task_board_messages.id", ondelete="SET NULL"), nullable=True)
    is_pinned = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=func.now())

    board = relationship("TaskBoard", foreign_keys=[board_id])
    author = relationship("AdminUser", foreign_keys=[author_admin_id])
    reply_to = relationship("TaskBoardMessage", remote_side=[id])


class TaskBoardMessageAttachment(Base):
    __tablename__ = "task_board_message_attachments"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("task_board_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(500), nullable=False)
    mime_type = Column(String(128), nullable=True)
    file_size = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now())

    message = relationship("TaskBoardMessage", foreign_keys=[message_id])


class PlayerAnomaly(Base):
    """A finding from the background anomaly scan (workers.task_scan_anomalies,
    every 30 min, scheduler-process-only — see services_anomaly.py): either a
    "twink" (alt-account) suspicion pairing two players, or a "behavior"
    statistical outlier for one player. Re-detection of the SAME pairing/
    subtype just bumps last_seen_at instead of duplicating a row."""
    __tablename__ = "player_anomalies"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(20), nullable=False, index=True)  # "twink" | "behavior"
    # "ip_ban_evasion" | "ip_shared" | "behavior_<action_type>"
    subtype = Column(String(50), nullable=False)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=False, index=True)
    # The other account in a "twink" pairing — NULL for "behavior" findings.
    related_player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=True, index=True)
    score = Column(Integer, nullable=False, default=0, index=True)
    evidence = Column(JSON, default=lambda: {})
    detected_at = Column(DateTime, default=func.now(), index=True)
    last_seen_at = Column(DateTime, default=func.now(), index=True)
    status = Column(String(20), nullable=False, default="new", index=True)  # new | reviewed | dismissed
    reviewed_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    # True if this specific finding triggered the automatic ban-evasion ban
    # (services_anomaly.py's _auto_ban_evader) — kept for audit/history even
    # if the finding is later dismissed.
    auto_banned = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint('category', 'subtype', 'player_id', 'related_player_id', name='uq_player_anomaly'),
    )


class WorldRound(Base):
    """Live round state fed by the client-side WorldStateStreamer plugin —
    see WORLD_STATE_API_GUIDE.md for the exact wire contract. One row per
    round_id, upserted in place (never an append-only log): `world/init`
    (once, at round start) fills in the static geometry columns; `world/snapshot`
    (every ~3s while the round runs) overwrites the `last_*` columns with the
    latest known dynamic state — "Обзор мира" polls the latter and treats a
    round as ended once last_update_at goes stale (no explicit end signal
    exists on the wire)."""
    __tablename__ = "world_rounds"

    id = Column(Integer, primary_key=True, index=True)
    round_id = Column(String(64), unique=True, nullable=False, index=True)

    # ── Static geometry, from world/init ────────────────────────────────
    server_name = Column(String(100), nullable=True, index=True)
    level_seed = Column(String(100), nullable=True)
    level_name = Column(String(255), nullable=True)
    level_type = Column(String(50), nullable=True)
    level_w = Column(Integer, nullable=True)
    level_h = Column(Integer, nullable=True)
    start_x = Column(Float, nullable=True)
    end_x = Column(Float, nullable=True)
    abyss_start_y = Column(Float, nullable=True)
    sub_name = Column(String(255), nullable=True)
    submarines = Column(JSON, nullable=True)  # [{id, name}]
    hulls = Column(JSON, nullable=True)       # [{id, sub, r:[x,y,w,h]}] — sub-local
    walls = Column(JSON, nullable=True)       # [{sub, r, h, p}] — sub-local
    doors = Column(JSON, nullable=True)       # [{id, sub, r, h}] — sub-local
    outline_w = Column(Integer, nullable=True)
    outline_h = Column(Integer, nullable=True)
    outline = Column(JSON, nullable=True)     # [[[x,y], ...], ...] — world coords, can be large
    init_sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())

    # ── Latest dynamic snapshot, from world/snapshot (upsert-only, no history) ──
    last_seq = Column(Integer, nullable=True)
    last_t = Column(Float, nullable=True)
    last_sent_at = Column(DateTime, nullable=True)
    last_subs = Column(JSON, nullable=True)         # [{id, x, y}] — world coords
    last_hulls_state = Column(JSON, nullable=True)  # [{id, water, oxygen, fire}]
    last_doors_state = Column(JSON, nullable=True)  # [{id, open}]
    last_reactors = Column(JSON, nullable=True)
    last_characters = Column(JSON, nullable=True)
    last_items = Column(JSON, nullable=True)        # null = IncludeItems:false — never overwritten with null, see routes/world_state.py
    # Persistent once-per-round claim for the automatic AI sector briefing.
    # It prevents duplicate reports across Uvicorn workers and process restarts.
    ai_briefing_queued_at = Column(DateTime, nullable=True)
    # Server-side receipt time (not the plugin's sent_at) — the single
    # source of truth for "is this round still live" (missing snapshots for
    # a few intervals = round over), independent of any clock skew on the
    # game server's end.
    last_update_at = Column(DateTime, nullable=True, index=True)


class AITaskLog(Base):
    """Stage-by-stage progress log for AI tasks (dossiers, health).

    Each worker appends bounded entries while a task moves through its
    stages, so an operator can trace where a task sits and what happened
    at each step without tailing application logs."""

    __tablename__ = "ai_task_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_kind = Column(String(30), nullable=False, index=True)  # dossier | health
    task_ref = Column(String(120), nullable=False, index=True)
    stage = Column(String(40), nullable=False)
    level = Column(String(10), nullable=False, default="info")  # info | warning | error
    message = Column(String(500), nullable=False, default="")
    created_at = Column(DateTime, default=func.now(), nullable=False, index=True)


class AICreatureAlert(Base):
    """Durable one-alert-per-character state for dispatcher contact warnings."""

    __tablename__ = "ai_creature_alerts"

    id = Column(Integer, primary_key=True, index=True)
    round_id = Column(String(64), nullable=False, index=True)
    character_id = Column(String(64), nullable=False)
    species = Column(String(100), nullable=False)
    distance_from_yasa_m_approx = Column(Integer, nullable=False)
    # Distance included in the last successfully delivered initial warning.
    # Later snapshots keep ``distance_from_yasa_m_approx`` fresh, allowing a
    # scheduled AI follow-up to compare the same durable character contact.
    reported_distance_from_yasa_m_approx = Column(Integer, nullable=True)
    # True only when this alert was first classified within 250 m of Yasa.
    # Exact species names may be shared with AI only for these close contacts.
    yasa_perimeter = Column(Boolean, nullable=False, default=False)
    detected_at = Column(DateTime, default=func.now(), nullable=False, index=True)
    # Refreshed from every snapshot while this entity is present. Contact
    # tracking uses it for a grace period after the last member disappears.
    last_seen_at = Column(DateTime, default=func.now(), nullable=True)
    # ``queued_at`` is a renewable delivery lease.  If a web worker dies after
    # claiming an alert, a later world snapshot may reclaim it once the lease
    # expires. ``delivered_at`` permanently consumes rows which either reached
    # the game-server action queue or were absorbed into an existing track.
    queued_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "round_id",
            "character_id",
            name="uq_ai_creature_alert_round_character",
        ),
    )


class AIDispatcherReminder(Base):
    """Durable delayed note authored by the AI dispatcher itself."""

    __tablename__ = "ai_dispatcher_reminders"

    id = Column(Integer, primary_key=True, index=True)
    source_event_id = Column(String(128), nullable=False)
    sequence = Column(Integer, nullable=False)
    round_id = Column(String(128), nullable=False, index=True)
    server_name = Column(String(20), nullable=False, index=True)
    note = Column(String(1000), nullable=False)
    communication_context = Column(JSON, nullable=True)
    due_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    queued_at = Column(DateTime, nullable=True, index=True)
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "source_event_id",
            "sequence",
            name="uq_ai_dispatcher_reminder_source_sequence",
        ),
    )


class WarehouseItem(Base):
    """Virtual dispatch resource warehouse — one row per Barotrauma item
    identifier (see static/item_catalog.json / tools/extract_barotrauma_items.py)
    with a quantity, managed from the Диспетчерская page (routes/warehouse.py).
    Gated by the same site-wide "dispatch" permission as the rest of that
    page — this isn't a separate feature/permission, just another dispatch
    tool. Cargo orders reserve stock atomically in routes/dispatch.py; a
    failed game-server ACK restores it exactly once in services.py."""
    __tablename__ = "warehouse_items"

    id = Column(Integer, primary_key=True, index=True)
    identifier = Column(String(100), unique=True, nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=0)
    updated_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    updated_by = relationship("AdminUser", foreign_keys=[updated_by_admin_id])


class SubCheckerConfig(Base):
    """Единственная строка с конфигурацией проверщика кораблей (ГОСТ).

    Всё содержимое проверки — формулы, пороги оборудования, список
    запрещённых предметов, классы и их наполнение — лежит в `data` (структура
    описана в sub_checker_defaults.DEFAULT_CONFIG). История правок ведётся
    отдельной таблицей sub_checker_config_revisions: настройки ГОСТа —
    нормативный документ, и «кто и когда поменял порог» должно быть видно.
    """
    __tablename__ = "sub_checker_config"

    id = Column(Integer, primary_key=True, index=True)
    data = Column(JSON, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    updated_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    updated_by = relationship("AdminUser", foreign_keys=[updated_by_admin_id])


class SubCheckerConfigRevision(Base):
    """Снимок конфигурации проверщика перед каждой правкой."""
    __tablename__ = "sub_checker_config_revisions"

    id = Column(Integer, primary_key=True, index=True)
    revision = Column(Integer, nullable=False, index=True)
    data = Column(JSON, nullable=False)
    comment = Column(String(500), nullable=True)
    created_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)

    created_by = relationship("AdminUser", foreign_keys=[created_by_admin_id])


class SubCheckResult(Base):
    """Результат одной проверки .sub-файла.

    Хранится и для проверок конкретной версии проекта (project_id/version_id
    заполнены — сюда же попадает автопроверка при загрузке новой версии), и
    для разовых загрузок файла во вкладке чекера (оба поля NULL).
    `report` — полный отчёт из services_sub_checker.check_sub_file.
    """
    __tablename__ = "sub_check_results"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("sub_projects.id"), nullable=True, index=True)
    version_id = Column(Integer, ForeignKey("sub_project_versions.id"), nullable=True, index=True)
    filename = Column(String(500), nullable=True)
    submarine_name = Column(String(255), nullable=True)
    ship_class = Column(String(64), nullable=True)
    tonnage = Column(Float, nullable=True)
    passed = Column(Boolean, nullable=False, default=False)
    error_count = Column(Integer, nullable=False, default=0)
    warning_count = Column(Integer, nullable=False, default=0)
    # Ревизия конфигурации, по которой считали — без неё старый отчёт нечем
    # объяснить после правки порогов.
    config_revision = Column(Integer, nullable=True)
    # True — проверка запущена автоматически при загрузке версии.
    is_auto = Column(Boolean, nullable=False, default=False)
    report = Column(JSON, nullable=True)
    checked_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)

    checked_by = relationship("AdminUser", foreign_keys=[checked_by_admin_id])
    project = relationship("SubProject", foreign_keys=[project_id])
    version = relationship("SubProjectVersion", foreign_keys=[version_id])
