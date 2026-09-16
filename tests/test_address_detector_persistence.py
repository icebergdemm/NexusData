from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from discord_bot.address_detector.analyzer import AnalysisResult, Verdict
from discord_bot.address_detector.context import AddressContext
from discord_bot.address_detector.persistence import AddressDetectorStore


def context(message_id: int, *, author_id: int = 7, timestamp: datetime | None = None) -> AddressContext:
    return AddressContext(
        message_id=message_id,
        guild_id=1,
        channel_id=2,
        author_id=author_id,
        author_display_name="Test User",
        timestamp=timestamp or datetime.now(timezone.utc),
        raw_content="ты где?",
        cleaned_content="ты где?",
        trigger_tokens=("ты",),
        is_reply=False,
        reply=None,
        reply_author_id=None,
        mentions=(),
        previous_messages=(),
        jump_url=f"https://discord.com/channels/1/2/{message_id}",
    )


def test_signal_is_idempotent_and_incident_pings_once() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    store = AddressDetectorStore(sessions)
    first = context(10)

    assert store.upsert_candidate(first, ("ты",)) is True
    assert store.upsert_candidate(first, ("ты",)) is False

    created = store.record_analysis(first, AnalysisResult(Verdict.UNCERTAIN, 0.5, "unclear", "rules"), 120)
    second = context(11, timestamp=first.timestamp + timedelta(seconds=30))
    store.upsert_candidate(second, ("ты",))
    aggregated = store.record_analysis(second, AnalysisResult(Verdict.LIKELY_VIOLATION, 0.9, "reply", "rules"), 120)

    assert created.incident_id == aggregated.incident_id
    assert created.should_ping_moderators is True
    assert aggregated.should_ping_moderators is False
    assert store.get_signal(10).processing_status == "classified"


def test_deleted_message_keeps_snapshot_and_marks_signal() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    store = AddressDetectorStore(sessionmaker(bind=engine))
    current = context(20)
    store.upsert_candidate(current, ("ты",))
    store.record_analysis(current, AnalysisResult(Verdict.UNCERTAIN, None, "fallback", "rules"), 120)

    store.mark_deleted(20)

    signal = store.get_signal(20)
    assert signal.deleted_at is not None
    assert signal.raw_content == "ты где?"
