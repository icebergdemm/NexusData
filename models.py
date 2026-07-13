from sqlalchemy import Column, Integer, String, ForeignKey, JSON, DateTime, func, Float, Boolean, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship
from database import Base


class Kit(Base):
    __tablename__ = "kits"
    
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String(50), index=True)
    kit_level = Column(Integer, default=0)
    items = Column(JSON)


class PlayerData(Base):
    __tablename__ = "players_data"
    
    player_id = Column(Integer, primary_key=True, index=True)
    player_last_time_name = Column(String(255), index=True)
    player_steamid = Column(String(50), unique=True, index=True)
    last_login_time = Column(DateTime, default=func.now())
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
    
    stats = relationship("PlayerStats", backref="player", uselist=False)
    
    @property
    def play_time_hours(self) -> float:
        """Convert play_time (in seconds) to hours"""
        if not self.play_time:
            return 0.0
        return self.play_time / 3600.0


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


class AdminActionLog(Base):
    __tablename__ = "admin_action_logs"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, index=True)
    action = Column(String(255))
    details = Column(JSON, nullable=True)
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
    login_time = Column(DateTime, nullable=False)
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
    steamid = Column(String(50), unique=True, nullable=False, index=True)
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
    reviewed = Column(Integer, default=0)  # 0 = not reviewed, 1 = reviewed


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

    # Per-server status: 0=pending, 1=done, 2=failed
    status_asper1 = Column(Integer, default=0, nullable=False)
    status_asper2 = Column(Integer, default=0, nullable=False)
    error_asper1 = Column(String(500), nullable=True)
    error_asper2 = Column(String(500), nullable=True)
    executed_at_asper1 = Column(DateTime, nullable=True)
    executed_at_asper2 = Column(DateTime, nullable=True)


class PlayerNote(Base):
    """Admin notes / personal file for a player."""
    __tablename__ = "player_notes"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    content = Column(String(4000), nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class LabelCategory(Base):
    """Admin-defined label categories (e.g. 'Подозрение на читы')."""
    __tablename__ = "label_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), unique=True, nullable=False)
    color = Column(String(16), default="#888888")  # hex color for UI
    icon = Column(String(8), default="🏷")         # emoji icon
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


class DiscordBotAction(Base):
    """Action queue for the Discord bot (similar to ServerActionQueue)."""
    __tablename__ = "discord_bot_actions"

    id = Column(Integer, primary_key=True, index=True)
    action_type = Column(String(50), nullable=False)  # send_rating, send_message, kick_member, etc.
    payload = Column(JSON, default=lambda: {})
    queued_by_admin_id = Column(Integer, ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    queued_at = Column(DateTime, default=func.now())
    executed = Column(Integer, default=0)  # 0=pending, 1=done, 2=failed
    executed_at = Column(DateTime, nullable=True)
    error_message = Column(String(500), nullable=True)


class DiscordBotConfig(Base):
    """Bot configuration stored in DB (channels, settings)."""
    __tablename__ = "discord_bot_config"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(String(2000), nullable=True)
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

    id = Column(Integer, primary_key=True, index=True)
    ticket_type = Column(String(50), nullable=False)  # help, player_report, bug, admin_report, department_report
    title = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="open")  # open, waiting, closed
    discord_channel_id = Column(String(25), nullable=True, index=True)
    creator_discord_id = Column(String(25), nullable=False)
    creator_username = Column(String(255), nullable=True)
    creator_display_name = Column(String(255), nullable=True)
    player_id = Column(Integer, ForeignKey("players_data.player_id"), nullable=True)
    assigned_role_ids = Column(JSON, default=lambda: [])  # Discord role IDs with access
    closed_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=func.now())
    closed_at = Column(DateTime, nullable=True)
    last_message_at = Column(DateTime, nullable=True)
    awaiting_admin = Column(Integer, default=1)  # 1 = waiting for admin reply


