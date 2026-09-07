"""Snapshot and import the legacy rating data into the v2 Mission ledger.

No old score is copied as a new score. Old ratings/history are archived as
JSON; role-action aggregates become explicitly tagged ``legacy.*`` Mission
events and are recalculated by the v2 engine. PvP is never inferred.

Examples:
  python scripts/migrate_rating_legacy.py --snapshot legacy-rating.json
  python scripts/migrate_rating_legacy.py --snapshot legacy-rating.json --import
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from database import GameSessionLocal, SessionLocal
from models import (
    Base,
    PlayerData,
    PlayerRoleAction,
    PlayerRoleStat,
    PlayerSession,
    PlayerRating,
    RatingCalculationRun,
    RatingConfigVersion,
    RatingProjectionState,
    RatingRankHistory,
    RatingEventLedger,
    RatingHistory,
)
from rating_constants import ROLES
from rating_v2_engine import FORMULA_VERSION, calculate_event_delta
from rating_legacy_migration import normalize_steam_id


def _row(row) -> dict:
    return {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in vars(row).items()
        if not key.startswith("_")
    }


def snapshot(db) -> dict:
    return {
        "snapshot_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "legacy NexusData rating tables",
        "players": [_row(row) for row in db.query(PlayerData).all()],
        "role_actions": [_row(row) for row in db.query(PlayerRoleAction).all()],
        "role_stats": [_row(row) for row in db.query(PlayerRoleStat).all()],
        "sessions": [_row(row) for row in db.query(PlayerSession).all()],
        "ratings": [_row(row) for row in db.query(PlayerRating).all()],
        "history": [_row(row) for row in db.query(RatingHistory).all()],
    }


def import_legacy_actions(source_db, target_db) -> tuple[int, int]:
    players = {row.player_id: normalize_steam_id(row.player_steamid) for row in source_db.query(PlayerData).all()}
    inserted = 0
    skipped = 0
    accepted_ids = []
    for action in source_db.query(PlayerRoleAction).order_by(PlayerRoleAction.id).all():
        steam_id = players.get(action.player_id, "")
        if not steam_id or action.role not in ROLES:
            skipped += 1
            continue
        event_id = f"legacy:role-action:{action.id}"
        if target_db.query(RatingEventLedger.id).filter(RatingEventLedger.event_id == event_id).first():
            continue
        raw_value = float(action.value or 0.0)
        event_type = f"legacy.{action.action_type}"
        delta, mapping_status = calculate_event_delta("mission", action.role, action.action_type, raw_value)
        row = RatingEventLedger(
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
            event_metadata={"source": "legacy.role_action", "legacy_action_id": action.id},
            formula_version=FORMULA_VERSION,
            config_version="legacy-import-v1",
        )
        target_db.add(row)
        target_db.flush()
        accepted_ids.append(row.id)
        inserted += 1
    target_db.commit()

    # Projection is deliberately explicit and can be invoked again safely;
    # the route worker filters projected_at, so no score is doubled.
    if accepted_ids:
        from routes.rating_events import _project_rating_events

        _project_rating_events(accepted_ids)
    return inserted, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--import", dest="do_import", action="store_true")
    args = parser.parse_args()

    source_db = SessionLocal()
    target_db = GameSessionLocal()
    try:
        Base.metadata.create_all(
            bind=target_db.get_bind(),
            tables=[
                RatingEventLedger.__table__,
                RatingProjectionState.__table__,
                RatingRankHistory.__table__,
                RatingCalculationRun.__table__,
                RatingConfigVersion.__table__,
            ],
        )
        data = snapshot(source_db)
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"snapshot: {args.snapshot} ({len(data['role_actions'])} role actions)")
        if args.do_import:
            inserted, skipped = import_legacy_actions(source_db, target_db)
            print(f"imported Mission events: {inserted}; skipped: {skipped}; PvP: 0 (unknown by design)")
    finally:
        source_db.close()
        target_db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
