from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import models
import rating_legacy_migration


def _sessions():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(
        engine,
        tables=[
            models.PlayerData.__table__,
            models.PlayerRoleAction.__table__,
            models.RatingEventLedger.__table__,
            models.RatingProjectionState.__table__,
            models.RatingCalculationRun.__table__,
        ],
    )
    source = Session(engine)
    target = Session(engine)
    return engine, source, target


def test_legacy_import_runs_once_and_never_duplicates_events():
    engine, source, target = _sessions()
    try:
        source.add(models.PlayerData(
            player_id=7,
            player_steamid="STEAM_0:1:123",
            player_last_time_name="Legacy Player",
        ))
        source.add(models.PlayerRoleAction(
            id=42,
            player_id=7,
            role="mechanic",
            action_type="repair_hull",
            value=4,
        ))
        source.commit()

        projected = []
        first = rating_legacy_migration.import_legacy_rating_once(
            source,
            target,
            project_events=lambda ids: projected.extend(ids) or True,
        )
        second = rating_legacy_migration.import_legacy_rating_once(
            source,
            target,
            project_events=lambda ids: projected.extend(ids) or True,
        )

        event = target.query(models.RatingEventLedger).one()
        marker = target.query(models.RatingCalculationRun).filter_by(
            run_id=rating_legacy_migration.LEGACY_IMPORT_RUN_ID,
        ).one()
        assert first["inserted"] == 1
        assert second["status"] == "already-complete"
        assert event.event_id == "legacy:role-action:42"
        assert event.mode == "mission"
        assert event.steam_id == "76561197960265975"
        assert event.event_type == "legacy.repair_hull"
        assert marker.status == "completed"
        assert len(projected) == 1

        source.add(models.PlayerRoleAction(
            id=43,
            player_id=7,
            role="mechanic",
            action_type="repair_device",
            value=8,
        ))
        source.commit()
        locked = rating_legacy_migration.import_legacy_rating_once(
            source,
            target,
            project_events=lambda ids: projected.extend(ids) or True,
        )
        assert locked["status"] == "already-complete"
        assert target.query(models.RatingEventLedger).count() == 1
    finally:
        source.close()
        target.close()
        engine.dispose()
