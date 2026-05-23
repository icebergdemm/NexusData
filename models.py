from sqlalchemy import Column, Integer, String, ForeignKey, JSON, DateTime, func, Float, Boolean, UniqueConstraint
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
    admin_id = Column(Integer)
    action = Column(String(255))
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=func.now())


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
    """Queued moderation actions (kick / ban / message / unban / trap) for the game server."""
    __tablename__ = "server_action_queue"

    id = Column(Integer, primary_key=True, index=True)
    action_type = Column(String(32), nullable=False)   # kick | ban | unban | message | trap
    target_steamid = Column(String(50), nullable=False, index=True)
    reason = Column(String(500), nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    message_text = Column(String(1000), nullable=True)
    server_name = Column(String(100), nullable=True, index=True)  # target server (null = all servers)
    queued_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    queued_at = Column(DateTime, default=func.now())
    executed = Column(Integer, default=0)    # 0 = pending, 1 = done, 2 = failed
    executed_at = Column(DateTime, nullable=True)
    error_message = Column(String(500), nullable=True)
    # Per-server ACK tracking (for broadcast actions, server_name=null)
    # 0 = pending, 1 = success, 2 = failed
    ack_asper1 = Column(Integer, default=0, nullable=False)
    ack_asper2 = Column(Integer, default=0, nullable=False)


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
    queued_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
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

    versions = relationship("SubProjectVersion", back_populates="project", order_by="SubProjectVersion.version_number.desc()")


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
