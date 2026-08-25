from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from database import Base
from models import AICreatureAlert, AppSetting, RoundEvent, WorldRound
from routes.dispatch import _queue_ai_large_creature_alert
from routes.world_state import _claim_large_creature_alerts, world_snapshot
from services_creature_alerts import (
    LargeCreatureSpawn,
    detect_large_creature_spawns,
    is_configured_large_species,
)


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


def _request(payload: dict) -> Request:
    body = json.dumps(payload).encode()
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/world/snapshot",
            "raw_path": b"/api/v1/world/snapshot",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
        },
        receive,
    )


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
            character_id="1",
            species="Mudraptor",
            distance_from_yasa_m_approx=140,
            contact_class="unclassified",
            yasa_perimeter=True,
        ),
        LargeCreatureSpawn(
            character_id="2",
            species="Moloch",
            distance_from_yasa_m_approx=500,
        ),
        LargeCreatureSpawn(
            character_id="4",
            species="Molochbaby",
            distance_from_yasa_m_approx=140,
            contact_class="unclassified",
            yasa_perimeter=True,
        ),
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


def test_expected_large_contact_families_all_trigger_but_abyss_does_not():
    species = [
        "Hammerhead",
        "Hammerheadmatriarch_f",
        "Crawlerbroodmother",
        "Moloch_m",
        "Molochblack",
        "Endworm",
    ]
    characters = [
        {
            "id": index,
            "species": prefab,
            "human": False,
            "dead": False,
            "x": 20_000 + index * 100,
            "y": 20_000,
        }
        for index, prefab in enumerate(species, start=1)
    ]

    detected = detect_large_creature_spawns(
        current_characters=characters,
        submarines=_submarines(),
        current_submarines=_positions(),
        large_species=[
            "Hammerhead",
            "Hammerheadmatriarch",
            "Crawlerbroodmother",
            "Moloch",
            "Molochblack",
            "Endworm",
        ],
        abyss_species=["Endworm"],
    )

    assert [item.species for item in detected] == species[:-1]


def test_watcher_and_giant_spineling_are_always_large_contacts():
    species = [
        "Watcher",
        "Watcher_f",
        "Spineling_giant",
        "GiantSpineling",
        "SpinelingGiant_m",
        "Spineling",
    ]
    characters = [
        {
            "id": index,
            "species": prefab,
            "human": False,
            "dead": False,
            "x": 70_000,
            "y": 10_000,
        }
        for index, prefab in enumerate(species, start=1)
    ]

    detected = detect_large_creature_spawns(
        current_characters=characters,
        submarines=_submarines(),
        current_submarines=_positions(),
        # Simulate a production env value which replaced the defaults.
        large_species=[],
        abyss_species=[],
    )

    assert [item.species for item in detected] == species[:-1]
    assert all(item.contact_class == "large" for item in detected)
    assert is_configured_large_species("Watcher", [])
    assert is_configured_large_species("Spineling_giant", [])
    assert not is_configured_large_species("Spineling", [])


def test_any_non_abyss_creature_inside_yasa_perimeter_is_alertable():
    detected = detect_large_creature_spawns(
        current_characters=[
            {"id": 1, "species": "Crawler", "human": False, "dead": False, "x": 34_900, "y": 10_000},
            {"id": 2, "species": "Mudraptor", "human": False, "dead": False, "x": 35_100, "y": 10_000},
            {"id": 3, "species": "Endworm", "human": False, "dead": False, "x": 10_100, "y": 10_000},
        ],
        submarines=_submarines(),
        current_submarines=_positions(),
        large_species=["Moloch"],
        abyss_species=["Endworm"],
    )

    assert detected == [
        LargeCreatureSpawn(
            "1",
            "Crawler",
            250,
            contact_class="unclassified",
            yasa_perimeter=True,
        )
    ]


