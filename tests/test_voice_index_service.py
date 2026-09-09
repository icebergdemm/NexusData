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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import models
from services_voice_index import (
    VoiceIndexService,
    _mapping_from_player_names,
    _mapping_from_voice_communications,
    extract_voice_packets_with_names,
)


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
        models.AppSetting.__table__,
        models.ReplayVoiceSegment.__table__,
        models.WorldRound.__table__,
        models.PlayerCommunication.__table__,
        models.PlayerData.__table__,
        models.PlayerName.__table__,
        models.ReplayVoiceSpeaker.__table__,
        models.VoiceArchiveClip.__table__,
        models.VoiceArchiveSelection.__table__,
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


def test_reindex_removes_archive_clip_after_manual_mapping_changes_profile():
    engine, db = _db()
    try:
        with tempfile.TemporaryDirectory(prefix="voice-remap-") as directory:
            path = os.path.join(directory, "round.barodemo")
            with open(path, "wb") as replay_file:
                replay_file.write(_demo())
            replay = models.RoundReplay(
                id=18,
                filename="round.barodemo",
                stored_filename="round.barodemo",
                has_voice=1,
                voice_index_status="pending",
                stream_id="stream-18",
            )
            db.add(replay)
            db.add(models.WorldRound(round_id="stream-18", character_steamids={"7": "steam-old"}))
            db.commit()

            class FakeReplayService:
                def __init__(self, _db):
                    pass

                def get_file_path(self, _replay):
                    return path

            with patch("services_replays.ReplayService", FakeReplayService), patch(
                "services_voice_archive.get_upload_dir", return_value=directory
            ):
                service = VoiceIndexService(db)
                assert service.index_replay(replay.id)["status"] == "ready"
                assert db.query(models.VoiceArchiveClip).filter_by(steam_id="steam-old").count() == 1

                world_round = db.query(models.WorldRound).filter_by(round_id="stream-18").one()
                world_round.character_steamids = {"7": "steam-new"}
                replay.voice_index_version = 0
                replay.voice_archive_version = 0
                db.commit()
                db.expire_all()
                assert service._mapping_for_replay(replay, [(100, 7)]) == {7: "steam-new"}

                result = service.index_replay(replay.id)
                assert result["status"] == "ready"
                assert result["profile_ids"] == ["steam-new"]

                assert db.query(models.VoiceArchiveClip).filter_by(steam_id="steam-old").count() == 0
            assert db.query(models.VoiceArchiveClip).filter_by(steam_id="steam-new").count() == 1
    finally:
        db.close()
        engine.dispose()


def test_index_retries_after_concurrent_speaker_row_insert():
    engine, db = _db()
    try:
        with tempfile.TemporaryDirectory(prefix="voice-retry-") as directory:
            path = os.path.join(directory, "round.barodemo")
            with open(path, "wb") as replay_file:
                replay_file.write(_demo())
            replay = models.RoundReplay(
                id=17,
                filename="round.barodemo",
                stored_filename="round.barodemo",
                has_voice=1,
                voice_index_status="pending",
            )
            db.add(replay)
            db.add(models.WorldRound(round_id="stream-17", character_steamids={"7": "steam-7"}))
            replay.stream_id = "stream-17"
            db.commit()

            class FakeReplayService:
                def __init__(self, _db):
                    pass

                def get_file_path(self, _replay):
                    return path

            service = VoiceIndexService(db)
            original_save = service._save_speaker_details
            calls = 0

            def flaky_save(*args, **kwargs):
                nonlocal calls
                if calls == 0:
                    calls += 1
                    raise IntegrityError("INSERT", {}, Exception("uq_replay_voice_speaker"))
                return original_save(*args, **kwargs)

            service._save_speaker_details = flaky_save
            with patch("services_replays.ReplayService", FakeReplayService):
                result = service.index_replay(replay.id)

            assert result["status"] == "ready"
            assert db.query(models.ReplayVoiceSpeaker).count() == 1
    finally:
        db.close()
        engine.dispose()


def test_unmapped_speaker_keeps_replay_partial_without_creating_segment():
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

            assert result["status"] == "partial"
            assert db.query(models.ReplayVoiceSegment).count() == 0
            assert db.query(models.RoundReplay).one().voice_index_status == "partial"
            speaker = db.query(models.ReplayVoiceSpeaker).one()
            assert speaker.speaker_id == 99
            assert speaker.resolution_status == "unresolved"
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


