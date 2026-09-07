"""Admin read/configuration API for the versioned rating projection."""

from datetime import datetime
import json
import math
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, func
from sqlalchemy.orm import Session, joinedload

from config import get_settings
from database import get_db
from models import (
    AdminSession,
    AdminUser,
    PlayerData,
    RatingAdjustment,
    RatingCalculationRun,
    RatingConfigVersion,
    RatingDeliveryLog,
    RatingEventLedger,
    RatingProjectionState,
    RatingRankHistory,
    PlayerRating,
)
from rating_constants import ROLES
from rating_v2_engine import EVENT_WEIGHTS, FORMULA_VERSION, activity_factor, calculate_event_delta, diminishing_factor, rating_from_points
from security import check_permission
from .admin import ensure_csrf_or_valid_admin_session


router = APIRouter(tags=["rating-admin-v2"])
settings = get_settings()


def get_current_admin_v2(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get("admin_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session = db.query(AdminSession).filter(
        AdminSession.token == token,
        AdminSession.revoked == 0,
    ).first()
    if not session or (session.expires_at is not None and session.expires_at <= datetime.utcnow()):
        raise HTTPException(status_code=401, detail="Not authenticated")
    admin = (
        db.query(AdminUser)
        .options(joinedload(AdminUser.privilege_group))
        .filter(AdminUser.id == session.admin_id)
        .first()
    )
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    from security import compute_department_site_permissions
    admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
    return admin


def _require_view(admin: AdminUser) -> None:
    if not check_permission(admin, "ratings", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")


def _require_edit(admin: AdminUser) -> None:
    if not check_permission(admin, "ratings", "edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")


def _validate_role(role: str) -> str:
    if role not in ROLES:
        raise HTTPException(status_code=422, detail="Unsupported role")
    return role


def _validate_role_filter(role: str) -> Optional[str]:
    """Validate the optional role filter used by the combined leaderboard."""
    if role == "all":
        return None
    return _validate_role(role)


def _validate_mode(mode: str) -> str:
    if mode not in {"mission", "pvp"}:
        raise HTTPException(status_code=422, detail="Unsupported rating mode")
    return mode


def _player_map(db: Session, steam_ids: list[str]) -> dict[str, PlayerData]:
    if not steam_ids:
        return {}
    players = db.query(PlayerData).filter(PlayerData.player_steamid.in_(steam_ids)).all()
    return {player.player_steamid: player for player in players}


def _state_row(state: RatingProjectionState, player: Optional[PlayerData], event_count: int = 0) -> dict:
    round_tokens = list(state.round_tokens or [])
    is_legacy = any(str(token).startswith("legacy:") for token in round_tokens)
    if is_legacy:
        data_quality = "legacy"
    elif state.qualified_rounds >= 10:
        data_quality = "sufficient_data"
    elif event_count:
        data_quality = "insufficient_data"
    else:
        data_quality = "no_data"
    return {
        "steam_id": state.steam_id,
        "player_name": player.display_name if player else state.steam_id,
        "player_id": player.player_id if player else None,
        "rating": round(state.current_rating, 2),
        "current_rating": round(state.current_rating, 2),
        "cumulative_points": round(state.cumulative_points, 2),
        "last_activity": state.last_event_at,
        "last_activity_date": state.last_event_at,
        "play_time_seconds": player.play_time if player else 0,
        "play_time_hours": round(player.play_time_hours, 2) if player else 0.0,
        "event_count": event_count,
        "qualified_rounds": state.qualified_rounds,
        "unmapped_events": state.unmapped_events,
        "eligibility": "eligible" if data_quality in {"legacy", "sufficient_data"} else "insufficient_data",
        "data_quality": data_quality,
        # This used to be displayed as a fake percentage. It was only
        # qualified_rounds / 10, not statistical confidence.
        "confidence": None,
    }


def _event_count_by_role(db: Session, steam_id: str, mode: str) -> dict[str, int]:
    return {
        role: int(count)
        for role, count in db.query(
            RatingEventLedger.role, func.count(RatingEventLedger.id)
        ).filter(
            RatingEventLedger.steam_id == steam_id,
            RatingEventLedger.mode == mode,
        ).group_by(RatingEventLedger.role).all()
    }


@router.get("/api/v2/admin/ratings")
def get_rating_leaderboard_v2(
    mode: str = Query("mission"),
    role: str = Query("captain"),
    limit: Optional[int] = Query(None, ge=1, le=500),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    admin: AdminUser = Depends(get_current_admin_v2),
    db: Session = Depends(get_db),
):
    _require_view(admin)
    mode = _validate_mode(mode)
    role_filter = _validate_role_filter(role)
    effective_page_size = limit or page_size
    offset = 0 if limit else (page - 1) * effective_page_size
    base_query = db.query(RatingProjectionState).filter(
        RatingProjectionState.mode == mode,
        RatingProjectionState.current_rating > 0,
    )
    if role_filter:
        base_query = base_query.filter(RatingProjectionState.role == role_filter)
    total = base_query.count()
    states = (
        base_query
        .order_by(desc(RatingProjectionState.current_rating), RatingProjectionState.role, RatingProjectionState.steam_id)
        .offset(offset)
        .limit(effective_page_size)
        .all()
    )
    players = _player_map(db, [state.steam_id for state in states])
    count_query = db.query(RatingEventLedger.steam_id, RatingEventLedger.role, func.count(RatingEventLedger.id)).filter(
        RatingEventLedger.mode == mode,
        RatingEventLedger.steam_id.in_(players.keys() or ["-"]),
    )
    if role_filter:
        count_query = count_query.filter(RatingEventLedger.role == role_filter)
    counts = {
        (steam_id, event_role): count
        for steam_id, event_role, count in count_query
        .group_by(RatingEventLedger.steam_id, RatingEventLedger.role)
        .all()
    }
    leaderboard = []
    for index, state in enumerate(states):
        row = _state_row(state, players.get(state.steam_id), counts.get((state.steam_id, state.role), 0))
        row["role"] = state.role
        row["rank"] = offset + index + 1
        leaderboard.append(row)
    total_pages = max(1, math.ceil(total / effective_page_size))
    return {
        "mode": mode,
        "selected_role": role_filter or "all",
        "roles": ROLES,
        "leaderboard": leaderboard,
        "page": 1 if limit else page,
        "page_size": effective_page_size,
        "total": total,
        "total_pages": total_pages,
        "has_next": (offset + len(states)) < total,
        "formula_version": FORMULA_VERSION,
        "generated_at": datetime.utcnow(),
        "can_edit": check_permission(admin, "ratings", "edit"),
        "is_superuser": bool(admin.is_superuser),
    }


@router.get("/api/v2/admin/ratings/{steam_id}")
def get_rating_player_v2(
    steam_id: str,
    mode: str = Query("mission"),
    role: str = Query("captain"),
    admin: AdminUser = Depends(get_current_admin_v2),
    db: Session = Depends(get_db),
):
    _require_view(admin)
    mode = _validate_mode(mode)
    player = db.query(PlayerData).filter(PlayerData.player_steamid == steam_id).first()
    if role == "all":
        states = db.query(RatingProjectionState).filter(
            RatingProjectionState.steam_id == steam_id,
            RatingProjectionState.mode == mode,
        ).all()
        state_by_role = {state.role: state for state in states}
        event_counts = _event_count_by_role(db, steam_id, mode)
        legacy_rows = db.query(PlayerRating).filter(
            PlayerRating.steam_id == steam_id,
        ).all() if mode == "mission" else []
        legacy_by_role = {row.role: {
            "rating": round(row.current_rating, 2),
            "cumulative_points": round(row.cumulative_points, 2),
            "source": "player_ratings",
        } for row in legacy_rows}
        ratings = []
        for role_name in ROLES:
            state = state_by_role.get(role_name)
            event_count = event_counts.get(role_name, 0)
            if state is None and not event_count and role_name not in legacy_by_role:
                continue
            row = _state_row(state, player, event_count) if state else {
                "steam_id": steam_id,
                "player_name": player.display_name if player else steam_id,
                "player_id": player.player_id if player else None,
                "rating": None,
                "current_rating": None,
                "cumulative_points": 0.0,
                "last_activity": None,
                "last_activity_date": None,
                "play_time_seconds": player.play_time if player else 0,
                "play_time_hours": round(player.play_time_hours, 2) if player else 0.0,
                "event_count": event_count,
                "qualified_rounds": 0,
                "unmapped_events": 0,
                "eligibility": "insufficient_data",
                "data_quality": "insufficient_data" if event_count else "no_data",
                "confidence": None,
            }
            row["role"] = role_name
            row["legacy_comparison"] = legacy_by_role.get(role_name)
            ratings.append(row)
        return {
            "steam_id": steam_id,
            "mode": mode,
            "role": "all",
            "roles": ROLES,
            "ratings": ratings,
        }

    role = _validate_role(role)
    state = db.query(RatingProjectionState).filter(
        RatingProjectionState.steam_id == steam_id,
        RatingProjectionState.mode == mode,
        RatingProjectionState.role == role,
    ).first()
    event_count = db.query(func.count(RatingEventLedger.id)).filter(
        RatingEventLedger.steam_id == steam_id,
        RatingEventLedger.mode == mode,
        RatingEventLedger.role == role,
    ).scalar() or 0
    legacy = db.query(PlayerRating).filter(
        PlayerRating.steam_id == steam_id,
        PlayerRating.role == role,
    ).first()
    legacy_comparison = {
        "rating": round(legacy.current_rating, 2),
        "cumulative_points": round(legacy.cumulative_points, 2),
        "source": "player_ratings",
    } if legacy else None
    if state is None:
        return {
            "steam_id": steam_id, "mode": mode, "role": role, "rating": None,
            "event_count": event_count, "legacy_comparison": legacy_comparison,
        }
    row = _state_row(state, player, event_count)
    row["legacy_comparison"] = legacy_comparison
    return {"steam_id": steam_id, "mode": mode, "role": role, "rating": row}


@router.get("/api/v2/admin/ratings/{steam_id}/history")
def get_rating_rank_history_v2(
    steam_id: str,
    mode: str = Query("mission"),
    role: str = Query("captain"),
    limit: int = Query(100, ge=1, le=500),
    admin: AdminUser = Depends(get_current_admin_v2),
    db: Session = Depends(get_db),
):
    _require_view(admin)
    mode = _validate_mode(mode)
    query = db.query(RatingRankHistory).filter(
        RatingRankHistory.steam_id == steam_id,
        RatingRankHistory.mode == mode,
    )
    if role != "all":
        query = query.filter(RatingRankHistory.role == _validate_role(role))
    rows = query.order_by(RatingRankHistory.captured_at.desc(), RatingRankHistory.id.desc()).limit(limit).all()
    history = [{
        "role": row.role,
        "rank": row.rank,
        "rating": round(row.rating, 2),
        "cumulative_points": round(row.cumulative_points, 2),
        "captured_at": row.captured_at,
    } for row in rows]
    return {"steam_id": steam_id, "mode": mode, "role": role, "history": history}


def _rating_explanation_for_role(db: Session, steam_id: str, mode: str, role: str) -> dict:
    events = db.query(RatingEventLedger).filter(
        RatingEventLedger.steam_id == steam_id,
        RatingEventLedger.mode == mode,
        RatingEventLedger.role == role,
    ).order_by(RatingEventLedger.occurred_at, RatingEventLedger.id).all()
    state = db.query(RatingProjectionState).filter(
        RatingProjectionState.steam_id == steam_id,
        RatingProjectionState.mode == mode,
        RatingProjectionState.role == role,
    ).first()
    adjustments = db.query(RatingAdjustment).filter(
        RatingAdjustment.steam_id == steam_id,
        RatingAdjustment.mode == mode,
        RatingAdjustment.role == role,
    ).order_by(RatingAdjustment.created_at, RatingAdjustment.id).all()
    contributions: dict[str, float] = {}
    for event in events:
        contributions[event.event_type] = round(contributions.get(event.event_type, 0.0) + event.points_delta, 2)
    adjustment_total = sum(adjustment.delta for adjustment in adjustments)
    if adjustment_total:
        contributions["manual_adjustment"] = round(adjustment_total, 2)
    points = state.cumulative_points if state else sum(event.points_delta for event in events) + adjustment_total
    return {
        "steam_id": steam_id,
        "mode": mode,
        "role": role,
        "current_rating": round(state.current_rating, 2) if state else rating_from_points(max(points, 0.0), 0),
        "cumulative_points": round(points, 2),
        "event_count": len(events),
        "contributions": contributions,
        "activity_factor": activity_factor(state.qualified_rounds) if state else 0.85,
        "qualified_rounds": state.qualified_rounds if state else 0,
        "data_quality": (
            _state_row(state, None, len(events))["data_quality"]
            if state else ("insufficient_data" if events else "no_data")
        ),
        "unmapped_events": state.unmapped_events if state else sum(
            1 for event in events if event.mapping_status == "unmapped"
        ),
        "penalties": round(sum(value for value in contributions.values() if value < 0), 2),
        "adjustments": [{
            "id": adjustment.id,
            "delta": adjustment.delta,
            "reason": adjustment.reason,
            "created_by": adjustment.created_by,
            "created_at": adjustment.created_at,
        } for adjustment in adjustments],
        "formula_version": FORMULA_VERSION,
        "config_version": next((event.config_version for event in reversed(events) if event.config_version), "default"),
    }


@router.get("/api/v2/admin/ratings/{steam_id}/explanation")
def get_rating_explanation_v2(
    steam_id: str,
    mode: str = Query("mission"),
    role: str = Query("captain"),
    admin: AdminUser = Depends(get_current_admin_v2),
    db: Session = Depends(get_db),
):
    _require_view(admin)
    mode = _validate_mode(mode)
    if role == "all":
        roles = []
        for role_name in ROLES:
            explanation = _rating_explanation_for_role(db, steam_id, mode, role_name)
            if explanation["event_count"] or explanation["qualified_rounds"] or explanation["adjustments"]:
                roles.append(explanation)
        return {"steam_id": steam_id, "mode": mode, "role": "all", "roles": roles}
    return _rating_explanation_for_role(db, steam_id, mode, _validate_role(role))


@router.get("/api/v2/admin/rating-events")
def get_rating_events_v2(
    mode: str = Query("mission"),
    role: Optional[str] = Query(None),
    steam_id: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1, le=500),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    admin: AdminUser = Depends(get_current_admin_v2),
    db: Session = Depends(get_db),
):
    _require_view(admin)
    mode = _validate_mode(mode)
    query = db.query(RatingEventLedger).filter(RatingEventLedger.mode == mode)
    if role and role != "all":
        query = query.filter(RatingEventLedger.role == _validate_role(role))
    if steam_id:
        query = query.filter(RatingEventLedger.steam_id == steam_id)
    effective_page_size = limit or page_size
    total = query.count()
    offset = 0 if limit else (page - 1) * effective_page_size
    events = query.order_by(
        desc(RatingEventLedger.occurred_at), desc(RatingEventLedger.id)
    ).offset(offset).limit(effective_page_size).all()
    return {
        "mode": mode,
        "pagination": {
            "page": 1 if limit else page,
            "page_size": effective_page_size,
            "total": total,
            "total_pages": max(1, math.ceil(total / effective_page_size)),
            "has_next": (offset + len(events)) < total,
        },
        "events": [{
            "id": event.id,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "steam_id": event.steam_id,
            "role": event.role,
            "raw_value": event.raw_value,
            "points_delta": event.points_delta,
            "mapping_status": event.mapping_status,
            "formula_version": event.formula_version,
            "config_version": event.config_version,
            "occurred_at": event.occurred_at,
            "received_at": event.received_at,
            "projected_at": event.projected_at,
            "source": event.source_context,
            "round": event.round_context,
            "metadata": event.event_metadata,
        } for event in events],
    }


@router.get("/api/v2/admin/rating-delivery-log")
def get_rating_delivery_log_v2(
    status: Optional[str] = Query(None),
    steam_id: Optional[str] = Query(None),
    mode: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    admin: AdminUser = Depends(get_current_admin_v2),
    db: Session = Depends(get_db),
):
    _require_view(admin)
    query = db.query(RatingDeliveryLog)
    if status:
        query = query.filter(RatingDeliveryLog.status == status)
    if mode:
        query = query.filter(RatingDeliveryLog.mode == _validate_mode(mode))
    if role and role != "all":
        query = query.filter(RatingDeliveryLog.role == _validate_role(role))
    if steam_id:
        query = query.filter(RatingDeliveryLog.event_id.in_(
            db.query(RatingEventLedger.event_id).filter(RatingEventLedger.steam_id == steam_id)
        ))
    rows = query.order_by(desc(RatingDeliveryLog.received_at), desc(RatingDeliveryLog.id)).limit(limit).all()
    status_counts = dict(query.with_entities(
        RatingDeliveryLog.status, func.count(RatingDeliveryLog.id)
    ).group_by(RatingDeliveryLog.status).all())
    summary = {status: int(status_counts.get(status, 0)) for status in ("accepted", "duplicate", "rejected", "retry", "failed")}
    summary["total"] = int(sum(status_counts.values()))
    return {
        "summary": summary,
        "entries": [{
            "event_id": row.event_id,
            "mode": row.mode,
            "role": row.role,
            "status": row.status,
            "reason": row.reason,
            "received_at": row.received_at,
        } for row in rows]
    }


@router.get("/api/v2/admin/rating-calculation-runs")
def get_rating_calculation_runs_v2(
    mode: str = Query("mission"),
    limit: int = Query(100, ge=1, le=500),
    admin: AdminUser = Depends(get_current_admin_v2),
    db: Session = Depends(get_db),
):
    _require_view(admin)
    mode = _validate_mode(mode)
    rows = db.query(RatingCalculationRun).filter(
        RatingCalculationRun.mode == mode,
    ).order_by(desc(RatingCalculationRun.started_at)).limit(limit).all()
    return {"mode": mode, "runs": [{
        "run_id": row.run_id,
        "formula_version": row.formula_version,
        "config_version": row.config_version,
        "status": row.status,
        "event_count": row.event_count,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "error_message": row.error_message,
    } for row in rows]}


@router.post("/api/v2/admin/rating-adjustments")
async def create_rating_adjustment_v2(
    payload: dict[str, Any],
    request: Request,
    admin: AdminUser = Depends(get_current_admin_v2),
    db: Session = Depends(get_db),
):
    await ensure_csrf_or_valid_admin_session(request, db)
    _require_edit(admin)
    mode = _validate_mode(str(payload.get("mode", "")))
    role = _validate_role(str(payload.get("role", "")))
    steam_id = str(payload.get("steam_id", "")).strip()
    reason = str(payload.get("reason", "")).strip()
    try:
        delta = float(payload.get("delta"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="delta must be numeric") from exc
    if not steam_id or not reason or not math.isfinite(delta):
        raise HTTPException(status_code=422, detail="steam_id, reason and finite delta are required")
    adjustment = RatingAdjustment(
        steam_id=steam_id,
        mode=mode,
        role=role,
        delta=delta,
        reason=reason,
        created_by=admin.id,
    )
    state = db.query(RatingProjectionState).filter(
        RatingProjectionState.steam_id == steam_id,
        RatingProjectionState.mode == mode,
        RatingProjectionState.role == role,
    ).first()
    if state is None:
        state = RatingProjectionState(steam_id=steam_id, mode=mode, role=role)
        db.add(state)
        db.flush()
    state.cumulative_points = max(0.0, state.cumulative_points + delta)
    state.current_rating = rating_from_points(state.cumulative_points, state.qualified_rounds or 0)
    db.add(adjustment)
    db.commit()
    return {
        "id": adjustment.id,
        "steam_id": steam_id,
        "mode": mode,
        "role": role,
        "delta": delta,
        "current_rating": state.current_rating,
    }


def _config_for_mode(mode: str) -> dict:
    mode = _validate_mode(mode)
    # This helper is used by a request handler below; the fallback keeps the
    # shape deterministic for a fresh dev database.
    current = {"WEIGHT_MATRIX": EVENT_WEIGHTS.get(mode, {})}
    return {
        "mode": mode,
        "active_profile": "balanced",
        "profiles": {
            "balanced": {"label": "Сбалансированный", **current},
            "conservative": {"label": "Осторожный", **current},
        },
        "config": current,
        "roles": ROLES,
    }


def _normalize_weight_matrix(config: object) -> dict:
    matrix = config.get("WEIGHT_MATRIX") if isinstance(config, dict) else None
    if not isinstance(matrix, dict) or set(matrix) - set(ROLES):
        raise HTTPException(status_code=422, detail="WEIGHT_MATRIX with known roles is required")
    normalized_matrix = {}
    for role, weights in matrix.items():
        if not isinstance(weights, dict):
            raise HTTPException(status_code=422, detail=f"WEIGHT_MATRIX[{role}] must be an object")
        try:
            normalized = {str(action): float(weight) for action, weight in weights.items()}
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="All weights must be numeric") from exc
        if any(not math.isfinite(weight) for weight in normalized.values()):
            raise HTTPException(status_code=422, detail="All weights must be finite")
        normalized_matrix[role] = normalized
    return {"WEIGHT_MATRIX": normalized_matrix}


@router.get("/api/v2/admin/rating-config/{mode}")
def get_rating_config_v2(
    mode: str,
    admin: AdminUser = Depends(get_current_admin_v2),
    db: Session = Depends(get_db),
):
    _require_view(admin)
    mode = _validate_mode(mode)
    row = db.query(RatingConfigVersion).filter(
        RatingConfigVersion.mode == mode,
    ).order_by(desc(RatingConfigVersion.created_at), desc(RatingConfigVersion.id)).first()
    config = row.config if row else {"WEIGHT_MATRIX": EVENT_WEIGHTS.get(mode, {})}
    base = _config_for_mode(mode)
    base["config"] = config
    base["active_profile"] = "balanced"
    base["config_version"] = row.version if row else "default"
    return base


@router.put("/api/v2/admin/rating-config/{mode}")
async def put_rating_config_v2(
    mode: str,
    payload: dict[str, Any],
    request: Request,
    admin: AdminUser = Depends(get_current_admin_v2),
    db: Session = Depends(get_db),
):
    await ensure_csrf_or_valid_admin_session(request, db)
    _require_edit(admin)
    _validate_mode(mode)
    config = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    saved = _normalize_weight_matrix(config)
    version = f"{mode}-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    db.add(RatingConfigVersion(
        mode=mode,
        version=version,
        config=saved,
        created_by=admin.id,
    ))
    db.commit()
    return {
        "mode": mode,
        "active_profile": payload.get("active_profile", "balanced"),
        "config_version": version,
        "config": saved,
        "roles": ROLES,
    }