def test_yasa_position_can_come_from_dynamic_cyrillic_name():
    detected = detect_large_creature_spawns(
        current_characters=[
            {"id": 1, "species": "Crawler", "human": 0, "dead": 0, "x": 100, "y": 0},
        ],
        submarines=[],
        current_submarines=[{"id": 99, "name": "Старательский участок Яса", "x": 0, "y": 0}],
        large_species=[],
        abyss_species=[],
    )

    assert detected == [
        LargeCreatureSpawn(
            "1",
            "Crawler",
            0,
            contact_class="unclassified",
            yasa_perimeter=True,
        )
    ]


def test_world_snapshot_creates_and_queues_yasa_perimeter_event():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[WorldRound.__table__, AICreatureAlert.__table__, AppSetting.__table__],
    )
    db = Session(engine)
    try:
        row = WorldRound(
            round_id="world-round",
            server_name="ASPER1",
            submarines=_submarines(),
        )
        db.add(row)
        db.commit()
        request = _request(
            {
                "round_id": "world-round",
                "server_name": "ASPER1",
                "seq": 1,
                "subs": _positions(),
                "characters": [
                    {
                        "id": 7,
                        "species": "Crawler",
                        "human": False,
                        "dead": False,
                        "x": 25_000,
                        "y": 10_000,
                    }
                ],
            }
        )

        with (
            patch("routes.world_state.settings.ai_dispatcher_url", "http://dispatcher/api"),
            patch("routes.world_state.settings.ai_dispatcher_api_key", "secret"),
            patch("routes.world_state._try_queue_ai_large_creature_alert") as queue_alert,
            patch("routes.world_state._try_queue_ai_world_briefing"),
        ):
            response = asyncio.run(world_snapshot(request, db))

        assert response.status_code == 200
        alert = db.query(AICreatureAlert).one()
        assert alert.character_id == "yasa:7"
        assert alert.distance_from_yasa_m_approx == 150
        assert alert.yasa_perimeter is True
        assert queue_alert.call_args.args[2][0].id == alert.id
    finally:
        db.close()
        engine.dispose()


def test_world_snapshot_does_not_track_contacts_while_ai_is_disabled():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[WorldRound.__table__, AICreatureAlert.__table__, AppSetting.__table__],
    )
    db = Session(engine)
    try:
        row = WorldRound(
            round_id="world-disabled",
            server_name="ASPER1",
            submarines=_submarines(),
        )
        db.add_all(
            [
                row,
                AppSetting(key="dispatch_ai_enabled", value="false"),
            ]
        )
        db.commit()
        request = _request(
            {
                "round_id": "world-disabled",
                "server_name": "ASPER1",
                "seq": 1,
                "subs": _positions(),
                "characters": [
                    {
                        "id": 7,
                        "species": "Moloch",
                        "human": False,
                        "dead": False,
                        "x": 25_000,
                        "y": 10_000,
                    }
                ],
            }
        )

        with (
            patch("routes.world_state.settings.ai_dispatcher_url", "http://dispatcher/api"),
            patch("routes.world_state.settings.ai_dispatcher_api_key", "secret"),
            patch("routes.world_state._try_queue_ai_large_creature_alert") as queue_alert,
            patch("routes.world_state._try_queue_ai_world_briefing"),
        ):
            response = asyncio.run(world_snapshot(request, db))

        assert response.status_code == 200
        assert db.query(AICreatureAlert).count() == 0
        assert queue_alert.call_args.args[2] == []
    finally:
        db.close()
        engine.dispose()


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


def test_new_character_id_extends_recent_same_species_track_without_alert():
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

        first = _claim_large_creature_alerts(
            db,
            row,
            [LargeCreatureSpawn("42", "Moloch", 700)],
        )
        second = _claim_large_creature_alerts(
            db,
            row,
            [LargeCreatureSpawn("43", "Moloch", 700)],
        )
        repeat = _claim_large_creature_alerts(
            db,
            row,
            [LargeCreatureSpawn("43", "Moloch", 700)],
        )
        db.commit()

        assert len(first) == 1
        assert second == []
        assert repeat == []
        assert db.query(AICreatureAlert).count() == 2
        tracked = (
            db.query(AICreatureAlert)
            .filter(AICreatureAlert.character_id == "43")
            .one()
        )
        assert tracked.delivered_at is not None
    finally:
        db.close()
        engine.dispose()


