import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import discord_bot.address_detector.qwen_classifier as qwen
import discord_bot.address_detector.runtime as runtime_module
import services_llm_gateway as gateway
from database import Base
from discord_bot.address_detector.config import DetectorConfig, load_detector_config
from discord_bot.address_detector.persistence import AddressDetectorStore
from discord_bot.address_detector.qwen_classifier import CLIENT_ID, QwenSemanticAnalyzer
from discord_bot.address_detector.runtime import AddressDetectorRuntime
from models import AILlmRequest, AddressDetectorBatchAudit, AddressDetectorSignal


def _install_gateway_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    AILlmRequest.__table__.create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(gateway, "SessionLocal", sessions)
    monkeypatch.setattr(gateway, "LLM_CLIENTS", deepcopy(gateway.LLM_CLIENTS))
    monkeypatch.setattr(gateway, "_wake", lambda: None)
    return sessions


def _payload():
    return {
        "messages": [{"index": 0, "speaker": "s1", "text": "скинь журнал", "reply_to": None, "mentions": []}],
        "classify_indexes": [0],
    }


def test_unified_detector_gateway_is_low_priority_and_has_one_active_ticket_with_internal_metadata(monkeypatch):
    sessions = _install_gateway_db(monkeypatch)
    untouched = {key: deepcopy(value) for key, value in gateway.LLM_CLIENTS.items() if key != CLIENT_ID}
    metadata = {"batch_id": "batch-a", "items": [{"index": 0, "message_id": 321, "revision": 4}]}
    ticket_id = gateway.submit(
        CLIENT_ID, [{"role": "user", "content": "batch"}],
        {"model": "test", "max_tokens": 256}, priority=210,
        idempotency_key="address_detector:batch-a", application_metadata=metadata,
    )

    db = sessions()
    try:
        row = db.query(AILlmRequest).one()
        assert row.request_json["app_metadata"] == metadata
        assert "app_metadata" not in row.request_json["payload"]
    finally:
        db.close()

    with pytest.raises(gateway.LLMGatewayError):
        gateway.submit(
            CLIENT_ID, [{"role": "user", "content": "next"}],
            {"model": "test", "max_tokens": 256}, idempotency_key="address_detector:batch-b",
        )
    assert ticket_id > 0
    assert gateway.LLM_CLIENTS[CLIENT_ID]["priority"] == 210
    assert {key: value for key, value in gateway.LLM_CLIENTS.items() if key != CLIENT_ID} == untouched


def test_qwen_request_uses_unified_gateway_policy_and_idempotency(monkeypatch):
    sessions = _install_gateway_db(monkeypatch)
    monkeypatch.setattr(qwen, "poll", lambda ticket_id: {
        "ticket_id": ticket_id, "status": "succeeded", "queue_wait_ms": 12, "backend_ms": 34,
        "result": {"choices": [{"message": {"content":
            '{"r":[{"i":0,"c":"D","r":"ii","e":"скинь"}]}'
        }}]},
    })
    qwen.configure_limits(CLIENT_ID, 8, 1)
    analyzer = QwenSemanticAnalyzer(DetectorConfig(llm_quota_min=8))

    async def invoke():
        return await analyzer.classify_batch(_payload(), "address_detector:batch-a")

    first, _ = asyncio.run(invoke())
    db = sessions()
    try:
        row = db.query(AILlmRequest).one()
        ticket_id = row.id
        assert row.client_id == CLIENT_ID
        assert row.requested_by is None
        assert row.idempotency_key == "address_detector:batch-a"
        assert row.priority == 210
        assert row.backend == "dossier"
        assert row.request_json["timeout"] == 25
        assert row.request_json["payload"]["max_tokens"] == qwen.max_tokens_for(1)
    finally:
        db.close()
    assert first[0]["classification"] == "direct_familiar_address"
    assert gateway.LLM_CLIENTS[CLIENT_ID]["quota_min"] == 8
    assert gateway.LLM_CLIENTS[CLIENT_ID]["max_queue_depth"] == 1

    asyncio.run(invoke())
    db = sessions()
    try:
        assert db.query(func.count(AILlmRequest.id)).scalar() == 1
        assert db.query(AILlmRequest).one().id == ticket_id
    finally:
        db.close()


