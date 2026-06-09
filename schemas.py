from pydantic import BaseModel, Field, field_validator, computed_field
from typing import List, Optional, Generic, TypeVar, Dict, Any
from datetime import datetime

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Pagination query parameters"""
    page: int = Field(default=1, ge=1, description="Page number (starting from 1)")
    page_size: int = Field(default=50, ge=1, le=1000, description="Items per page")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response"""
    items: List[T]
    total: int = Field(description="Total number of items")
    page: int = Field(description="Current page number")
    page_size: int = Field(description="Items per page")
    total_pages: int = Field(description="Total number of pages")
    has_next: bool = Field(description="Has next page")
    has_prev: bool = Field(description="Has previous page")


class KitOut(BaseModel):
    id: int
    job_id: str
    kit_level: int
    items: Any = Field(default_factory=dict)  # Can be dict or list in DB

    class Config:
        from_attributes = True


class PlayerDataBase(BaseModel):
    player_last_time_name: str = Field(min_length=1, max_length=255)
    player_steamid: str = Field(min_length=17, max_length=18)
    rank_id: int = Field(default=0, ge=0)
    kit_level: int = Field(default=0, ge=0)
    achievements: List[str] = Field(default_factory=list, description="List of achievement IDs (for .NET API compatibility)")
    
    @field_validator('achievements', mode='before')
    def normalize_achievements(cls, v):
        """Convert achievements to list of strings for .NET API compatibility.
        In DB, achievements may be stored as dict (reward structure) but API expects List[str]."""
        if v is None:
            return []
        if isinstance(v, list):
            # Filter to strings only
            return [str(x) for x in v if isinstance(x, (str, int))]
        if isinstance(v, dict):
            # If it's a dict (reward structure), return empty list for API
            return []
        if isinstance(v, str):
            return [v]
        return []
    
    @field_validator('player_steamid')
    def validate_steamid(cls, v: str) -> str:
        """Validate SteamID format"""
        from utils import validate_steamid
        if not validate_steamid(v):
            raise ValueError('Invalid SteamID format')
        return v
    
    @field_validator('player_last_time_name')
    def validate_name(cls, v: str) -> str:
        """Validate player name"""
        from utils import validate_player_name
        if not validate_player_name(v):
            raise ValueError('Invalid player name')
        return v


class PlayerDataCreate(PlayerDataBase):
    play_time: Optional[int] = Field(None, ge=0, description="Play time in seconds (None preserves existing)")
    last_login_time: Optional[str] = None
    last_ip: Optional[str] = Field(None, max_length=45, description="Last IP address")
    last_subnet: Optional[str] = Field(None, max_length=45, description="Last subnet (e.g., 192.168.1.0/24)")
    first_login: Optional[str] = None
    avg_ping: Optional[int] = Field(None, ge=0, description="Average ping")
    # These fields are optional for upsert (preserve existing if not provided)
    rank_id: Optional[int] = Field(None, ge=0, description="Rank ID (None preserves existing)")
    kit_level: Optional[int] = Field(None, ge=0, description="Kit level (None preserves existing)")


class PlayerDataUpdate(BaseModel):
    player_last_time_name: Optional[str] = Field(None, min_length=1, max_length=255)
    last_login_time: Optional[str] = None
    play_time: Optional[int] = Field(None, ge=0)
    rank_id: Optional[int] = Field(None, ge=0)
    kit_level: Optional[int] = Field(None, ge=0)
    achievements: Optional[List[str]] = None


class PlayerDataOut(PlayerDataBase):
    player_id: int
    play_time: int = Field(default=0, ge=0, description="Play time in seconds")
    last_login_time: datetime
    rank_name: Optional[str] = None
    kit_name: Optional[str] = None
    achievements_data: Optional[Dict[str, Any]] = Field(default=None, description="Raw achievements/rewards JSON from DB")

    @computed_field
    @property
    def play_time_hours(self) -> float:
        return self.play_time / 3600.0 if self.play_time else 0.0

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_with_mappings(cls, obj, ranks_mapping: dict, kits_mapping: dict):
        data = cls.from_orm(obj)
        data.rank_name = ranks_mapping.get(str(obj.rank_id), "N/A") if obj.rank_id is not None else "N/A"
        data.kit_name = kits_mapping.get(str(obj.kit_level), "N/A") if obj.kit_level is not None else "N/A"
        # Include raw achievements data (dict structure from DB)
        # This is the authoritative source for kval_kits, items, stats, talents
        raw_ach = obj.achievements
        if raw_ach is None:
            data.achievements_data = {}
        elif isinstance(raw_ach, dict):
            data.achievements_data = raw_ach
        elif isinstance(raw_ach, list):
            data.achievements_data = {"legacy_list": raw_ach}
        else:
            data.achievements_data = {}
        # Also set the legacy 'achievements' list field for backward compatibility
        # (extract keys from dict if present)
        if isinstance(raw_ach, dict):
            # Provide a flat list of item IDs for .NET clients that read 'achievements' field
            items = raw_ach.get("items", {})
            data.achievements = list(items.keys()) if items else []
        return data


