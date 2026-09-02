from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import (
    AICreatureAlert,
    AIDispatcherReminder,
    AppSetting,
    DispatchMessage,
    RoundEvent,
    WorldRound,
)
from routes.dispatch import (
    _build_ai_communication_context,
    _extract_ai_dispatcher_transmissions,
    _finish_ai_creature_alert_delivery,
    _persist_ai_dispatcher_reminders,
    _queue_due_ai_dispatcher_reminders,
    client_round_conversation_context,
)
from routes.world_state import _serialize_ai_tracked_contacts


def _engine_for(*tables):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=list(tables))
    return engine


def test_communication_context_keeps_recent_chat_and_players_actual_vessel() -> None:
    engine = _engine_for(WorldRound.__table__, DispatchMessage.__table__)
    db = Session(engine)
    try:
        world_round = WorldRound(
            round_id="round-1",
            server_name="ASPER1",
            sub_name="Акула",
            submarines=[
                {"id": 1, "name": "Акула"},
                {"id": 2, "name": "Касатка"},
            ],
            last_characters=[
                {"id": 7, "human": True, "name": "Иван", "sub_id": 2},
            ],
            last_update_at=datetime.utcnow(),
        )
        db.add(world_round)
        db.add_all(
            [
                DispatchMessage(
                    player_name="Старый экипаж",
                    sender_type="player",
                    server_name="ASPER1",
                    text="Сообщение прошлого выхода.",
                    created_at=datetime.utcnow() - timedelta(minutes=5),
                ),
                DispatchMessage(
                    player_name="Иван",
                    sender_type="player",
                    server_name="ASPER1",
                    text="Диспетчер, приём.",
                ),
                DispatchMessage(
                    player_name="Console",
                    sender_type="player",
                    server_name="ASPER1",
                    text="[Администрация]: внутренний msg_box_to.",
                ),
                DispatchMessage(
                    player_name="ИИ-диспетчер",
                    sender_type="dispatcher",
                    server_name="ASPER1",
                    text="[Диспетчер] -> «Иван, борт «Касатка»» Слушаю.",
                ),
            ]
        )
        db.commit()

        context = _build_ai_communication_context(db, "ASPER1", sender_name="Иван")

        assert context["source"] == {
            "kind": "player",
            "name": "Иван",
            "vessel_name": "Касатка",
        }
        assert context["default_target"] == "Иван, борт «Касатка»"
        assert [item["text"] for item in context["recent_messages"]] == [
            "Диспетчер, приём.",
            "[Диспетчер] -> «Иван, борт «Касатка»» Слушаю.",
        ]
    finally:
        db.close()
        engine.dispose()


def test_communication_context_uses_configured_message_limit() -> None:
    engine = _engine_for(
        WorldRound.__table__,
        DispatchMessage.__table__,
        AppSetting.__table__,
    )
    db = Session(engine)
    try:
        db.add(
            WorldRound(
                round_id="round-history",
                server_name="ASPER1",
                created_at=datetime.utcnow() - timedelta(minutes=1),
                last_update_at=datetime.utcnow(),
            )
        )
        db.add(
            AppSetting(
                key="dispatch_ai_runtime_config_v1",
                value='{"history_messages":9,"nearby_context_messages":3}',
            )
        )
        db.add_all(
            [
                DispatchMessage(
                    player_name="Экипаж",
                    sender_type="player",
                    server_name="ASPER1",
                    text=f"Сообщение {index}",
                    created_at=datetime.utcnow() + timedelta(seconds=index),
                )
                for index in range(6)
            ]
        )
        db.commit()

        context = _build_ai_communication_context(db, "ASPER1")

        assert [item["text"] for item in context["recent_messages"]] == [
            "Сообщение 3",
            "Сообщение 4",
            "Сообщение 5",
        ]
    finally:
        db.close()
        engine.dispose()