def _demo_with_spawn(speaker_id: int, nickname: str) -> bytes:
    return (
        b"BDMO"
        + struct.pack("<HHBq", 4, 1, 10, 1753000000000)
        + _str("ASPER1") + _str("seed") + _str("Ridge") + _str("Orca")
        + struct.pack("<I", 0)
        + bytes([0x03]) + struct.pack("<IH", 0, speaker_id)
        + _str("human") + _str(nickname) + bytes([0])
        + bytes([0x07]) + struct.pack("<IHH", 100, speaker_id, 3) + b"abc"
        + bytes([0xFF]) + struct.pack("<I", 1000)
    )


def _demo_with_two_spawns() -> bytes:
    return (
        b"BDMO"
        + struct.pack("<HHBq", 4, 1, 10, 1753000000000)
        + _str("ASPER1") + _str("seed") + _str("Ridge") + _str("Orca")
        + struct.pack("<I", 0)
        + bytes([0x03]) + struct.pack("<IH", 0, 7)
        + _str("human") + _str("Alice") + bytes([0])
        + bytes([0x03]) + struct.pack("<IH", 0, 8)
        + _str("human") + _str("Unknown") + bytes([0])
        + bytes([0x07]) + struct.pack("<IHH", 100, 7, 3) + b"abc"
        + bytes([0x07]) + struct.pack("<IHH", 200, 8, 3) + b"def"
        + bytes([0xFF]) + struct.pack("<I", 1000)
    )


def test_old_replay_uses_unique_historical_nickname_to_recover_mapping():
    engine, db = _db()
    try:
        with tempfile.TemporaryDirectory(prefix="voice-name- service-") as directory:
            path = os.path.join(directory, "round.barodemo")
            with open(path, "wb") as replay_file:
                replay_file.write(_demo_with_spawn(383, "  Alice  "))

            packets, embedded, names = extract_voice_packets_with_names(path)
            assert packets == [(100, 383)]
            assert embedded == {}
            assert names == {383: {"  Alice  "}}

            db.add(models.PlayerData(player_id=1, player_last_time_name="Alice", player_steamid="steam-alice"))
            db.add(models.PlayerName(
                player_id=1,
                nickname="Alice",
                first_seen=datetime(2024, 1, 1),
                last_seen=datetime(2026, 12, 31),
            ))
            db.commit()
            replay = models.RoundReplay(recorded_at=datetime(2026, 9, 1))

            assert _mapping_from_player_names(db, replay, names) == {383: "steam-alice"}
    finally:
        db.close()
        engine.dispose()


def test_old_replay_uses_spawn_name_even_when_voice_packet_precedes_spawn_frame():
    with tempfile.TemporaryDirectory(prefix="voice-name-order-") as directory:
        path = os.path.join(directory, "round.barodemo")
        with open(path, "wb") as replay_file:
            replay_file.write(
                b"BDMO"
                + struct.pack("<HHBq", 4, 1, 10, 1753000000000)
                + _str("ASPER1") + _str("seed") + _str("Ridge") + _str("Orca")
                + struct.pack("<I", 0)
                + bytes([0x07]) + struct.pack("<IHH", 100, 383, 3) + b"abc"
                + bytes([0x03]) + struct.pack("<IH", 200, 383)
                + _str("human") + _str("Alice") + bytes([0])
                + bytes([0xFF]) + struct.pack("<I", 1000)
            )

        assert extract_voice_packets_with_names(path)[2] == {383: {"Alice"}}


def test_old_replay_does_not_guess_when_nickname_matches_multiple_profiles():
    engine, db = _db()
    try:
        for player_id, steam_id in ((1, "steam-alice-1"), (2, "steam-alice-2")):
            db.add(models.PlayerData(
                player_id=player_id,
                player_last_time_name="Alice",
                player_steamid=steam_id,
            ))
            db.add(models.PlayerName(
                player_id=player_id,
                nickname="Alice",
                first_seen=datetime(2024, 1, 1),
                last_seen=datetime(2026, 12, 31),
            ))
        db.commit()
        replay = models.RoundReplay(recorded_at=datetime(2026, 9, 1))

        assert _mapping_from_player_names(db, replay, {383: {"Alice"}}) == {}
    finally:
        db.close()
        engine.dispose()


