from sqlalchemy import Column, Integer, String, ForeignKey, JSON, DateTime, func, Float
from sqlalchemy.orm import relationship
from database import Base


class Kit(Base):
    __tablename__ = "kits"
    
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String(15), index=True)
    kit_level = Column(Integer)
    items = Column(JSON)


class PlayerData(Base):
    __tablename__ = "players_data"
    
    player_id = Column(Integer, primary_key=True, index=True)
    player_last_time_name = Column(String(255), index=True)
    player_steamid = Column(String(50), unique=True, index=True)
    last_login_time = Column(DateTime, default=func.now())
    play_time = Column(Integer, default=0)
    rank_id = Column(Integer)
    kit_level = Column(Integer)
    achievements = Column(JSON, default=list)
    
    stats = relationship("PlayerStats", backref="player", uselist=False)


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
    role = Column(String(50), default="admin")  # admin, moderator, etc.
    permissions = Column(JSON, default=list)  # list of permissions
    created_at = Column(DateTime, default=func.now())
    last_login = Column(DateTime, nullable=True)


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