def test_round_conversation_tool_searches_the_entire_active_round() -> None:
    engine = _engine_for(
        WorldRound.__table__,
        RoundEvent.__table__,
        DispatchMessage.__table__,
    )
    db = Session(engine)
    try:
        started_at = datetime.utcnow() - timedelta(minutes=2)
        db.add(
            WorldRound(
                round_id="round-search",
                server_name="ASPER1",
                created_at=started_at,
                last_update_at=datetime.utcnow(),
            )
        )
        db.add(
            RoundEvent(
                event_type="start",
                server_name="ASPER1",
                created_at=started_at,
            )
        )
        db.add_all(
            [
                DispatchMessage(
                    player_name="Старый экипаж",
                    sender_type="player",
                    server_name="ASPER1",
                    text="Кислород до начала раунда.",
                    created_at=started_at - timedelta(minutes=1),
                ),
                DispatchMessage(
                    player_name="Механик",
                    sender_type="player",
                    server_name="ASPER1",
                    text="Проверил кислородный генератор.",
                    created_at=started_at + timedelta(seconds=5),
                ),
                DispatchMessage(
                    player_name="Console",
                    sender_type="player",
                    server_name="ASPER1",
                    text="кислород internal echo",
                    created_at=started_at + timedelta(seconds=6),
                ),
                DispatchMessage(
                    player_name="Медик",
                    sender_type="player",
                    server_name="ASPER2",
                    text="кислород чужого сервера",
                    created_at=started_at + timedelta(seconds=7),
                ),
            ]
        )
        db.commit()

        result = client_round_conversation_context(
            server_name="asper1",
            query="кислород",
            limit=100,
            db=db,
        )

        assert result["round_id"] == "round-search"
        assert result["total"] == 1
        assert result["truncated"] is False
        assert [item["text"] for item in result["messages"]] == [
            "Проверил кислородный генератор."
        ]
    finally:
        db.close()
        engine.dispose()


def test_addressed_transmissions_are_split_for_full_prefixed_radio_limit() -> None:
    result = {
        "transmissions": [
            {
                "target": "Капитан, борт «Акула»",
                "message": (
                    "Первая завершённая сводка по сектору принята. "
                    "Вторая завершённая сводка содержит отдельное распоряжение экипажу."
                ),
            },
            {"target": "Снегирь", "message": "Следуйте к борту «Акула»."},
        ]
    }

    transmissions = _extract_ai_dispatcher_transmissions(
        result,
        default_target="Неопределено",
    )

    assert transmissions[-1] == {
        "target": "Снегирь",
        "message": "Следуйте к борту «Акула».",
    }
    assert all(
        len(f'[Диспетчер] -> «{item["target"]}» {item["message"]}') <= 200
        for item in transmissions
    )


def test_model_reminder_is_idempotent_and_due_turn_is_queued() -> None:
    engine = _engine_for(AIDispatcherReminder.__table__)
    payload = {
        "event_id": "alert-1",
        "round_id": "round-1",
        "communication_context": {
            "source": None,
            "default_target": "Борт «Акула»",
            "available_targets": [],
            "recent_messages": [],
        },
    }
    result = {
        "reminders": [
            {
                "delay_seconds": 60,
                "note": "Контакт 42 был в 800 метрах от Yasa; сравнить дальность.",
            }
        ]
    }
    jobs = []
    try:
        with patch("routes.dispatch.GameSessionLocal", side_effect=lambda: Session(engine)):
            assert _persist_ai_dispatcher_reminders(
                result,
                payload=payload,
                server_name="ASPER1",
            ) == 1
            assert _persist_ai_dispatcher_reminders(
                result,
                payload=payload,
                server_name="ASPER1",
            ) == 0

        db = Session(engine)
        reminder = db.query(AIDispatcherReminder).one()
        reminder_id = int(reminder.id)
        reminder.due_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
        db.close()

        with (
            patch("routes.dispatch.GameSessionLocal", side_effect=lambda: Session(engine)),
            patch("routes.dispatch._ai_dispatcher_enabled", return_value=True),
            patch(
                "routes.dispatch._resolve_ai_conversation_round_id",
                return_value="round-1",
            ),
            patch(
                "routes.dispatch._enqueue_ai_dispatch_job",
                side_effect=lambda job: jobs.append(job) or True,
            ),
            patch("routes.dispatch.settings.ai_dispatcher_url", "http://ai/messages"),
            patch("routes.dispatch.settings.ai_dispatcher_api_key", "secret"),
        ):
            status = _queue_due_ai_dispatcher_reminders()

        assert status == {"status": "ok", "queued": 1, "cancelled": 0}
        assert len(jobs) == 1
        assert jobs[0].payload["message_type"] == "scheduled_follow_up"
        assert jobs[0].payload["follow_up_context"]["note"].startswith("Контакт 42")
        assert jobs[0].reminder_ids == (reminder_id,)
    finally:
        engine.dispose()


