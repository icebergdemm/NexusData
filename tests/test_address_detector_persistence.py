from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import AddressDetectorSignal
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
    assert signal.processing_status == "deleted"


def test_context_snapshot_is_compact_and_revision_results_are_fenced() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    store = AddressDetectorStore(sessionmaker(bind=engine))
    first = context(30)
    assert store.upsert_candidate(first, ("ты",))
    assert store.set_classifier_context(first, 1)

    stored = store.get_signal(30)
    snapshot = stored.context_snapshot
    audit = snapshot["revisions"]["1"]["classifier_context"]
    assert audit == {
        "current": {"speaker": "s1", "text": "ты где?", "reply_to": None, "mentions": []},
        "context": [],
    }
    assert "author_id" not in str(snapshot)
    assert "author_display_name" not in str(snapshot)

    edited = AddressContext(**{**first.__dict__, "raw_content": "обычный текст", "cleaned_content": "обычный текст"})
    revision = store.update_candidate(edited, ())
    assert revision == 2
    assert store.record_analysis(first, AnalysisResult(Verdict.LIKELY_VIOLATION, 0.9, "old", "rules"), 120, 1) is None
    assert store.get_signal(30).classification is None

    assert store.set_classifier_context(edited, 2)
    assert store.record_analysis(edited, AnalysisResult(Verdict.CLEAN, 0.99, "clean", "rules"), 120, 2) is not None
    snapshot = store.get_signal(30).context_snapshot
    assert snapshot["revisions"]["1"]["classifier_context"] == audit
    assert snapshot["revisions"]["2"]["classifier_context"]["current"]["text"] == "обычный текст"


def test_semantic_snapshot_correlates_candidate_revision_to_gateway_ticket() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    store = AddressDetectorStore(sessionmaker(bind=engine))
    current = context(34)
    store.upsert_candidate(current, ("ты",))
    payload = {"messages": [], "classify_indexes": [0]}

    assert store.set_classifier_request(current, 1, payload)
    submitted = store.get_signal(34).context_snapshot["revisions"]["1"]
    assert submitted["classifier_context"] == payload
    assert submitted["semantic_request"] == {
        "message_id": 34,
        "revision": 1,
        "idempotency_key": "address_detector_fast:34:1",
    }

    result = AnalysisResult(
        Verdict.UNCERTAIN, reason="no_specific_addressee", classifier_version="qwen",
        classification="ambiguous", reason_code="no_specific_addressee", ticket_id=987,
        idempotency_key="address_detector_fast:34:1",
    )
    store.record_analysis(current, result, 120, revision=1)
    audit = store.get_signal(34).context_snapshot["revisions"]["1"]["semantic_analysis"]
    assert (audit["message_id"], audit["revision"], audit["ticket_id"], audit["idempotency_key"]) == (
        34, 1, 987, "address_detector_fast:34:1",
    )


def test_delete_invalidates_in_flight_revision() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    store = AddressDetectorStore(sessionmaker(bind=engine))
    current = context(31)
    store.upsert_candidate(current, ("ты",))
    assert store.mark_deleted(31)
    assert store.record_analysis(current, AnalysisResult(Verdict.LIKELY_VIOLATION, 0.9, "late", "rules"), 120, 1) is None
    assert store.get_signal(31).processing_status == "deleted"


def test_legacy_context_snapshot_without_revision_fields_remains_readable() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    store = AddressDetectorStore(sessions)
    current = context(32)
    store.upsert_candidate(current, ("ты",))
    db = sessions()
    try:
        signal = db.query(AddressDetectorSignal).filter_by(message_id="32").one()
        signal.context_snapshot = {"reply": None, "previous_messages": [], "jump_url": "old-link"}
        db.commit()
    finally:
        db.close()

    assert store.set_classifier_context(current, 1)
    assert store.set_shadow(32, 1, {"status": "completed", "classification": "no_violation"})
    snapshot = store.get_signal(32).context_snapshot
    assert snapshot["reply"] is None
    assert snapshot["jump_url"] == "old-link"
    assert snapshot["revisions"]["1"]["classifier_context"]["current"]["text"] == "ты где?"
    assert snapshot["revisions"]["1"]["shadow"]["status"] == "completed"


def test_clean_edit_stores_new_current_text_without_rewriting_old_revision() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    store = AddressDetectorStore(sessionmaker(bind=engine))
    original = context(33)
    store.upsert_candidate(original, ("ты",))
    store.set_classifier_context(original, 1)
    edited = AddressContext(**{
        **original.__dict__, "raw_content": "Обычный текст", "cleaned_content": "Обычный текст",
        "trigger_tokens": (),
    })

    assert store.mark_edit_clean(33, edited)

    signal = store.get_signal(33)
    assert signal.raw_content == "Обычный текст"
    assert signal.processing_status == "clean"
    assert signal.context_snapshot["current_revision"] == 2
    assert signal.context_snapshot["revisions"]["1"]["classifier_context"]["current"]["text"] == "ты где?"
