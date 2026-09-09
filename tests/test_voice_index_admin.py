from datetime import datetime, timedelta
import asyncio
import json
import os
import struct
import sys
import tempfile
import types
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

if sys.platform == "win32" and "fcntl" not in sys.modules:
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX = 2
    fcntl.LOCK_UN = 8
    fcntl.flock = lambda *_args, **_kwargs: None
    sys.modules["fcntl"] = fcntl

from models import (
    AdminUser,
    Base,
    PlayerData,
    ReplayVoiceSegment,
    ReplayVoiceSpeaker,
    RoundReplay,
    VoiceIndexRun,
    VoiceIndexRunLog,
)
from routes.voice_index import (
    api_cancel_voice_index,
    api_get_voice_index_logs,
    api_get_voice_speaker_details,
    api_resolve_voice_speakers,
    api_start_voice_index,
)
from services_voice_index import get_active_voice_index_run, queue_voice_index_backfill, task_run_voice_backfill
from workers import task_index_replay_voice


def _db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _admin(superuser: bool):
    return AdminUser(
        discord_username="admin" if superuser else "moderator",
        is_superuser=superuser,
    )


def _json_request(payload: dict) -> Request:
    body = json.dumps(payload).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": "/api/v1/admin/voice-index", "headers": []},
        receive,
    )


def _demo(speaker_id: int = 7):
    def string(value: str) -> bytes:
        raw = value.encode("utf-8")
        return struct.pack("<H", len(raw)) + raw

    return (
        b"BDMO"
        + struct.pack("<HHBq", 4, 1, 10, 1753000000000)
        + string("ASPER1") + string("seed") + string("Ridge") + string("Orca")
        + struct.pack("<I", 0)
        + bytes([0x07]) + struct.pack("<IHH", 100, speaker_id, 3) + b"abc"
        + bytes([0xFF]) + struct.pack("<I", 1000)
    )


def test_non_superuser_cannot_start_voice_index():
    admin = _admin(False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api_start_voice_index(db=None, current_admin=admin))
    assert exc.value.status_code == 403


def test_start_rejects_existing_active_run():
    db = _db()
    admin = _admin(True)
    db.add(admin)
    db.commit()
    db.add(VoiceIndexRun(requested_by_admin_id=admin.id, status="running"))
    db.commit()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api_start_voice_index(db=db, current_admin=admin))
    assert exc.value.status_code == 409


def test_superuser_start_creates_run_and_queues_backfill():
    db = _db()
    admin = _admin(True)
    db.add(admin)
    db.commit()

    with patch("routes.voice_index.queue_voice_index_backfill", return_value="voice-run-1") as queue:
        response = asyncio.run(api_start_voice_index(db=db, current_admin=admin))

    assert response["status"] == "pending"
    queue.assert_called_once_with(response["id"])
    assert db.query(VoiceIndexRunLog).filter(VoiceIndexRunLog.run_id == response["id"]).count() == 1


def test_web_worker_does_not_queue_backfill_into_private_scheduler(monkeypatch):
    monkeypatch.setenv("WORKER_ROLE", "web")

    with patch("task_scheduler.scheduler.submit") as submit:
        task_id = queue_voice_index_backfill(123)

    assert task_id == "voice_index_backfill_run_123"
    submit.assert_not_called()


def test_scheduler_scan_claims_pending_manual_backfill():
    db = _db()
    try:
        run = VoiceIndexRun(status="pending", requested_by_admin_id=1)
        db.add(run)
        db.commit()

        with patch("workers.SessionLocal", return_value=db), patch(
            "services_voice_index.queue_voice_index_backfill",
            return_value="voice_index_backfill_run_1",
        ) as queue:
            result = task_index_replay_voice()

        assert result == {"manual_run_id": run.id, "manual_run_queued": True}
        queue.assert_called_once_with(run.id)
    finally:
        db.close()


def test_cancel_pending_run_marks_it_cancelled():
    db = _db()
    admin = _admin(True)
    db.add(admin)
    db.commit()
    run = VoiceIndexRun(requested_by_admin_id=admin.id, status="pending")
    db.add(run)
    db.commit()

    response = asyncio.run(api_cancel_voice_index(run.id, db=db, current_admin=admin))

    assert response["status"] == "cancelled"
    db.refresh(run)
    assert run.status == "cancelled"


