"""One-time, idempotent import of legacy rating actions into the v2 ledger.

The old tables remain the audit source. Their aggregated role actions become
explicitly tagged Mission events; no historical PvP data is invented.
"""

from __future__ import annotations

from datetime import datetime, timezone

from models import (
    PlayerData,
    PlayerRoleAction,
    RatingCalculationRun,
    RatingEventLedger,
)
from rating_constants import ROLES
from rating_v2_engine import FORMULA_VERSION, calculate_event_delta


LEGACY_IMPORT_RUN_ID = "legacy-rating-import-v1"


def normalize_steam_id(value: str | None) -> str:
    value = (value or "").strip()
    if value.upper().startswith("STEAM_"):
        parts = value[6:].split(":")
        parts = parts[1:] if len(parts) == 3 else parts
    elif value.upper().startswith("STEAM:"):
        parts = value.split(":")[1:]
    else:
        return value
    if len(parts) != 2:
        return value
    try:
        return str(76561197960265728 + int(parts[1]) * 2 + int(parts[0]))
    except ValueError:
        return value


def import_legacy_rating_once(source_db, target_db, project_events=None) -> dict:
    """Import old role actions exactly once and project all pending rows.

    ``source_db`` and ``target_db`` are separate sessions because production
    uses separate SQLAlchemy pools. The operation is safe to retry after a
    process or projection failure: event IDs and the run marker are durable.
    """
    marker = target_db.query(RatingCalculationRun).filter(
        RatingCalculationRun.run_id == LEGACY_IMPORT_RUN_ID,
    ).first()
    if marker and marker.status == "completed":
        return {"status": "already-complete", "inserted": 0, "skipped": 0}

    if marker is None:
        marker = RatingCalculationRun(
            run_id=LEGACY_IMPORT_RUN_ID,
            mode="mission",
            formula_version=FORMULA_VERSION,
            config_version="legacy-import-v1",
            status="running",
            started_at=datetime.utcnow(),
        )
        target_db.add(marker)
        target_db.flush()

    players = {
        row.player_id: normalize_steam_id(row.player_steamid)
        for row in source_db.query(PlayerData).all()
    }
    inserted = 0
    skipped = 0
    for action in source_db.query(PlayerRoleAction).order_by(PlayerRoleAction.id).all():
        steam_id = players.get(action.player_id, "")
        if not steam_id or action.role not in ROLES:
            skipped += 1
            continue
        event_id = f"legacy:role-action:{action.id}"
        if target_db.query(RatingEventLedger.id).filter(
            RatingEventLedger.event_id == event_id,
        ).first():
            continue

        raw_value = float(action.value or 0.0)
        event_type = f"legacy.{action.action_type}"
        delta, mapping_status = calculate_event_delta(
            "mission", action.role, action.action_type, raw_value,
        )
        target_db.add(RatingEventLedger(
            event_id=event_id,
            version=1,
            steam_id=steam_id,
            mode="mission",
            role=action.role,
            event_type=event_type,
            raw_value=raw_value,
            points_delta=delta,
            mapping_status=mapping_status,
            occurred_at=datetime.now(timezone.utc).replace(tzinfo=None),
            source_context={
                "server_name": "legacy",
                "server_session_id": "legacy-import",
                "plugin_version": "legacy",
                "formula_version": FORMULA_VERSION,
            },
            round_context={"round_token": f"legacy:player:{action.player_id}"},
            event_metadata={
                "source": "legacy.role_action",
                "legacy_action_id": action.id,
            },
            formula_version=FORMULA_VERSION,
            config_version="legacy-import-v1",
        ))
        inserted += 1

    marker.event_count = target_db.query(RatingEventLedger.id).filter(
        RatingEventLedger.event_id.like("legacy:role-action:%"),
    ).count()
    target_db.commit()

    pending_ids = [row.id for row in target_db.query(RatingEventLedger.id).filter(
        RatingEventLedger.event_id.like("legacy:role-action:%"),
        RatingEventLedger.projected_at.is_(None),
    ).order_by(RatingEventLedger.id).all()]
    if pending_ids:
        if project_events is None:
            from routes.rating_events import _project_rating_events

            project_events = _project_rating_events
        if project_events(pending_ids) is False:
            return {
                "status": "projection-pending",
                "inserted": inserted,
                "skipped": skipped,
                "pending": len(pending_ids),
            }

    marker = target_db.query(RatingCalculationRun).filter(
        RatingCalculationRun.run_id == LEGACY_IMPORT_RUN_ID,
    ).first()
    marker.status = "completed"
    marker.completed_at = datetime.utcnow()
    target_db.commit()
    return {"status": "completed", "inserted": inserted, "skipped": skipped}
