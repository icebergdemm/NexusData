import os
import struct
import tempfile
import asyncio
import json
import sys
import types
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

if sys.platform == "win32" and "fcntl" not in sys.modules:
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX = 2
    fcntl.LOCK_UN = 8
    fcntl.flock = lambda *_args, **_kwargs: None
    sys.modules["fcntl"] = fcntl
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import models
from services_voice_index import VoiceIndexService, _mapping_from_voice_communications


def _str(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<H", len(raw)) + raw


def _demo(speaker_id: int = 7) -> bytes:
    return (
        b"BDMO"
        + struct.pack("<HHBq", 4, 1, 10, 1753000000000)
        + _str("ASPER1") + _str("seed") + _str("Ridge") + _str("Orca")
        + struct.pack("<I", 0)
        + bytes([0x07]) + struct.pack("<IHH", 100, speaker_id, 3) + b"abc"
        + bytes([0xFF]) + struct.pack("<I", 1000)
    )


def _db():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine, tables=[
        models.RoundReplay.__table__,
        models.ReplayVoiceSegment.__table__,
        models.WorldRound.__table__,
        models.PlayerCommunication.__table__,
    ])
    return engine, Session(engine)


def _json_request(payload: dict) -> Request:
    body = json.dumps(payload).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": "/api/v1/world/voice-map", "headers": []},
        receive,
    )


def test_index_replaces_segments_and_is_idempotent():
    engine, db = _db()
    try:
        with tempfile.TemporaryDirectory(prefix="voice-service-") as directory:
            path = os.path.join(directory, "round.barodemo")
            with open(path, "wb") as replay_file:
                replay_file.write(_demo())
            replay = models.RoundReplay(
                id=11,
                filename="round.barodemo",
                stored_filename="round.barodemo",
                has_voice=1,
                uploaded_at=datetime.utcnow(),
                voice_index_status="pending",
                stream_id="stream-11",
            )
            db.add(replay)
            db.add(models.WorldRound(round_id="stream-11", character_steamids={"7": "steam-7"}))
            db.commit()

            class FakeReplayService:
                def __init__(self, _db):
                    pass

                def get_file_path(self, _replay):
                    return path

            with patch("services_replays.ReplayService", FakeReplayService):
                service = VoiceIndexService(db)
                assert service.index_replay(11)["status"] == "ready"
                assert service.index_replay(11)["status"] == "ready"

            assert db.query(models.ReplayVoiceSegment).count() == 1
            assert db.query(models.RoundReplay).one().voice_index_status == "ready"
    finally:
        db.close()
        engine.dispose()


def test_unmapped_speaker_marks_replay_as_error_without_creating_segment():
    engine, db = _db()
    try:
        with tempfile.TemporaryDirectory(prefix="voice-service-") as directory:
            path = os.path.join(directory, "round.barodemo")
            with open(path, "wb") as replay_file:
                replay_file.write(_demo(99))
            replay = models.RoundReplay(
                id=12,
                filename="round.barodemo",
                stored_filename="round.barodemo",
                has_voice=1,
                uploaded_at=datetime.utcnow(),
                voice_index_status="pending",
            )
            db.add(replay)
            db.commit()

            class FakeReplayService:
                def __init__(self, _db):
                    pass

                def get_file_path(self, _replay):
                    return path

            with patch("services_replays.ReplayService", FakeReplayService):
                result = VoiceIndexService(db).index_replay(12)

            assert result["status"] == "error"
            assert db.query(models.ReplayVoiceSegment).count() == 0
            assert db.query(models.RoundReplay).one().voice_index_status == "error"
    finally:
        db.close()
        engine.dispose()


def test_old_replay_recovers_speaker_mapping_from_server_asr_event_time():
    engine, db = _db()
    try:
        recorded_at = datetime(2025, 1, 1, 12, 0, 0)
        replay = models.RoundReplay(
            id=13,
            filename="round.barodemo",
            stored_filename="round.barodemo",
            server_name="ASPER1",
            recorded_at=recorded_at,
        )
        db.add(replay)
        db.add(models.PlayerCommunication(
            event_uid="voice-event-13",
            source="voice",
            channel="default",
            server_name="ASPER1",
            server_session_id="session-1",
            player_steamid="76561198000000013",
            occurred_at=recorded_at.replace(microsecond=100000),
        ))
        db.commit()

        mapping = _mapping_from_voice_communications(db, replay, [(100, 7), (300, 7)])

        assert mapping == {7: "76561198000000013"}
    finally:
        db.close()
        engine.dispose()


def test_voice_api_rejects_non_superuser_before_touching_player_data():
    from routes.admin import api_player_voice_segments

    with pytest.raises(HTTPException) as error:
        asyncio.run(api_player_voice_segments(
            player_id=1,
            page=1,
            page_size=25,
            db=None,
            current_admin=type("Admin", (), {"is_superuser": False})(),
        ))

    assert error.value.status_code == 403


def test_stream_voice_map_merges_late_joiner_identity():
    from routes.world_state import world_voice_map

    engine, db = _db()
    try:
        db.add(models.WorldRound(round_id="stream-late"))
        db.commit()
        asyncio.run(world_voice_map(
            _json_request({
                "round_id": "stream-late",
                "speaker_id": 42,
                "steam_id": "eos-42",
            }),
            db=db,
        ))
        assert db.query(models.WorldRound).one().character_steamids == {"42": "eos-42"}
    finally:
        db.close()
        engine.dispose()
