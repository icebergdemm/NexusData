from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import AppSetting, PlayerCommunication, PlayerCommunicationRecipient, RoundEvent
from routes.communications import (
    ingest_communications,
    purge_expired_communications,
    reconcile_stale_pending_communications,
)
from schemas import CommunicationBatchIn


def _db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _batch(*, uid="chat-1", status="not_applicable", text="hello", token="round-1", source="chat"):
    return CommunicationBatchIn.model_validate({
        "server_name": "ASPER1",
        "server_session_id": "session-1",
        "events": [{
            "event_uid": uid,
            "source": source,
            "channel": "default",
            "server_name": "ASPER1",
            "server_session_id": "session-1",
            "round_token": token,
            "player_steamid": "76561198000000001",
            "player_name": "Player",
            "role": "captain",
            "original_text": text if source == "chat" else None,
            "transcript_text": text if source == "voice" else None,
            "asr_status": status,
            "occurred_at": datetime.utcnow().isoformat(),
            "recipients": [{
                "player_steamid": "76561198000000002",
                "player_name": "Listener",
                "delivered_text": text,
                "channel": "default",
            }],
        }],
    })


def test_batch_is_idempotent_and_links_by_round_token():
    db = _db()
    db.add(RoundEvent(
        event_type="start", server_name="ASPER1", server_session_id="session-1",
        round_token="round-1", created_at=datetime.utcnow(), players=[],
    ))
    db.commit()

    first = ingest_communications(_batch(), db)
    second = ingest_communications(_batch(), db)

    row = db.query(PlayerCommunication).one()
    assert first["inserted"] == 1
    assert second["updated"] == 1
    assert row.round_event_id is not None
    assert len(row.recipients) == 1
    assert row.recipients[0].delivered_text == "hello"


def test_pending_voice_can_be_updated_without_new_row():
    db = _db()
    ingest_communications(_batch(uid="voice-1", status="pending", text=None, source="voice"), db)
    ingest_communications(_batch(uid="voice-1", status="recognized", text="recognized text", token=None, source="voice"), db)

    rows = db.query(PlayerCommunication).all()
    assert len(rows) == 1
    assert rows[0].asr_status == "recognized"
    assert rows[0].original_text is None
    assert rows[0].transcript_text == "recognized text"


def test_lobby_message_does_not_attach_to_any_round():
    db = _db()
    db.add(RoundEvent(
        event_type="start", server_name="ASPER1", server_session_id="session-1",
        round_token="round-1", created_at=datetime.utcnow(), players=[],
    ))
    db.commit()

    ingest_communications(_batch(uid="lobby-1", token=None), db)

    row = db.query(PlayerCommunication).one()
    assert row.round_event_id is None


def test_retention_deletes_recipients_with_parent():
    db = _db()
    db.add(AppSetting(key="communications_retention_days", value="90"))
    old = PlayerCommunication(
        event_uid="old", source="chat", channel="default", server_name="ASPER1",
        server_session_id="session-1", player_name="Old", occurred_at=datetime.utcnow() - timedelta(days=100),
        recipients=[PlayerCommunicationRecipient(player_steamid="76561198000000002")],
    )
    db.add(old)
    db.commit()

    assert purge_expired_communications(db) == 1
    assert db.query(PlayerCommunication).count() == 0
    assert db.query(PlayerCommunicationRecipient).count() == 0


def test_stale_pending_voice_is_marked_failed_after_server_restart():
    db = _db()
    row = PlayerCommunication(
        event_uid="stale-voice",
        source="voice",
        channel="local",
        server_name="ASPER1",
        server_session_id="old-session",
        player_name="Old",
        asr_status="pending",
        occurred_at=datetime.utcnow() - timedelta(minutes=10),
    )
    db.add(row)
    db.commit()

    assert reconcile_stale_pending_communications(db) == 1
    db.refresh(row)
    assert row.asr_status == "failed"
    assert "timed out" in (row.asr_error or "")