def test_tracked_contact_does_not_hide_different_species_or_distant_group():
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

        assert len(
            _claim_large_creature_alerts(
                db,
                row,
                [LargeCreatureSpawn("42", "Moloch", 700)],
            )
        ) == 1
        different_species = _claim_large_creature_alerts(
            db,
            row,
            [LargeCreatureSpawn("43", "Hammerhead", 700)],
        )
        distant_group = _claim_large_creature_alerts(
            db,
            row,
            [LargeCreatureSpawn("44", "Moloch", 1_100)],
        )

        assert [alert.character_id for alert in different_species] == ["43"]
        assert [alert.character_id for alert in distant_group] == ["44"]
    finally:
        db.close()
        engine.dispose()


def test_same_species_contact_can_alert_again_after_tracking_window():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[WorldRound.__table__, AICreatureAlert.__table__],
    )
    db = Session(engine)
    try:
        row = WorldRound(round_id="world-round", server_name="ASPER1")
        old = AICreatureAlert(
            round_id="world-round",
            character_id="42",
            species="Moloch",
            distance_from_yasa_m_approx=700,
            delivered_at=datetime.utcnow() - timedelta(minutes=11),
            last_seen_at=datetime.utcnow() - timedelta(minutes=11),
        )
        db.add_all([row, old])
        db.commit()

        claimed = _claim_large_creature_alerts(
            db,
            row,
            [LargeCreatureSpawn("43", "Moloch", 700)],
        )

        assert [alert.character_id for alert in claimed] == ["43"]
    finally:
        db.close()
        engine.dispose()


def test_large_contact_is_not_reported_again_when_it_crosses_yasa_perimeter():
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

        sector_alert = LargeCreatureSpawn("42", "Moloch", 500)
        perimeter_alert = LargeCreatureSpawn(
            "42",
            "Moloch",
            180,
            contact_class="large",
            yasa_perimeter=True,
        )

        assert len(_claim_large_creature_alerts(db, row, [sector_alert])) == 1
        assert _claim_large_creature_alerts(db, row, [perimeter_alert]) == []
        db.commit()

        assert {
            alert.character_id for alert in db.query(AICreatureAlert).all()
        } == {"42"}
    finally:
        db.close()
        engine.dispose()


def test_undelivered_alert_is_released_after_lease_but_delivered_alert_is_not():
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
        spawn = LargeCreatureSpawn("42", "Hammerhead", 450)

        first = _claim_large_creature_alerts(db, row, [spawn])
        assert len(first) == 1
        alert = first[0]

        alert.queued_at = datetime.utcnow() - timedelta(minutes=6)
        db.commit()
        retried = _claim_large_creature_alerts(db, row, [spawn])
        assert [item.id for item in retried] == [alert.id]

        alert.delivered_at = datetime.utcnow()
        alert.queued_at = None
        db.commit()
        assert _claim_large_creature_alerts(db, row, [spawn]) == []
    finally:
        db.close()
        engine.dispose()


def test_distant_ai_payload_hides_contact_names():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            RoundEvent.__table__,
            WorldRound.__table__,
            AICreatureAlert.__table__,
            AppSetting.__table__,
        ],
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
        assert job.creature_alert_ids == (17,)
        assert job.at_most_once_delivery is True
        assert job.payload["message_type"] == "threat_alert"
        assert job.payload["threat_context"] == {
            "contact_scope": "single",
            "contact_class": "large",
            "yasa_perimeter": False,
            "distance_from_yasa_m_approx": 725,
            "contact_names": [],
            "contact_ids": ["42"],
        }
        serialized = repr(job.payload).casefold()
        assert "moloch" not in serialized
        assert "contact_names" in serialized
        assert "direction" not in serialized
        assert "azimuth" not in serialized
        assert "clock" not in serialized
        assert "x" not in job.payload["threat_context"]
        assert "y" not in job.payload["threat_context"]
    finally:
        db.close()
        engine.dispose()