def test_scoped_direct_message_reaches_one_gateway_ticket_and_applies_marker_signal(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'gateway-integration.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(gateway, "SessionLocal", sessions)
    monkeypatch.setattr(gateway, "LLM_CLIENTS", deepcopy(gateway.LLM_CLIENTS))
    monkeypatch.setattr(gateway, "_wake", lambda: None)
    monkeypatch.setattr(runtime_module, "active_legacy_detector_tickets", lambda: [])
    monkeypatch.setattr(qwen, "poll", lambda ticket_id: (
        gateway.finish(ticket_id, "succeeded", result={"choices": [{"message": {"content":
            '{"r":[{"i":0,"c":"D","r":"ip","e":"Ты"}]}'
        }}]}) and gateway.poll(ticket_id)
    ))

    async def history(**_kwargs):
        if False:
            yield None

    message = SimpleNamespace(
        id=321, content="Ты можешь посмотреть третий раздел?", guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=2, history=history),
        author=SimpleNamespace(id=9, display_name="User", bot=False), mentions=[],
        reference=None, created_at=datetime.now(timezone.utc),
        jump_url="https://discord.com/channels/1/2/321", type=None, webhook_id=None,
    )
    reactions, signals, runtime_status = [], [], {}
    store = AddressDetectorStore(sessions)

    class ReactionPort:
        async def add(self, current):
            reactions.append(("add", current.id))

        async def remove(self, current):
            reactions.append(("remove", current.id))

    async def resolve(_channel_id, _message_id):
        return message

    async def scenario():
        runtime = AddressDetectorRuntime(
            DetectorConfig(enabled=True, monitored_channel_ids={2}, llm_enabled=True),
            ReactionPort(), analyzer=QwenSemanticAnalyzer(DetectorConfig()),
            store=store, resolve_message=resolve,
            on_signal=lambda context, result, _record: signals.append(
                (context.message_id, result.classification)
            ),
        )
        await runtime.start()
        assert await runtime.handle_message(message)
        await asyncio.wait_for(runtime.join(), 5)
        runtime_status.update(runtime.status())
        await runtime.stop()

    asyncio.run(scenario())
    db = sessions()
    try:
        tickets = db.query(AILlmRequest).filter_by(client_id=CLIENT_ID).all()
        assert len(tickets) == 1, {
            "runtime": runtime_status, "pending": store.pending_work(),
            "states": store.detector_channel_states(),
            "signals": db.query(AddressDetectorSignal).all(),
            "audits": db.query(AddressDetectorBatchAudit).all(),
        }
        audit = db.query(AddressDetectorBatchAudit).first()
        assert audit is not None, tickets[0].request_json
        assert audit.outcomes, (tickets[0].result_ref, audit.outcomes)
        signal = db.query(AddressDetectorSignal).filter_by(message_id="321").one()
        assert tickets[0].status == "succeeded"
        assert signal.classification == "LIKELY_VIOLATION"
        assert audit.ticket_id == tickets[0].id
        assert audit.outcomes[0]["action"] == "moderator_signal"
        assert audit.outcomes[0]["model_classification"] == "direct_familiar_address"
        assert audit.outcomes[0]["evidence"] == "Ты"
        assert audit.outcomes[0]["evidence_validation"] == "verified"
        assert audit.outcomes[0]["action_eligible"] is True
        semantic = signal.context_snapshot["revisions"]["1"]["semantic_analysis"]
        assert semantic["evidence"] == "Ты"
        assert semantic["evidence_validation"] == "verified"
        assert semantic["action_eligible"] is True
    finally:
        db.close()
    assert reactions == [("add", 321)]
    assert signals == [(321, "direct_familiar_address")]


def test_legacy_background_mode_is_ignored_and_does_not_enable_qwen(monkeypatch):
    sessions = _install_gateway_db(monkeypatch)
    config = load_detector_config(object(), {"address_llm_background_enabled": "true"})
    assert config.llm_enabled is False
    assert not hasattr(config, "llm_background_enabled")
    db = sessions()
    try:
        assert db.query(func.count(AILlmRequest.id)).scalar() == 0
    finally:
        db.close()


def test_waiting_dossier_ticket_is_claimed_before_unified_detector(monkeypatch):
    _install_gateway_db(monkeypatch)
    gateway.submit(
        CLIENT_ID, [{"role": "user", "content": "batch"}], {"model": "test", "max_tokens": 224},
        idempotency_key="detector-ticket",
    )
    gateway.submit(
        "dossier", [{"role": "user", "content": "dossier"}], {"model": "test", "max_tokens": 64},
        idempotency_key="dossier-ticket",
    )

    assert gateway.claim_next().client_id == "dossier"
