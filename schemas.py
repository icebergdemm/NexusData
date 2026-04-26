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
    items: dict

    class Config:
        from_attributes = True


class PlayerDataBase(BaseModel):
    player_last_time_name: str = Field(min_length=1, max_length=255)
    player_steamid: str = Field(min_length=17, max_length=18)
    play_time: int = Field(default=0, ge=0, description="Play time in seconds")
    rank_id: int = Field(default=0, ge=0)
    kit_level: int = Field(default=0, ge=0)
    achievements: List[str] = Field(default_factory=list)
    
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
    last_login_time: Optional[str] = None
    last_ip: Optional[str] = Field(None, max_length=45, description="Last IP address")
    last_subnet: Optional[str] = Field(None, max_length=45, description="Last subnet (e.g., 192.168.1.0/24)")
    first_login: Optional[str] = None
    avg_ping: Optional[int] = Field(None, ge=0, description="Average ping")


class PlayerDataUpdate(BaseModel):
    player_last_time_name: Optional[str] = Field(None, min_length=1, max_length=255)
    last_login_time: Optional[str] = None
    play_time: Optional[int] = Field(None, ge=0)
    rank_id: Optional[int] = Field(None, ge=0)
    kit_level: Optional[int] = Field(None, ge=0)
    achievements: Optional[List[str]] = None


class PlayerDataOut(PlayerDataBase):
    player_id: int
    last_login_time: datetime
    rank_name: Optional[str] = None
    kit_name: Optional[str] = None

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

