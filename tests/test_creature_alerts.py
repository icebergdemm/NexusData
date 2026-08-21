from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import AICreatureAlert, RoundEvent, WorldRound
from routes.dispatch import _queue_ai_large_creature_alert
from routes.world_state import _claim_large_creature_alerts
from services_creature_alerts import LargeCreatureSpawn, detect_large_creature_spawns


def _submarines():
    return [
        {"id": 10, "name": "Акула - [ASPER] 1"},
        {"id": 11, "name": "Старательский участок Yasa"},
    ]


def _positions():
    return [
        {"id": 10, "x": 0, "y": 0},
        {"id": 11, "x": 10_000, "y": 10_000},
    ]


def test_detects_large_non_abyss_creatures_and_measures_from_yasa():
    previous = [
        {
            "id": 1,
            "species": "Mudraptor",
            "human": False,
            "dead": False,
            "x": 1,
            "y": 1,
        }
    ]
    current = [
        *previous,
        {
            "id": 2,
            "species": "Moloch",
            "human": False,
            "dead": False,
            "x": 40_000,
            "y": 50_000,
        },
        {
            "id": 3,
            "species": "Endworm",
            "human": False,
            "dead": False,
            "x": 20_000,
            "y": 20_000,
        },
        {
            "id": 4,
            "species": "Molochbaby",
            "human": False,
            "dead": False,
            "x": 20_000,
            "y": 20_000,
        },
        {
            "id": 5,
            "species": "Moloch",
            "human": False,
            "dead": True,
            "x": 20_000,
            "y": 20_000,
        },
    ]

    result = detect_large_creature_spawns(
        current_characters=current,
        submarines=_submarines(),
        current_submarines=_positions(),
        large_species=["Moloch", "Endworm"],
        abyss_species=["Endworm", "Charybdis", "Latcher"],
    )

    assert result == [
        LargeCreatureSpawn(
            character_id="2",
            species="Moloch",
            distance_from_yasa_m_approx=500,
        )
    ]


def test_first_snapshot_is_detected_once_yasa_is_available():
    current = [
        {
            "id": 2,
            "species": "Moloch",
            "human": False,
            "dead": False,
            "x": 40_000,
            "y": 50_000,
        }
    ]
    arguments = {
        "current_characters": current,
        "current_submarines": _positions(),
        "large_species": ["Moloch"],
        "abyss_species": [],
    }

    assert detect_large_creature_spawns(
        submarines=_submarines(),
        **arguments,
    ) == [LargeCreatureSpawn("2", "Moloch", 500)]


def test_missing_yasa_defers_detection_until_static_map_arrives():
    current = [
        {
            "id": 2,
            "species": "Moloch",
            "human": False,
            "dead": False,
            "x": 40_000,
            "y": 50_000,
        }
    ]
    arguments = {
        "current_characters": current,
        "current_submarines": _positions(),
        "large_species": ["Moloch"],
        "abyss_species": [],
    }

    assert detect_large_creature_spawns(
        submarines=[{"id": 10, "name": "Акула"}],
        **arguments,
    ) == []
    assert detect_large_creature_spawns(
        submarines=_submarines(),
        **arguments,
    ) == [LargeCreatureSpawn("2", "Moloch", 500)]


def test_configured_species_matches_variant_suffix_but_not_unrelated_prefab():
    current = [
        {
            "id": 2,
            "species": "Molochblack_m",
            "human": False,
            "dead": False,
            "x": 40_000,
            "y": 50_000,
        },
        {
            "id": 3,
            "species": "Molochbaby",
            "human": False,
            "dead": False,
            "x": 40_000,
            "y": 50_000,
        },
        {
            "id": 4,
            "species": "Endworm_m",
            "human": False,
            "dead": False,
            "x": 40_000,
            "y": 50_000,
        },
    ]

    assert detect_large_creature_spawns(
        current_characters=current,
        submarines=_submarines(),
        current_submarines=_positions(),
        large_species=["Molochblack", "Endworm"],
        abyss_species=["Endworm"],
    ) == [LargeCreatureSpawn("2", "Molochblack_m", 500)]


def test_claim_is_durable_and_deduplicated_per_round_and_character():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[WorldRound.__table__, AICreatureAlert.__table__],
    )
    db = Session(engine)
    try:
        row = WorldRound(round_id="world-round", server_name="ASPER1")
        db.add(row)
        db.commit()
        spawn = LargeCreatureSpawn("42", "Moloch", 700)

        first = _claim_large_creature_alerts(db, row, [spawn])
        second = _claim_large_creature_alerts(db, row, [spawn])
        db.commit()

        assert len(first) == 1
        assert second == []
        assert db.query(AICreatureAlert).count() == 1
    finally:
        db.close()
        engine.dispose()


def test_ai_payload_contains_only_scope_and_approximate_meters():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[RoundEvent.__table__, WorldRound.__table__, AICreatureAlert.__table__],
    )
    db = Session(engine)
    try:
        row = WorldRound(round_id="world-round", server_name="ASPER1")
        alert = AICreatureAlert(
            id=17,
            round_id="world-round",
            character_id="42",
            species="Moloch",
            distance_from_yasa_m_approx=725,
        )
        db.add_all([row, alert])
        db.commit()

        with (
            patch("routes.dispatch.settings.ai_dispatcher_url", "http://dispatcher/api"),
            patch("routes.dispatch.settings.ai_dispatcher_api_key", "secret"),
            patch("routes.dispatch._enqueue_ai_dispatch_job") as enqueue,
        ):
            _queue_ai_large_creature_alert(row, [alert], db)

        job = enqueue.call_args.args[0]
        assert job.payload["message_type"] == "threat_alert"
        assert job.payload["threat_context"] == {
            "contact_scope": "single",
            "distance_from_yasa_m_approx": 725,
        }
        serialized = repr(job.payload).casefold()
        assert "moloch" not in serialized
        assert "species" not in serialized
        assert "direction" not in serialized
        assert "azimuth" not in serialized
        assert "clock" not in serialized
        assert "x" not in job.payload["threat_context"]
        assert "y" not in job.payload["threat_context"]
    finally:
        db.close()
        engine.dispose()
