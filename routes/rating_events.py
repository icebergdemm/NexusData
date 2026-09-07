"""Versioned game-server ingest for the rating v2 event ledger."""

from datetime import datetime, timedelta
import logging
from uuid import uuid4
from typing import Any, Dict, List, Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import get_settings
from database import GameSessionLocal, get_game_db
from models import RatingCalculationRun, RatingConfigVersion, RatingDeliveryLog, RatingEventLedger, RatingProjectionState, RatingRankHistory
from rating_v2_engine import EVENT_WEIGHTS
from rating_constants import ROLES
from rating_v2_engine import FORMULA_VERSION, calculate_event_delta, diminishing_factor, rating_from_points
from security import verify_external_api_key_dependency


logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(
    prefix="/api/v2/rating",
    tags=["rating-events-v2"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)


class RatingEventIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    version: Literal[1]
    steam_id: str = Field(min_length=10, max_length=50)
    mode: Literal["mission", "pvp"]
    role: str = Field(min_length=1, max_length=32)
    event_type: str = Field(min_length=1, max_length=64)
    raw_value: float | None = None
    points_delta: float | None = None  # rejected below; retained only for clear error messages
    occurred_at: datetime
    server_name: str | None = None
    server_session_id: str | None = None
    round_token: str | None = None
    plugin_version: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # Compatibility input for the first dev contract. New servers send the
    # flat fields above; these two fields are folded into the same contexts.
    source: Dict[str, Any] | None = None
    round: Dict[str, Any] | None = None

    @field_validator("event_id", "steam_id", "role", "event_type")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("role")
    @classmethod
    def require_known_role(cls, value: str) -> str:
        if value not in ROLES:
            raise ValueError("unsupported role")
        return value

    @field_validator("raw_value", "points_delta")
    @classmethod
    def require_finite_value(cls, value: float | None) -> float | None:
        if value is not None and (value != value or value in (float("inf"), float("-inf"))):
            raise ValueError("must be finite")
        return value

    @model_validator(mode="after")
    def require_rating_value(self):
        if self.raw_value is None:
            raise ValueError("raw_value is required; points_delta is server-owned")
        source = self.source or {}
        round_context = self.round or {}
        if not self.server_session_id:
            self.server_session_id = source.get("server_session_id")
        if not self.round_token:
            self.round_token = round_context.get("round_token")
        if not self.server_name:
            self.server_name = source.get("server_name") or source.get("server")
        if not self.plugin_version:
            self.plugin_version = source.get("plugin_version")
        if not self.server_name or not self.server_session_id or not self.round_token:
            raise ValueError("server_name, server_session_id and round_token are required")
        self.source = {
            "server_name": self.server_name or "",
            "server_session_id": self.server_session_id,
            "plugin_version": self.plugin_version or "",
            **source,
        }
        self.round = {
            "round_token": self.round_token,
            **round_context,
        }
        return self


class RatingEventBatchIn(BaseModel):
    events: List[object] = Field(min_length=1)


def _event_result(raw_event: object, status: str, reason: str | None = None) -> dict:
    event_id = raw_event.get("event_id") if isinstance(raw_event, dict) else None
    result = {"event_id": event_id, "status": status}
    if reason:
        result["reason"] = reason
    return result


def _event_conflicts(existing: RatingEventLedger, incoming: RatingEventIn) -> bool:
    """An event id is immutable; a reused id with another payload is corruption."""
    existing_occurred_at = existing.occurred_at.replace(tzinfo=None) if existing.occurred_at else None
    incoming_occurred_at = incoming.occurred_at.replace(tzinfo=None) if incoming.occurred_at else None
    return any((
        existing.version != incoming.version,
        existing.steam_id != incoming.steam_id,
        existing.mode != incoming.mode,
        existing.role != incoming.role,
        existing.event_type != incoming.event_type,
        float(existing.raw_value or 0.0) != float(incoming.raw_value or 0.0),
        existing_occurred_at != incoming_occurred_at,
        (existing.source_context or {}) != (incoming.source or {}),
        (existing.round_context or {}) != (incoming.round or {}),
        (existing.event_metadata or {}) != (incoming.metadata or {}),
    ))


def _record_delivery(db: Session, raw_event: object, status: str, reason: str | None = None) -> None:
    event_id = raw_event.get("event_id") if isinstance(raw_event, dict) else None
    mode = raw_event.get("mode") if isinstance(raw_event, dict) else None
    role = raw_event.get("role") if isinstance(raw_event, dict) else None
    db.add(RatingDeliveryLog(event_id=event_id, mode=mode, role=role, status=status, reason=reason))


def _weights_for_mode(db: Session, mode: str) -> tuple[dict, str]:
    config = db.query(RatingConfigVersion).filter(
        RatingConfigVersion.mode == mode,
    ).order_by(RatingConfigVersion.created_at.desc(), RatingConfigVersion.id.desc()).first()
    return (
        config.config.get("WEIGHT_MATRIX", EVENT_WEIGHTS.get(mode, {})) if config else EVENT_WEIGHTS.get(mode, {}),
        config.version if config else "default",
    )


def _project_rating_events(event_ids: List[int]) -> None:
    """Apply committed ledger rows in a separate game-pool session."""
    db = GameSessionLocal()
    try:
        events = (
            db.query(RatingEventLedger)
            .filter(RatingEventLedger.id.in_(event_ids), RatingEventLedger.projected_at.is_(None))
            .order_by(RatingEventLedger.id)
            .with_for_update()
            .all()
        )
        now = datetime.utcnow()
        mode_configs = {}
        for mode in {event.mode for event in events}:
            mode_configs[mode] = _weights_for_mode(db, mode)
        runs = {}
        for mode in {event.mode for event in events}:
            run = RatingCalculationRun(
                run_id=uuid4().hex,
                mode=mode,
                formula_version=FORMULA_VERSION,
                config_version=mode_configs[mode][1],
                status="running",
                event_count=sum(1 for event in events if event.mode == mode),
                started_at=now,
            )
            db.add(run)
            runs[mode] = run
        for event in events:
            state = (
                db.query(RatingProjectionState)
                .filter(
                    RatingProjectionState.steam_id == event.steam_id,
                    RatingProjectionState.mode == event.mode,
                    RatingProjectionState.role == event.role,
                )
                .with_for_update()
                .first()
            )
            if state is None:
                state = RatingProjectionState(
                    steam_id=event.steam_id,
                    mode=event.mode,
                    role=event.role,
                )
                db.add(state)
                db.flush()

            if event.mapping_status == "mapped":
                action_counts = dict(state.action_counts or {})
                previous_count = int(action_counts.get(event.event_type, 0))
                multiplier = diminishing_factor(previous_count)
                delta = (event.points_delta or 0.0) * multiplier
                action_counts[event.event_type] = previous_count + 1
                state.action_counts = action_counts
            else:
                delta = 0.0
                state.unmapped_events = (state.unmapped_events or 0) + 1
            state.cumulative_points = max(0.0, state.cumulative_points + delta)
            state.current_rating = rating_from_points(state.cumulative_points, state.qualified_rounds or 0)
            round_token = (event.round_context or {}).get("round_token")
            round_tokens = list(state.round_tokens or [])
            if round_token and round_token not in round_tokens and event.mapping_status == "mapped":
                round_tokens.append(round_token)
                state.round_tokens = round_tokens[-1000:]
                state.qualified_rounds = len(state.round_tokens)
                state.current_rating = rating_from_points(state.cumulative_points, state.qualified_rounds)
            if state.last_event_at is None or event.occurred_at > state.last_event_at:
                state.last_event_at = event.occurred_at
            state.updated_at = now
            event.projected_at = now
        for run in runs.values():
            run.status = "completed"
            run.completed_at = now
        _snapshot_rank_history(db, events, now)
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.exception("rating v2 projection failed for ledger ids %s", event_ids)
        return False
    finally:
        db.close()


def _snapshot_rank_history(db: Session, events: list[RatingEventLedger], now: datetime) -> None:
    """Capture at most one position snapshot per mode/role per hour."""
    keys = {(event.mode, event.role) for event in events}
    for mode, role in keys:
        latest = db.query(RatingRankHistory.id).filter(
            RatingRankHistory.mode == mode,
            RatingRankHistory.role == role,
        ).order_by(RatingRankHistory.captured_at.desc()).first()
        if latest:
            latest_row = db.query(RatingRankHistory).filter(RatingRankHistory.id == latest[0]).first()
            if latest_row and latest_row.captured_at and now - latest_row.captured_at < timedelta(hours=1):
                continue
        states = db.query(RatingProjectionState).filter(
            RatingProjectionState.mode == mode,
            RatingProjectionState.role == role,
            RatingProjectionState.current_rating > 0,
        ).order_by(RatingProjectionState.current_rating.desc(), RatingProjectionState.steam_id).all()
        for rank, state in enumerate(states, 1):
            db.add(RatingRankHistory(
                steam_id=state.steam_id,
                mode=mode,
                role=role,
                rank=rank,
                rating=state.current_rating,
                cumulative_points=state.cumulative_points,
                captured_at=now,
            ))


@router.post("/events:batch")
def ingest_rating_events(
    body: RatingEventBatchIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_game_db),
):
    """Append events once and asynchronously project only newly accepted rows."""
    accepted_ids: List[int] = []
    results = []

    for raw_event in body.events:
        try:
            event = RatingEventIn.model_validate(raw_event)
        except ValidationError as exc:
            reason = exc.errors()[0]["msg"]
            _record_delivery(db, raw_event, "rejected", reason)
            results.append(_event_result(raw_event, "rejected", reason))
            continue

        existing = db.query(RatingEventLedger).filter(RatingEventLedger.event_id == event.event_id).first()
        if existing:
            if _event_conflicts(existing, event):
                reason = "event_id already exists with a different payload"
                _record_delivery(db, raw_event, "rejected", reason)
                results.append(_event_result(raw_event, "rejected", reason))
                continue
            _record_delivery(db, raw_event, "duplicate")
            results.append(_event_result(raw_event, "duplicate"))
            continue

        weights, config_version = _weights_for_mode(db, event.mode)
        points_delta, mapping_status = calculate_event_delta(
            event.mode, event.role, event.event_type, event.raw_value, weights=weights
        )

        ledger = RatingEventLedger(
            event_id=event.event_id,
            version=event.version,
            steam_id=event.steam_id,
            mode=event.mode,
            role=event.role,
            event_type=event.event_type,
            raw_value=event.raw_value if event.raw_value is not None else event.points_delta,
            points_delta=points_delta,
            mapping_status=mapping_status,
            formula_version=FORMULA_VERSION,
            config_version=config_version,
            occurred_at=event.occurred_at,
            source_context=event.source or {},
            round_context=event.round or {},
            event_metadata=event.metadata,
        )
        try:
            with db.begin_nested():
                db.add(ledger)
                db.flush()
        except IntegrityError:
            _record_delivery(db, raw_event, "duplicate")
            results.append(_event_result(raw_event, "duplicate"))
            continue

        accepted_ids.append(ledger.id)
        _record_delivery(db, raw_event, "accepted")
        results.append(_event_result(raw_event, "accepted"))

    db.commit()
    if accepted_ids:
        background_tasks.add_task(_project_rating_events, accepted_ids)
    return {"results": results}