class PlayerStatsBase(BaseModel):
    successful_missions_captain: int = Field(default=0, ge=0)
    total_missions_captain: int = Field(default=0, ge=0)
    saved_players: int = Field(default=0, ge=0)
    killed_monsters: int = Field(default=0, ge=0)
    killed_players: int = Field(default=0, ge=0)
    killed_npc: int = Field(default=0, ge=0)


class PlayerStatsCreate(PlayerStatsBase):
    pass


class PlayerStatsUpdate(BaseModel):
    successful_missions_captain: Optional[int] = Field(None, ge=0)
    total_missions_captain: Optional[int] = Field(None, ge=0)
    saved_players: Optional[int] = Field(None, ge=0)
    killed_monsters: Optional[int] = Field(None, ge=0)
    killed_players: Optional[int] = Field(None, ge=0)
    killed_npc: Optional[int] = Field(None, ge=0)


class PlayerStatsOut(PlayerStatsBase):
    player_id: int

    class Config:
        from_attributes = True


class PlayerKillCreate(BaseModel):
    monster_type: str = Field(min_length=1, max_length=100)
    kills_count: int = Field(ge=0)


class PlayerKillOut(PlayerKillCreate):
    id: int
    player_id: int
    
    class Config:
        from_attributes = True


class PlayerRoleActionCreate(BaseModel):
    role: str = Field(min_length=1, max_length=50)
    action_type: str = Field(min_length=1, max_length=100)
    value: float = Field(default=1)


class PlayerRoleActionOut(PlayerRoleActionCreate):
    id: int
    player_id: int
    
    class Config:
        from_attributes = True


class PlayerIPCreate(BaseModel):
    ip: str = Field(min_length=7, max_length=45)
    subnet: str = Field(min_length=7, max_length=45)


class PlayerIPOut(PlayerIPCreate):
    id: int
    player_id: int
    first_seen: datetime
    last_seen: datetime
    
    class Config:
        from_attributes = True


class PlayerNameCreate(BaseModel):
    nickname: str = Field(min_length=1, max_length=255)


class PlayerNameOut(PlayerNameCreate):
    id: int
    player_id: int
    first_seen: datetime
    last_seen: datetime
    
    class Config:
        from_attributes = True


class PlayerRoleStatCreate(BaseModel):
    role: str = Field(min_length=1, max_length=50)
    rounds_played: int = Field(default=0, ge=0)
    mvp_score: float = Field(default=0.0)


class PlayerRoleStatOut(PlayerRoleStatCreate):
    id: int
    player_id: int
    
    class Config:
        from_attributes = True


class PlayerSessionCreate(BaseModel):
    login_time: str  # ISO8601 datetime string
    logout_time: Optional[str] = None  # ISO8601 datetime string
    avg_ping: Optional[int] = Field(None, ge=0)


class PlayerSessionOut(BaseModel):
    id: int
    player_id: int
    session_number: int
    login_time: datetime
    logout_time: Optional[datetime]
    session_duration: Optional[int]
    avg_ping: Optional[int]
    
    class Config:
        from_attributes = True


class StatusResponse(BaseModel):
    status: str
    version: str


class HealthResponse(BaseModel):
    status: str
    database: str = Field(description="Database connection status")
    timestamp: datetime


class MessageEnvelope(BaseModel):
    status: str
    version: str


class HealthResponse(BaseModel):
    status: str
    database: str = Field(description="Database connection status")
    timestamp: datetime