def test_yasa_perimeter_contacts_share_one_alert_even_when_sizes_differ():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            RoundEvent.__table__,
            WorldRound.__table__,
            AICreatureAlert.__table__,
            AppSetting.__table__,
        ],
    )
    db = Session(engine)
    try:
        row = WorldRound(round_id="world-round", server_name="ASPER1")
        alerts = [
            AICreatureAlert(
                id=21,
                round_id="world-round",
                character_id="42",
                species="Moloch",
                distance_from_yasa_m_approx=700,
            ),
            AICreatureAlert(
                id=22,
                round_id="world-round",
                character_id="yasa:43",
                species="Crawler",
                distance_from_yasa_m_approx=150,
                yasa_perimeter=True,
            ),
            AICreatureAlert(
                id=23,
                round_id="world-round",
                character_id="44",
                species="Hammerhead",
                distance_from_yasa_m_approx=225,
                yasa_perimeter=True,
            ),
        ]
        db.add_all([row, *alerts])
        db.commit()

        with (
            patch("routes.dispatch.settings.ai_dispatcher_url", "http://dispatcher/api"),
            patch("routes.dispatch.settings.ai_dispatcher_api_key", "secret"),
            patch("routes.dispatch._enqueue_ai_dispatch_job", return_value=True) as enqueue,
        ):
            assert _queue_ai_large_creature_alert(row, alerts, db)

        contexts = [call.args[0].payload["threat_context"] for call in enqueue.call_args_list]
        assert contexts == [
            {
                "contact_scope": "single",
                "contact_class": "large",
                "yasa_perimeter": False,
                "distance_from_yasa_m_approx": 700,
                "contact_names": [],
                "contact_ids": ["42"],
            },
            {
                "contact_scope": "group",
                "contact_class": "large",
                "yasa_perimeter": True,
                "distance_from_yasa_m_approx": 150,
                "contact_names": ["Crawler", "Hammerhead"],
                "contact_ids": ["43", "44"],
            },
        ]
        assert enqueue.call_count == 2
        assert enqueue.call_args_list[1].args[0].creature_alert_ids == (22, 23)
    finally:
        db.close()
        engine.dispose()


def test_regular_and_giant_spinelings_near_yasa_queue_one_warning():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            RoundEvent.__table__,
            WorldRound.__table__,
            AICreatureAlert.__table__,
            AppSetting.__table__,
        ],
    )
    db = Session(engine)
    try:
        row = WorldRound(round_id="world-round", server_name="ASPER1")
        alerts = [
            AICreatureAlert(
                id=31,
                round_id="world-round",
                character_id="yasa:spineling-1",
                species="Spineling",
                distance_from_yasa_m_approx=110,
                yasa_perimeter=True,
            ),
            AICreatureAlert(
                id=32,
                round_id="world-round",
                character_id="giant-spineling-1",
                species="Spineling_giant",
                distance_from_yasa_m_approx=130,
                yasa_perimeter=True,
            ),
        ]
        db.add_all([row, *alerts])
        db.commit()

        with (
            patch("routes.dispatch.settings.ai_dispatcher_url", "http://dispatcher/api"),
            patch("routes.dispatch.settings.ai_dispatcher_api_key", "secret"),
            patch("routes.dispatch._enqueue_ai_dispatch_job", return_value=True) as enqueue,
        ):
            assert _queue_ai_large_creature_alert(row, alerts, db)

        assert enqueue.call_count == 1
        job = enqueue.call_args.args[0]
        assert job.creature_alert_ids == (31, 32)
        assert job.payload["threat_context"] == {
            "contact_scope": "group",
            "contact_class": "large",
            "yasa_perimeter": True,
            "distance_from_yasa_m_approx": 110,
            "contact_names": ["Spineling", "Spineling_giant"],
            "contact_ids": ["giant-spineling-1", "spineling-1"],
        }
    finally:
        db.close()
        engine.dispose()