@router.post("/api/v2/admin/rating-config/{mode}/rollback/{version}")
async def rollback_rating_config_v2(
    mode: str,
    version: str,
    request: Request,
    admin: AdminUser = Depends(get_current_admin_v2),
    db: Session = Depends(get_db),
):
    """Restore a prior config by writing a new version; no destructive delete."""
    await ensure_csrf_or_valid_admin_session(request, db)
    _require_edit(admin)
    _validate_mode(mode)
    row = db.query(RatingConfigVersion).filter(
        RatingConfigVersion.mode == mode,
        RatingConfigVersion.version == version,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Config version not found")
    saved = row.config
    new_version = f"{mode}-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    db.add(RatingConfigVersion(
        mode=mode,
        version=new_version,
        config=saved,
        created_by=admin.id,
    ))
    db.commit()
    return {"mode": mode, "config_version": new_version, "rolled_back_from": version, "config": saved}


@router.post("/api/v2/admin/rating-config/{mode}/preview")
async def preview_rating_config_v2(
    mode: str,
    payload: dict[str, Any],
    request: Request,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin_v2),
):
    await ensure_csrf_or_valid_admin_session(request, db)
    _require_edit(admin)
    _validate_mode(mode)
    config = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    candidate = _normalize_weight_matrix(config)
    current_row = db.query(RatingConfigVersion).filter(
        RatingConfigVersion.mode == mode,
    ).order_by(desc(RatingConfigVersion.created_at), desc(RatingConfigVersion.id)).first()
    current = current_row.config if current_row else {"WEIGHT_MATRIX": EVENT_WEIGHTS.get(mode, {})}
    buckets: dict[tuple[str, str], dict] = {}
    events = db.query(RatingEventLedger).filter(
        RatingEventLedger.mode == mode,
    ).order_by(RatingEventLedger.id).all()
    for event in events:
        key = (event.steam_id, event.role)
        bucket = buckets.setdefault(key, {"points": 0.0, "counts": {}, "rounds": set()})
        delta, mapping = calculate_event_delta(mode, event.role, event.event_type, event.raw_value, candidate.get("WEIGHT_MATRIX", {}))
        if mapping == "mapped":
            previous = int(bucket["counts"].get(event.event_type, 0))
            bucket["points"] += delta * diminishing_factor(previous)
            bucket["counts"][event.event_type] = previous + 1
            token = (event.round_context or {}).get("round_token")
            if token:
                bucket["rounds"].add(token)
    old_states = {(row.steam_id, row.role): row for row in db.query(RatingProjectionState).filter(RatingProjectionState.mode == mode).all()}
    changes = []
    for key, bucket in buckets.items():
        before = old_states.get(key)
        before_rating = before.current_rating if before else 0.0
        after_rating = rating_from_points(bucket["points"], len(bucket["rounds"]))
        if abs(after_rating - before_rating) > 0.01:
            changes.append({"steam_id": key[0], "role": key[1], "before": round(before_rating, 2), "after": after_rating, "delta": round(after_rating - before_rating, 2)})
    changes.sort(key=lambda item: abs(item["delta"]), reverse=True)
    return {
        "mode": mode,
        "preview": True,
        "changed_fields": ["WEIGHT_MATRIX"],
        "affected_players": len(changes),
        "top_changes": changes[:100],
        "current_config_version": current_row.version if current_row else "default",
        "message": "Предпросмотр пересчитал ledger; сохранение не выполнялось.",
    }