class MessageEnvelope(BaseModel):
    """Universal inbound message envelope for external/game sources."""
    type: str = Field(min_length=1, max_length=128, description="Message type, e.g. player.upsert")
    version: int = Field(default=1, ge=1)
    source: str = Field(min_length=1, max_length=128)
    trace_id: Optional[str] = Field(default=None, max_length=128)
    occurred_at: Optional[datetime] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class MessageProcessResult(BaseModel):
    status: str = "accepted"
    message_type: str
    trace_id: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


# ── Server moderation schemas ────────────────────────────────────────────────────────────────────

class OnlinePlayerInfo(BaseModel):
    steamid: str
    player_name: Optional[str] = None
    job_id: Optional[str] = None
    ping: Optional[int] = None


class ServerPlayersReport(BaseModel):
    players: List[OnlinePlayerInfo]
    # Optional extended fields (v2)
    creatures: Optional[List[Dict[str, Any]]] = None  # [{id, hp, x, y}]
    submarine_health: Optional[int] = None  # 0-100
    supply_shuttle_eta: Optional[int] = None  # seconds until shuttle


class ServerPlayersReportResult(BaseModel):
    status: str
    count: int


# ── Dispatch (radio terminal) schemas ────────────────────────────────────────

class DispatchMessageIn(BaseModel):
    """Message from game server radio terminal."""
    steamid: Optional[str] = Field(None, description="SteamID64 sender (can be null)")
    player_name: Optional[str] = None
    text: str = Field(..., max_length=2000)
    server_name: Optional[str] = None


class DispatchMessageOut(BaseModel):
    id: int
    steamid: Optional[str] = None
    player_name: Optional[str] = None
    text: str
    sender_type: str  # "player" or "dispatcher"
    server_name: Optional[str] = None
    created_at: Optional[str] = None


# ── Round lifecycle schemas ──────────────────────────────────────────────────

class RoundStartIn(BaseModel):
    """Game server reports round start."""
    server_name: Optional[str] = None
    submarine_name: str
    mission: Optional[str] = None
    location: Optional[str] = None
    players: List[Dict[str, str]] = Field(default_factory=list)  # [{steamid, player_name}]


class RoundEndIn(BaseModel):
    """Game server reports round end."""
    server_name: Optional[str] = None
    mission_completed: bool = False
    reached_station: bool = False
    players: List[Dict[str, str]] = Field(default_factory=list)  # [{steamid, player_name}]


class AnticheatEventIn(BaseModel):
    player_steamid: str
    player_name: Optional[str] = None
    event_type: str
    details: Optional[Dict[str, Any]] = None
    occurred_at: Optional[datetime] = None


class AnticheatEventOut(BaseModel):
    id: int
    player_steamid: str
    player_name: Optional[str] = None
    player_id: Optional[int] = None
    event_type: str
    details: Optional[Dict[str, Any]] = None
    occurred_at: Optional[datetime] = None
    reviewed: bool = False

    class Config:
        from_attributes = True


class ServerActionOut(BaseModel):
    id: int
    action_type: str
    target_steamid: str
    reason: Optional[str] = None
    duration_minutes: Optional[int] = None
    message_text: Optional[str] = None
    server_name: Optional[str] = None  # Backward compat alias for target_server
    queued_at: Optional[datetime] = None
    ack_asper1: int = 0
    ack_asper2: int = 0

    class Config:
        from_attributes = True

    @classmethod
    def from_v2(cls, row):
        """Convert ServerAction (v2) to the API response format (backward compat)."""
        return cls(
            id=row.id,
            action_type=row.action_type,
            target_steamid=row.target_steamid,
            reason=row.reason,
            duration_minutes=row.duration_minutes,
            message_text=row.message_text,
            server_name=row.target_server,
            queued_at=row.created_at,
            ack_asper1=row.status_asper1,
            ack_asper2=row.status_asper2,
        )


class ServerActionAck(BaseModel):
    success: bool
    error_message: Optional[str] = None
    server_name: Optional[str] = None  # Имя сервера, отправляющего ACK


class QueueActionIn(BaseModel):
    action_type: str
    target_steamid: str
    reason: Optional[str] = None
    duration_minutes: Optional[int] = None
    message_text: Optional[str] = None
    server_name: Optional[str] = None


class QueueActionResult(BaseModel):
    status: str
    action_id: int

