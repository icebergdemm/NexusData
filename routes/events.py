from fastapi import APIRouter, Depends
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


@router.post("", response_model=MessageProcessResult)
def ingest_event(
    message: MessageEnvelope,
    db: Session = Depends(get_db),
):
    """Universal ingress endpoint for game server and external sources."""
    dispatcher = EventDispatcherService(db)
    return dispatcher.process(message)
