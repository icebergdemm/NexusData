from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from schemas import MessageEnvelope, MessageProcessResult
from security import verify_external_api_key_dependency
from services import EventDispatcherService

settings = get_settings()

router = APIRouter(
    prefix="/api/v1/events",
    tags=["events"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)


class LegacyGameEvent(BaseModel):
    type: str
    timestamp: Optional[int] = None
    server_name: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class LegacyEventBatch(BaseModel):
    server_name: Optional[str] = None
    api_key: Optional[str] = None
    events: List[LegacyGameEvent] = Field(default_factory=list)


@router.post("", response_model=MessageProcessResult)
def ingest_event(
    message: MessageEnvelope,
    db: Session = Depends(get_db),
):
    """Universal ingress endpoint for game server and external sources."""
    dispatcher = EventDispatcherService(db)
    return dispatcher.process(message)


@router.post("/batch")
def ingest_legacy_batch(
    batch: LegacyEventBatch,
    db: Session = Depends(get_db),
):
    """Accept the legacy game-plugin batch envelope during API migration."""
    dispatcher = EventDispatcherService(db)
    processed = 0
    for event in batch.events:
        if event.type == "player.join":
            data = event.data
            dispatcher.process(MessageEnvelope(
                type="player.upsert",
                version=1,
                source=event.server_name or batch.server_name or "barotrauma",
                payload={
                    "player_steamid": str(data.get("steamid") or data.get("steam_id") or ""),
                    "player_last_time_name": str(data.get("name") or "Unknown"),
                    "last_ip": data.get("ip"),
                },
            ))
            processed += 1
    return {"status": "accepted", "processed": processed, "received": len(batch.events)}