def test_tracked_contact_exposes_movement_but_hides_distant_species() -> None:
    engine = _engine_for(WorldRound.__table__, AICreatureAlert.__table__)
    db = Session(engine)
    try:
        world_round = WorldRound(round_id="round-1", server_name="ASPER1")
        db.add(world_round)
        db.add_all(
            [
                AICreatureAlert(
                    round_id="round-1",
                    character_id="42",
                    species="Moloch",
                    distance_from_yasa_m_approx=800,
                    reported_distance_from_yasa_m_approx=1000,
                    last_seen_at=datetime.utcnow(),
                ),
                AICreatureAlert(
                    round_id="round-1",
                    character_id="yasa:7",
                    species="Crawler",
                    distance_from_yasa_m_approx=120,
                    reported_distance_from_yasa_m_approx=140,
                    last_seen_at=datetime.utcnow(),
                ),
            ]
        )
        db.commit()

        contacts = _serialize_ai_tracked_contacts(db, world_round)

        distant = next(item for item in contacts if item["contact_id"] == "42")
        nearby = next(item for item in contacts if item["contact_id"] == "7")
        assert distant["movement_relative_to_yasa"] == "closer"
        assert distant["distance_change_from_reported_m"] == -200
        assert distant["contact_names"] == []
        assert nearby["contact_names"] == ["Ползун"]
    finally:
        db.close()
        engine.dispose()


def test_tracked_watcher_and_giant_spineling_remain_large_without_env_allowlist() -> None:
    engine = _engine_for(WorldRound.__table__, AICreatureAlert.__table__)
    db = Session(engine)
    try:
        world_round = WorldRound(round_id="round-1", server_name="ASPER1")
        db.add(world_round)
        db.add_all(
            [
                AICreatureAlert(
                    round_id="round-1",
                    character_id="watcher-1",
                    species="Watcher",
                    distance_from_yasa_m_approx=1200,
                    last_seen_at=datetime.utcnow(),
                ),
                AICreatureAlert(
                    round_id="round-1",
                    character_id="spineling-1",
                    species="Spineling_giant",
                    distance_from_yasa_m_approx=900,
                    last_seen_at=datetime.utcnow(),
                ),
            ]
        )
        db.commit()

        with patch("routes.world_state.settings.ai_large_creature_species_list", []):
            contacts = _serialize_ai_tracked_contacts(db, world_round)

        assert {item["contact_id"] for item in contacts} == {
            "watcher-1",
            "spineling-1",
        }
        assert all(item["contact_class"] == "large" for item in contacts)
    finally:
        db.close()
        engine.dispose()


def test_successful_alert_delivery_records_comparison_baseline() -> None:
    engine = _engine_for(AICreatureAlert.__table__)
    db = Session(engine)
    alert = AICreatureAlert(
        round_id="round-1",
        character_id="42",
        species="Moloch",
        distance_from_yasa_m_approx=925,
        queued_at=datetime.utcnow(),
    )
    db.add(alert)
    db.commit()
    alert_id = int(alert.id)
    db.close()
    try:
        with patch("routes.dispatch.GameSessionLocal", side_effect=lambda: Session(engine)):
            _finish_ai_creature_alert_delivery((alert_id,), delivered=True)

        verify = Session(engine)
        updated = verify.query(AICreatureAlert).filter_by(id=alert_id).one()
        assert updated.delivered_at is not None
        assert updated.reported_distance_from_yasa_m_approx == 925
        verify.close()
    finally:
        engine.dispose()
