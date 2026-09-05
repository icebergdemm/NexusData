"""Player chat and voice communication ingest/admin APIs."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, desc, or_
from sqlalchemy.orm import Session, joinedload

from database import get_db, get_game_db
from models import (
    AdminUser,
    AppSetting,
    PlayerCommunication,
    PlayerCommunicationRecipient,
    PlayerData,
    RoundEvent,
)
from routes.admin import get_current_admin, log_admin_action
from schemas import CommunicationBatchIn
from security import check_permission, verify_external_api_key_dependency


router = APIRouter()
api_router = APIRouter(
    prefix="/api/v1/server",
    tags=["player-communications"],
    dependencies=[Depends(verify_external_api_key_dependency)],
)

RETENTION_SETTING = "communications_retention_days"
DEFAULT_RETENTION_DAYS = 90
MAX_RETENTION_DAYS = 3650


def _round_event_for(db: Session, event: dict) -> Optional[RoundEvent]:
    token = event.get("round_token")
    if not token:
        return None
    filters = (
        RoundEvent.server_name == event.get("server_name"),
        RoundEvent.server_session_id == event.get("server_session_id"),
        RoundEvent.round_token == token,
    )
    # Use the finished round as the UI/replay foreign key when it already
    # exists. During a live round the start row is still useful; round/end
    # reconciliation below replaces it with the end row. Time is deliberately
    # absent from this lookup — clocks are not an identity system.
    for event_type in ("end", "start"):
        row = (
            db.query(RoundEvent)
            .filter(RoundEvent.event_type == event_type, *filters)
            .order_by(desc(RoundEvent.created_at), desc(RoundEvent.id))
            .first()
        )
        if row is not None:
            return row
    return None


def _player_id(db: Session, steamid: Optional[str]) -> Optional[int]:
    if not steamid:
        return None
    return db.query(PlayerData.player_id).filter(PlayerData.player_steamid == steamid).scalar()


def _upsert_recipient(db: Session, communication: PlayerCommunication, payload: dict) -> None:
    steamid = payload.get("player_steamid")
    recipient = next(
        (r for r in communication.recipients if r.player_steamid == steamid),
        None,
    )
    if recipient is None:
        recipient = PlayerCommunicationRecipient(
            communication_id=communication.id,
            player_steamid=steamid,
            player_id=_player_id(db, steamid),
            packet_count=int(payload["packet_count"]) if payload.get("packet_count") is not None else 1,
        )
        communication.recipients.append(recipient)

    for key in (
        "player_name", "delivered_text", "server_queued", "delivery_allowed",
        "channel", "is_radio", "is_dead", "distance_factor", "first_observed_at",
        "last_observed_at",
    ):
        value = payload.get(key)
        if value is not None:
            setattr(recipient, key, value)
    packet_count = payload.get("packet_count")
    if packet_count is not None:
        recipient.packet_count = max(recipient.packet_count or 0, int(packet_count))


@api_router.post("/communications/batch")
def ingest_communications(body: CommunicationBatchIn, db: Session = Depends(get_game_db)):
    """Idempotent game-server batch ingest. No audio is accepted or stored."""
    inserted = 0
    updated = 0
    for raw in body.events:
        event = raw.model_dump()
        recipients = event.pop("recipients", [])
        event["server_name"] = body.server_name.strip().upper()
        event["server_session_id"] = body.server_session_id
        existing = (
            db.query(PlayerCommunication)
            .options(joinedload(PlayerCommunication.recipients))
            .filter(PlayerCommunication.event_uid == event["event_uid"])
            .first()
        )
        if existing is None:
            existing = PlayerCommunication(
                event_uid=event.pop("event_uid"),
                recipients=[],
                player_id=_player_id(db, event.get("player_steamid")),
                round_event=_round_event_for(db, event),
                **{k: v for k, v in event.items() if k != "recipients"},
            )
            db.add(existing)
            db.flush()
            inserted += 1
        else:
            for key, value in event.items():
                if key not in {"event_uid", "server_name", "server_session_id"} and value is not None:
                    setattr(existing, key, value)
            if existing.round_event is None:
                existing.round_event = _round_event_for(db, event)
            updated += 1

        for recipient in recipients:
            _upsert_recipient(db, existing, recipient)

    db.commit()
    return {"status": "ok", "inserted": inserted, "updated": updated, "processed": inserted + updated}


def _require_view(admin: AdminUser) -> None:
    if not check_permission(admin, "moderation", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid datetime: {value}") from exc


def _event_dict(event: PlayerCommunication, include_recipients: bool = False) -> dict:
    result = {
        "id": event.id,
        "event_uid": event.event_uid,
        "source": event.source,
        "channel": event.channel,
        "server_name": event.server_name,
        "server_session_id": event.server_session_id,
        "round_token": event.round_token,
        "round_id": event.round_event_id,
        "round_finished": bool(event.round_event and event.round_event.event_type == "end"),
        "player_steamid": event.player_steamid,
        "player_id": event.player_id,
        "player_name": event.player_name,
        "role": event.role,
        "submarine_name": event.submarine_name,
        "hull_id": event.hull_id,
        "hull_name": event.hull_name,
        "position_x": event.position_x,
        "position_y": event.position_y,
        "original_text": event.original_text,
        "transcript_text": event.transcript_text,
        "asr_status": event.asr_status,
        "asr_confidence": event.asr_confidence,
        "asr_duration_ms": event.asr_duration_ms,
        "asr_error": event.asr_error,
        "occurred_at": event.occurred_at,
        "ended_at": event.ended_at,
        "recipient_count": len(event.recipients),
    }
    if include_recipients:
        result["recipients"] = [
            {
                "id": r.id,
                "player_steamid": r.player_steamid,
                "player_id": r.player_id,
                "player_name": r.player_name,
                "delivered_text": r.delivered_text,
                "server_queued": r.server_queued,
                "delivery_allowed": r.delivery_allowed,
                "channel": r.channel,
                "is_radio": r.is_radio,
                "is_dead": r.is_dead,
                "distance_factor": r.distance_factor,
                "packet_count": r.packet_count,
                "first_observed_at": r.first_observed_at,
                "last_observed_at": r.last_observed_at,
            }
            for r in event.recipients
        ]
    return result


@router.get("/api/v1/admin/communications")
def list_communications(
    request: Request,
    server: Optional[str] = None,
    player_steamid: Optional[str] = None,
    player_id: Optional[int] = None,
    source: Optional[str] = Query(None, pattern="^(chat|voice)$"),
    channel: Optional[str] = None,
    asr_status: Optional[str] = None,
    round_id: Optional[int] = None,
    lobby: bool = False,
    search: Optional[str] = None,
    time_from: Optional[str] = None,
    time_to: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_view(current_admin)
    query = db.query(PlayerCommunication).options(joinedload(PlayerCommunication.recipients))
    if server:
        query = query.filter(PlayerCommunication.server_name == server)
    if player_steamid:
        query = query.filter(PlayerCommunication.player_steamid == player_steamid)
    if player_id:
        query = query.filter(PlayerCommunication.player_id == player_id)
    if source:
        query = query.filter(PlayerCommunication.source == source)
    if channel:
        query = query.filter(PlayerCommunication.channel == channel)
    if asr_status:
        query = query.filter(PlayerCommunication.asr_status == asr_status)
    if round_id is not None:
        query = query.filter(PlayerCommunication.round_event_id == round_id)
    elif lobby:
        query = query.filter(PlayerCommunication.round_event_id.is_(None))
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                PlayerCommunication.original_text.like(pattern),
                PlayerCommunication.transcript_text.like(pattern),
                PlayerCommunication.player_name.like(pattern),
                PlayerCommunication.event_uid.like(pattern),
            )
        )
    start = _parse_datetime(time_from)
    end = _parse_datetime(time_to)
    if start:
        query = query.filter(PlayerCommunication.occurred_at >= start)
    if end:
        query = query.filter(PlayerCommunication.occurred_at <= end)

    total = query.count()
    rows = (
        query.order_by(desc(PlayerCommunication.occurred_at), desc(PlayerCommunication.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "events": [_event_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "has_next": page * page_size < total,
    }


@router.get("/api/v1/admin/communications/{communication_id}")
def get_communication(
    communication_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_view(current_admin)
    row = (
        db.query(PlayerCommunication)
        .options(joinedload(PlayerCommunication.recipients))
        .filter(PlayerCommunication.id == communication_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Communication not found")
    return _event_dict(row, include_recipients=True)


@router.get("/api/v1/admin/communications/{communication_id}/recipients")
def get_communication_recipients(
    communication_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_view(current_admin)
    row = (
        db.query(PlayerCommunication)
        .options(joinedload(PlayerCommunication.recipients))
        .filter(PlayerCommunication.id == communication_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Communication not found")
    return {"communication_id": row.id, "recipients": _event_dict(row, include_recipients=True)["recipients"]}


@router.get("/api/v1/admin/communications/config")
def get_communication_config(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_view(current_admin)
    row = db.query(AppSetting).filter(AppSetting.key == RETENTION_SETTING).first()
    value = int(row.value) if row and row.value else DEFAULT_RETENTION_DAYS
    return {"retention_days": value, "default_days": DEFAULT_RETENTION_DAYS, "max_days": MAX_RETENTION_DAYS}


@router.patch("/api/v1/admin/communications/config")
async def set_communication_config(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "admin", "edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    body = await request.json()
    try:
        value = int(body.get("retention_days"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="retention_days must be an integer") from exc
    if not 1 <= value <= MAX_RETENTION_DAYS:
        raise HTTPException(status_code=422, detail=f"retention_days must be between 1 and {MAX_RETENTION_DAYS}")
    row = db.query(AppSetting).filter(AppSetting.key == RETENTION_SETTING).first()
    if row is None:
        row = AppSetting(key=RETENTION_SETTING)
        db.add(row)
    row.value = str(value)
    db.commit()
    log_admin_action(db, current_admin.id, "communications_retention_update", {"retention_days": value})
    return {"retention_days": value}


def purge_expired_communications(db: Session) -> int:
    row = db.query(AppSetting).filter(AppSetting.key == RETENTION_SETTING).first()
    days = int(row.value) if row and row.value else DEFAULT_RETENTION_DAYS
    cutoff = datetime.utcnow() - timedelta(days=max(1, min(days, MAX_RETENTION_DAYS)))
    expired_ids = [row[0] for row in db.query(PlayerCommunication.id).filter(PlayerCommunication.occurred_at < cutoff).all()]
    if not expired_ids:
        return 0
    # Explicit child deletion keeps the cleanup deterministic on local SQLite
    # test databases too; MySQL's ON DELETE CASCADE remains the safety net for
    # direct parent deletion elsewhere.
    db.query(PlayerCommunicationRecipient).filter(
        PlayerCommunicationRecipient.communication_id.in_(expired_ids)
    ).delete(synchronize_session=False)
    deleted = db.query(PlayerCommunication).filter(PlayerCommunication.id.in_(expired_ids)).delete(synchronize_session=False)
    db.commit()
    return int(deleted or 0)


router.include_router(api_router)