class TicketMessage(Base):
    """Message in a ticket (synced from Discord)."""
    __tablename__ = "ticket_messages"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False, index=True)
    discord_message_id = Column(String(25), nullable=True)
    author_discord_id = Column(String(25), nullable=False)
    author_username = Column(String(255), nullable=True)
    author_display_name = Column(String(255), nullable=True)
    author_avatar_url = Column(String(500), nullable=True)
    content = Column(String(4000), nullable=True)
    attachments = Column(JSON, default=lambda: [])  # [{url, filename, content_type}]
    is_bot = Column(Integer, default=0)  # 1 = sent from web panel via bot
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
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    # Soft-delete
    is_deleted = Column(Integer, default=0)  # 0=active, 1=deleted
    deleted_at = Column(DateTime, nullable=True)

    versions = relationship("SubProjectVersion", back_populates="project", order_by="SubProjectVersion.version_number.desc()")
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
    issued_at = Column(DateTime, default=func.now(), index=True)
    expires_at = Column(DateTime, nullable=True)           # NULL = permanent
    is_active = Column(Integer, default=1)                 # 1 = active, 0 = void/overridden
    notes = Column(String(2000), nullable=True)            # internal admin notes
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
    event_type = Column(String(20), nullable=False)  # start | end
    server_name = Column(String(100), nullable=True, index=True)
    submarine_name = Column(String(255), nullable=True)
    mission = Column(String(500), nullable=True)
    location = Column(String(255), nullable=True)
    mission_completed = Column(Integer, nullable=True)
    reached_station = Column(Integer, nullable=True)
    players = Column(JSON, default=lambda: [])
    created_at = Column(DateTime, default=func.now(), index=True)


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


class AppSetting(Base):
    """Generic runtime key-value settings editable from the admin panel."""
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(String(2000), nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


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
    """A single document. Content is stored as Tiptap/ProseMirror JSON."""
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False, default="Без названия")
    icon = Column(String(16), nullable=True)  # emoji
    content = Column(JSON, nullable=True)  # ProseMirror JSON document
    content_text = Column(LONGTEXT, nullable=True)  # plain-text extract for search
    folder_id = Column(Integer, ForeignKey("document_folders.id", ondelete="SET NULL"), nullable=True, index=True)
    owner_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False, index=True)
    last_edited_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    version = Column(Integer, nullable=False, default=1)  # optimistic concurrency
    is_archived = Column(Boolean, nullable=False, default=False)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)  # trash
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), index=True)

    owner = relationship("AdminUser", foreign_keys=[owner_admin_id])
    last_editor = relationship("AdminUser", foreign_keys=[last_edited_by_admin_id])
    folder = relationship("DocumentFolder", foreign_keys=[folder_id])
    permissions = relationship("DocumentPermission", back_populates="document", cascade="all, delete-orphan")
    comments = relationship("DocumentComment", back_populates="document", cascade="all, delete-orphan")


class DocumentPermission(Base):
    """Grants view/comment/edit access to a document beyond its owner."""
    __tablename__ = "document_permissions"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    grantee_type = Column(String(20), nullable=False)  # "admin" | "privilege_group" | "everyone"
    grantee_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    grantee_group_id = Column(Integer, ForeignKey("admin_privilege_groups.id"), nullable=True)
    level = Column(String(10), nullable=False, default="view")  # "view" | "comment" | "edit"
    granted_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())

    document = relationship("Document", back_populates="permissions")
    grantee_admin = relationship("AdminUser", foreign_keys=[grantee_admin_id])
    grantee_group = relationship("AdminPrivilegeGroup", foreign_keys=[grantee_group_id])


class DocumentRevision(Base):
    """Point-in-time snapshot of a document's content, for version history."""
    __tablename__ = "document_revisions"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=True)
    content = Column(JSON, nullable=True)
    version = Column(Integer, nullable=False)
    saved_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)

    saved_by = relationship("AdminUser", foreign_keys=[saved_by_admin_id])


class DocumentComment(Base):
    """Inline comment anchored to a highlighted range (via a `comment` mark in the doc)."""
    __tablename__ = "document_comments"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = Column(Integer, ForeignKey("document_comments.id", ondelete="CASCADE"), nullable=True, index=True)
    author_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False)
    body = Column(String(2000), nullable=False)
    anchor_text = Column(String(500), nullable=True)  # quoted snippet the comment refers to
    mark_id = Column(String(64), nullable=True, index=True)  # matches the comment mark's id in the doc JSON
    resolved = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=func.now(), index=True)

    document = relationship("Document", back_populates="comments")
    author = relationship("AdminUser", foreign_keys=[author_admin_id])


class DocumentStar(Base):
    """Per-admin favorite/starred document."""
    __tablename__ = "document_stars"
    __table_args__ = (UniqueConstraint("document_id", "admin_id", name="ux_document_star"),)

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=func.now())