def test_index_replay_uses_unique_nickname_fallback_for_old_archive():
    engine, db = _db()
    try:
        with tempfile.TemporaryDirectory(prefix="voice-name-index-") as directory:
            path = os.path.join(directory, "round.barodemo")
            with open(path, "wb") as replay_file:
                replay_file.write(_demo_with_spawn(383, "Alice"))

            db.add(models.PlayerData(player_id=1, player_last_time_name="Alice", player_steamid="steam-alice"))
            db.add(models.PlayerName(
                player_id=1,
                nickname="Alice",
                first_seen=datetime(2024, 1, 1),
                last_seen=datetime(2026, 12, 31),
            ))
            replay = models.RoundReplay(
                id=14,
                filename="round.barodemo",
                stored_filename="round.barodemo",
                has_voice=1,
                recorded_at=datetime(2026, 9, 1),
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
                result = VoiceIndexService(db).index_replay(14)

            assert result["status"] == "ready"
            assert db.query(models.ReplayVoiceSegment).one().steam_id == "steam-alice"
    finally:
        db.close()
        engine.dispose()


def test_index_replay_keeps_mapped_segments_when_other_speakers_are_unresolved():
    engine, db = _db()
    try:
        with tempfile.TemporaryDirectory(prefix="voice-partial-") as directory:
            path = os.path.join(directory, "round.barodemo")
            with open(path, "wb") as replay_file:
                replay_file.write(_demo_with_two_spawns())

            db.add(models.PlayerData(player_id=1, player_last_time_name="Alice", player_steamid="steam-alice"))
            db.add(models.PlayerName(
                player_id=1,
                nickname="Alice",
                first_seen=datetime(2024, 1, 1),
                last_seen=datetime(2026, 12, 31),
            ))
            db.add(models.RoundReplay(
                id=15,
                filename="round.barodemo",
                stored_filename="round.barodemo",
                has_voice=1,
                recorded_at=datetime(2026, 9, 1),
                voice_index_status="pending",
            ))
            db.commit()

            class FakeReplayService:
                def __init__(self, _db):
                    pass

                def get_file_path(self, _replay):
                    return path

            with patch("services_replays.ReplayService", FakeReplayService):
                result = VoiceIndexService(db).index_replay(15)

            assert result["status"] == "partial"
            assert result["segments"] == 1
            assert result["unresolved_speaker_ids"] == [8]
            assert db.query(models.ReplayVoiceSegment).one().steam_id == "steam-alice"
            rows = {
                row.speaker_id: row
                for row in db.query(models.ReplayVoiceSpeaker).all()
            }
            assert rows[7].resolution_status == "mapped"
            assert rows[7].steam_id == "steam-alice"
            assert rows[8].resolution_status == "unresolved"
            assert rows[8].names == ["Unknown"]
    finally:
        db.close()
        engine.dispose()


def test_index_replay_honours_manual_skip_for_unresolved_speaker():
    engine, db = _db()
    try:
        with tempfile.TemporaryDirectory(prefix="voice-skip-") as directory:
            path = os.path.join(directory, "round.barodemo")
            with open(path, "wb") as replay_file:
                replay_file.write(_demo_with_spawn(8, "Unknown"))
            replay = models.RoundReplay(
                id=16,
                filename="round.barodemo",
                stored_filename="round.barodemo",
                has_voice=1,
                voice_index_status="partial",
            )
            db.add(replay)
            db.flush()
            db.add(models.ReplayVoiceSpeaker(
                replay_id=replay.id,
                speaker_id=8,
                names=["Unknown"],
                resolution_status="skipped",
                mapping_source="manual_skip",
            ))
            db.commit()

            class FakeReplayService:
                def __init__(self, _db):
                    pass

                def get_file_path(self, _replay):
                    return path

            with patch("services_replays.ReplayService", FakeReplayService):
                result = VoiceIndexService(db).index_replay(16)

            assert result["status"] == "partial"
            assert result["unresolved_speaker_ids"] == []
            assert result["skipped_speaker_ids"] == [8]
            assert db.query(models.ReplayVoiceSegment).count() == 0
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