def test_cancel_running_run_becomes_terminal_immediately():
    db = _db()
    admin = _admin(True)
    db.add(admin)
    db.commit()
    run = VoiceIndexRun(requested_by_admin_id=admin.id, status="running")
    db.add(run)
    db.commit()

    response = asyncio.run(api_cancel_voice_index(run.id, db=db, current_admin=admin))

    assert response["status"] == "cancelled"
    db.refresh(run)
    assert run.status == "cancelled"


def test_cancelled_run_worker_finishes_without_processing_replays():
    db = _db()
    admin = _admin(True)
    db.add(admin)
    db.commit()
    run = VoiceIndexRun(requested_by_admin_id=admin.id, status="cancelling")
    db.add(run)
    db.commit()

    result = task_run_voice_backfill(run.id, session_factory=lambda: db)

    assert result["status"] == "cancelled"
    db.refresh(run)
    assert run.status == "cancelled"


def test_restart_recovers_legacy_cancelling_run():
    db = _db()
    admin = _admin(True)
    db.add(admin)
    db.commit()
    run = VoiceIndexRun(requested_by_admin_id=admin.id, status="cancelling")
    db.add(run)
    db.commit()

    assert get_active_voice_index_run(db) is None
    db.refresh(run)
    assert run.status == "cancelled"


def test_backfill_run_records_ready_and_partial_logs():
    db = _db()
    admin = _admin(True)
    db.add(admin)
    db.commit()
    good = RoundReplay(
        filename="good.barodemo",
        stored_filename="good.barodemo",
        uploaded_at=datetime.utcnow() - timedelta(minutes=2),
        has_voice=True,
        voice_index_status="pending",
        stream_id="good-stream",
    )
    bad = RoundReplay(
        filename="bad.barodemo",
        stored_filename="bad.barodemo",
        uploaded_at=datetime.utcnow() - timedelta(minutes=1),
        has_voice=True,
        voice_index_status="pending",
    )
    db.add_all([good, bad])
    from models import WorldRound
    db.add(WorldRound(round_id="good-stream", character_steamids={"7": "steam-good"}))
    db.commit()
    run = VoiceIndexRun(requested_by_admin_id=admin.id)
    db.add(run)
    db.commit()

    with tempfile.TemporaryDirectory() as temp_dir:
        good_path = os.path.join(temp_dir, "good.barodemo")
        bad_path = os.path.join(temp_dir, "bad.barodemo")
        open(good_path, "wb").write(_demo())
        open(bad_path, "wb").write(_demo(8))

        class FakeReplayService:
            def __init__(self, _db):
                pass

            def get_file_path(self, replay):
                return good_path if replay.id == good.id else bad_path

        with patch("services_replays.ReplayService", FakeReplayService):
            result = task_run_voice_backfill(run.id, session_factory=lambda: db)

    db.refresh(run)
    assert result["status"] == "completed"
    assert (run.total, run.processed, run.ready_count, run.partial_count, run.error_count) == (2, 2, 1, 1, 0)
    assert run.segment_count == 1
    assert run.profile_count == 1
    logs = db.query(VoiceIndexRunLog).filter(VoiceIndexRunLog.run_id == run.id).all()
    assert {log.status for log in logs} == {"ready", "partial"}
    assert db.query(ReplayVoiceSegment).count() == 1


def test_backfill_truncates_long_replay_error_in_run_message():
    db = _db()
    admin = _admin(True)
    db.add(admin)
    db.commit()
    replay = RoundReplay(
        filename="broken.barodemo",
        stored_filename="broken.barodemo",
        uploaded_at=datetime.utcnow(),
        has_voice=True,
        voice_index_status="pending",
    )
    db.add(replay)
    run = VoiceIndexRun(requested_by_admin_id=admin.id)
    db.add(run)
    db.commit()

    with patch(
        "services_voice_index.VoiceIndexService.index_replay",
        return_value={"status": "error", "replay_id": replay.id, "error": "x" * 6000},
    ):
        result = task_run_voice_backfill(run.id, session_factory=lambda: db)

    assert result["status"] == "completed"
    db.refresh(run)
    assert len(run.last_message) <= 1000


