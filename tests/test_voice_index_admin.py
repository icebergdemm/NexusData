from datetime import datetime, timedelta
import asyncio
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

if sys.platform == "win32" and "fcntl" not in sys.modules:
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX = 2
    fcntl.LOCK_UN = 8
    fcntl.flock = lambda *_args, **_kwargs: None
    sys.modules["fcntl"] = fcntl

from models import (
    AdminUser,
    Base,
    ReplayVoiceSegment,
    RoundReplay,
    VoiceIndexRun,
    VoiceIndexRunLog,
)
from routes.voice_index import (
    api_cancel_voice_index,
    api_get_voice_index_logs,
    api_start_voice_index,
)
from services_voice_index import task_run_voice_backfill


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


def test_cancel_running_run_requests_stop_after_current_replay():
    db = _db()
    admin = _admin(True)
    db.add(admin)
    db.commit()
    run = VoiceIndexRun(requested_by_admin_id=admin.id, status="running")
    db.add(run)
    db.commit()

    response = asyncio.run(api_cancel_voice_index(run.id, db=db, current_admin=admin))

    assert response["status"] == "cancelling"
    db.refresh(run)
    assert run.status == "cancelling"


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


def test_backfill_run_records_success_and_error_logs():
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
    assert (run.total, run.processed, run.ready_count, run.error_count) == (2, 2, 1, 1)
    logs = db.query(VoiceIndexRunLog).filter(VoiceIndexRunLog.run_id == run.id).all()
    assert {log.status for log in logs} == {"ready", "error"}
    assert db.query(ReplayVoiceSegment).count() == 1


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
