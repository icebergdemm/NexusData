import asyncio
import sys
import types
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import models

if sys.platform == "win32" and "fcntl" not in sys.modules:
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX = 2
    fcntl.LOCK_UN = 8
    fcntl.flock = lambda *_args, **_kwargs: None
    sys.modules["fcntl"] = fcntl

from routes.admin import api_player_voice_clips


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    return engine, Session(engine)


def _admin(superuser: bool):
    return models.AdminUser(discord_username="voice-test", is_superuser=superuser)


def test_voice_clip_api_requires_superuser():
    engine, db = _db()
    try:
        db.add(models.PlayerData(player_id=7, player_steamid="steam-7", player_last_time_name="Alice"))
        db.commit()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(api_player_voice_clips(7, db=db, current_admin=_admin(False)))
        assert exc.value.status_code == 403
    finally:
        db.close()
        engine.dispose()


def test_voice_clip_api_groups_and_paginates_replays():
    engine, db = _db()
    try:
        db.add(models.PlayerData(player_id=7, player_steamid="steam-7", player_last_time_name="Alice"))
        for replay_id in (1, 2):
            clip = models.VoiceArchiveClip(
                archive_key=("a" if replay_id == 1 else "b") * 64,
                steam_id="steam-7",
                source_replay_id=replay_id,
                source_replay_filename=f"round-{replay_id}.barodemo",
                server_name="ASPER1",
                recorded_at=datetime(2026, 9, replay_id),
                speaker_id=7,
                start_ms=100,
                end_ms=2100,
                packet_count=2,
                storage_filename=f"voice_archive/{replay_id}.ndvc",
                storage_size=18,
                archive_version=1,
            )
            db.add(clip)
            db.flush()
            db.add(models.VoiceArchiveSelection(steam_id="steam-7", slot=replay_id - 1, band="early", clip=clip))
        db.commit()

        response = asyncio.run(api_player_voice_clips(7, page=1, page_size=1, db=db, current_admin=_admin(True)))

        assert response["total_groups"] == 2
        assert response["total_clips"] == 2
        assert response["total_pages"] == 2
        assert len(response["items"]) == 1
        assert response["items"][0]["replay_id"] == 2
        assert response["items"][0]["clips"][0]["audio_url"].endswith("/audio")
    finally:
        db.close()
        engine.dispose()