def test_log_list_is_paginated():
    db = _db()
    admin = _admin(True)
    db.add(admin)
    db.commit()
    run = VoiceIndexRun(requested_by_admin_id=admin.id, status="completed")
    db.add(run)
    db.commit()
    db.add_all(
        [
            VoiceIndexRunLog(run_id=run.id, status="info", message=f"log {i}")
            for i in range(3)
        ]
    )
    db.commit()

    response = asyncio.run(
        api_get_voice_index_logs(
            run_id=run.id,
            page=2,
            page_size=2,
            db=db,
            current_admin=admin,
        )
    )
    assert response["total"] == 3
    assert response["pages"] == 2
    assert len(response["items"]) == 1

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            api_get_voice_index_logs(
                run_id=run.id,
                page=1,
                page_size=20,
                db=db,
                current_admin=_admin(False),
            )
        )
    assert exc.value.status_code == 403


def test_log_list_hides_resolved_replay_from_review_button_state():
    db = _db()
    admin = _admin(True)
    db.add(admin)
    db.commit()
    run = VoiceIndexRun(requested_by_admin_id=admin.id, status="completed")
    replay = RoundReplay(
        filename="round.barodemo",
        stored_filename="round.barodemo",
        voice_index_status="partial",
    )
    db.add_all([run, replay])
    db.flush()
    db.add(VoiceIndexRunLog(run_id=run.id, replay_id=replay.id, status="partial", message="needs mapping"))
    speaker = ReplayVoiceSpeaker(
        replay_id=replay.id,
        speaker_id=383,
        resolution_status="unresolved",
    )
    db.add(speaker)
    db.commit()

    response = asyncio.run(api_get_voice_index_logs(
        run_id=run.id,
        page=1,
        page_size=20,
        db=db,
        current_admin=admin,
    ))
    assert response["items"][0]["needs_resolution"] is True
    assert response["items"][0]["unresolved_count"] == 1

    speaker.resolution_status = "mapped"
    db.commit()
    response = asyncio.run(api_get_voice_index_logs(
        run_id=run.id,
        page=1,
        page_size=20,
        db=db,
        current_admin=admin,
    ))
    assert response["items"][0]["needs_resolution"] is False
    assert response["items"][0]["unresolved_count"] == 0


def test_non_superuser_cannot_view_voice_speaker_details():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api_get_voice_speaker_details(
            replay_id=1,
            db=None,
            current_admin=_admin(False),
        ))
    assert exc.value.status_code == 403


def test_superuser_can_view_and_resolve_voice_speakers():
    db = _db()
    admin = _admin(True)
    db.add(admin)
    db.add(PlayerData(player_id=7, player_last_time_name="Alice", player_steamid="steam-alice"))
    replay = RoundReplay(
        filename="round.barodemo",
        stored_filename="round.barodemo",
        voice_index_status="partial",
    )
    db.add(replay)
    db.flush()
    db.add(ReplayVoiceSpeaker(
        replay_id=replay.id,
        speaker_id=383,
        names=["Alice"],
        candidate_profiles=[{"player_id": 7, "steam_id": "steam-alice", "name": "Alice"}],
        resolution_status="unresolved",
        packet_count=4,
        start_ms=100,
        end_ms=900,
    ))
    db.commit()

    details = asyncio.run(api_get_voice_speaker_details(
        replay_id=replay.id,
        db=db,
        current_admin=admin,
    ))
    assert details["items"][0]["speaker_id"] == 383
    assert details["items"][0]["candidate_profiles"][0]["steam_id"] == "steam-alice"

    request = _json_request({
        "decisions": [{"speaker_id": 383, "action": "map", "steam_id": "steam-alice"}]
    })
    with patch("routes.voice_index.queue_replay_voice_index", return_value="voice-383") as queue:
        response = asyncio.run(api_resolve_voice_speakers(
            replay_id=replay.id,
            request=request,
            db=db,
            current_admin=admin,
        ))

    assert response["status"] == "queued"
    queue.assert_called_once_with(replay.id)
    row = db.query(ReplayVoiceSpeaker).one()
    assert row.resolution_status == "mapped"
    assert row.mapping_source == "manual"
    assert row.steam_id == "steam-alice"
