import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import time as time_module

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import discord_bot.address_detector.qwen_shadow as qwen
import services_ai_client as ai_client
import services_llm_gateway as gateway
from discord_bot.address_detector.config import DetectorConfig
from discord_bot.address_detector.qwen_shadow import FAST_CLIENT_ID, QwenSemanticAnalyzer
from models import AILlmRequest


def _db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    AILlmRequest.__table__.create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(gateway, "SessionLocal", sessions)
    monkeypatch.setattr(gateway, "LLM_CLIENTS", deepcopy(gateway.LLM_CLIENTS))
    monkeypatch.setattr(gateway, "_wake", lambda: None)
    monkeypatch.setattr(qwen, "request_params", lambda _max_tokens=64: {
        "model": "test-qwen", "max_tokens": 64, "temperature": 0,
        "response_format": qwen.RESPONSE_FORMAT,
    })
    return sessions


def _payload():
    return {
        "messages": [{"index": 0, "speaker": "s1", "text": "скинь журнал", "reply_to": None, "mentions": []}],
        "classify_indexes": [0],
    }


def _result():
    return {"choices": [{"message": {"content":
        '{"results":[{"index":0,"classification":"direct_familiar_address","reason_code":"informal_imperative"}]}'
    }}]}


def test_submit_claim_finish_and_poll_share_utc_timeline(monkeypatch):
    sessions = _db(monkeypatch)
    current = [datetime(2026, 10, 2, 10, 0, 0)]
    monkeypatch.setattr(gateway, "_utc_now", lambda: current[0])

    ticket_id = gateway.submit(FAST_CLIENT_ID, [{"role": "user", "content": "test"}], {"max_tokens": 64},
                               idempotency_key="utc-regression")
    current[0] += timedelta(seconds=20)
    gateway.claim_next()
    current[0] += timedelta(seconds=3)
    gateway.finish(ticket_id, "succeeded", _result())
    ticket = gateway.poll(ticket_id)

    db = sessions()
    try:
        row = db.query(AILlmRequest).filter_by(id=ticket_id).one()
        assert row.created_at == datetime(2026, 10, 2, 10, 0, 0)
        assert row.started_at == datetime(2026, 10, 2, 10, 0, 20)
        assert row.finished_at == datetime(2026, 10, 2, 10, 0, 23)
    finally:
        db.close()
    assert (ticket["queue_wait_ms"], ticket["backend_ms"], ticket["request_age_ms"]) == (20_000, 3_000, 23_000)


def test_result_visible_at_30_seconds_succeeds_without_retry_ticket(monkeypatch):
    sessions = _db(monkeypatch)
    current = [datetime(2026, 10, 2, 10, 0, 0)]
    clock = [0.0]
    monkeypatch.setattr(gateway, "_utc_now", lambda: current[0])
    monkeypatch.setattr(time_module, "monotonic", lambda: clock[0])

    original_poll = gateway.poll
    def delayed_poll(ticket_id):
        db = sessions()
        try:
            row = db.query(AILlmRequest).filter_by(id=ticket_id).one()
            row.status = "succeeded"
            row.started_at = row.created_at + timedelta(seconds=20)
            row.finished_at = row.started_at + timedelta(seconds=3)
            row.result_ref = _result()
            db.commit()
        finally:
            db.close()
        current[0] = datetime(2026, 10, 2, 10, 0, 30)
        return original_poll(ticket_id)

    monkeypatch.setattr(qwen, "poll", delayed_poll)
    monkeypatch.setattr(time_module, "sleep", lambda _seconds: None)
    analyzer = QwenSemanticAnalyzer(DetectorConfig())
    results, timing = asyncio.run(analyzer.classify_batch(
        _payload(), "address_detector_fast:42:1", client_id=FAST_CLIENT_ID,
    ))

    assert results[0]["classification"] == "direct_familiar_address"
    assert timing["queue_wait_ms"] == 20_000
    assert timing["backend_ms"] == 3_000
    db = sessions()
    try:
        rows = db.query(AILlmRequest).all()
        assert len(rows) == 1
        assert rows[0].idempotency_key == "address_detector_fast:42:1"
    finally:
        db.close()


@pytest.mark.parametrize(("ticket", "expected_error"), [
    ({"status": "queued", "queue_wait_ms": 60_001, "request_age_ms": 60_001}, "queue_wait_timeout"),
    ({"status": "running", "queue_wait_ms": 20_000, "backend_ms": 3_000,
      "request_age_ms": 120_001}, "request_age_timeout"),
])
def test_queue_wait_and_request_age_are_independent(monkeypatch, ticket, expected_error):
    submitted = []
    cancelled = []
    monkeypatch.setattr(qwen, "find_ticket", lambda *_: None)
    monkeypatch.setattr(qwen, "submit", lambda *_args, **kwargs: submitted.append(kwargs["idempotency_key"]) or 41)
    monkeypatch.setattr(qwen, "poll", lambda _ticket_id: {"ticket_id": 41, **ticket})
    monkeypatch.setattr(qwen, "cancel", lambda ticket_id: cancelled.append(ticket_id) or True)

    with pytest.raises(TimeoutError, match=expected_error):
        qwen._gateway_result(FAST_CLIENT_ID, _payload(), "address_detector_fast:42:1", DetectorConfig())

    assert submitted == ["address_detector_fast:42:1"]
    assert cancelled == [41]


def test_retry_occurs_only_after_terminal_backend_failure(monkeypatch):
    submitted = []
    responses = iter([
        {"ticket_id": 41, "status": "failed", "error": "ReadTimeout", "queue_wait_ms": 1,
         "backend_ms": 25_000, "request_age_ms": 25_001},
        {"ticket_id": 42, "status": "succeeded", "queue_wait_ms": 1, "backend_ms": 20,
         "request_age_ms": 25_100, "result": _result()},
    ])
    monkeypatch.setattr(qwen, "find_ticket", lambda _client, _key: None)
    monkeypatch.setattr(qwen, "submit", lambda *_args, **kwargs: submitted.append(kwargs["idempotency_key"]) or len(submitted) + 40)
    monkeypatch.setattr(qwen, "poll", lambda _ticket_id: next(responses))

    results, _ = qwen._gateway_result(FAST_CLIENT_ID, _payload(), "address_detector_fast:42:1", DetectorConfig())

    assert results[0]["classification"] == "direct_familiar_address"
    assert submitted == ["address_detector_fast:42:1", "address_detector_fast:42:1:retry1"]


def test_backend_timeout_is_applied_at_http_transport(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_client, "post_chat_completions",
                        lambda payload, timeout: calls.append((payload, timeout)) or ({"ok": True}, 3))

    result, elapsed = gateway.post_backend("dossier", {"messages": []}, timeout=25)

    assert result == {"ok": True}
    assert elapsed == 3
    assert calls == [({"messages": []}, 25)]
