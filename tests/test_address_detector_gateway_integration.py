import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import discord_bot.address_detector.runtime as runtime_module
import services_llm_gateway as gateway
from discord_bot.address_detector.analyzer import AnalysisResult, Verdict
from discord_bot.address_detector.config import DetectorConfig
from discord_bot.address_detector.context import AddressContext
from discord_bot.address_detector.runtime import AddressDetectorRuntime
from discord_bot.address_detector.service import DetectorJob
from models import AILlmRequest


def _context(message_id=321):
    return AddressContext(
        message_id=message_id, guild_id=1, channel_id=7, author_id=3,
        author_display_name="speaker", timestamp=datetime.now(timezone.utc),
        raw_content="ты можешь скинуть лог?", cleaned_content="ты можешь скинуть лог?",
        trigger_tokens=("ты",), is_reply=False, reply=None, reply_author_id=None,
        mentions=(), previous_messages=(), jump_url=None,
    )


class Store:
    def __init__(self):
        self.shadow = {}
        self.results = []

    def pending_shadow(self):
        return []

    def set_shadow(self, message_id, revision, value):
        self.shadow[(message_id, revision)] = dict(value)
        return True

    def is_current(self, message_id, revision):
        return True

    def record_analysis(self, context, result, _window, _revision):
        self.results.append(result)
        return SimpleNamespace(incident_id=1, should_ping_moderators=True, message_count=1)


class Reactions:
    async def add(self, _message):
        pass

    async def remove(self, _message):
        pass


def _config(shadow=True, **overrides):
    return DetectorConfig(
        enabled=True, monitored_channel_ids={7}, llm_shadow_enabled=shadow, **overrides,
    )


def _install_gateway_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AILlmRequest.__table__.create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(gateway, "SessionLocal", sessions)
    monkeypatch.setattr(gateway, "LLM_CLIENTS", deepcopy(gateway.LLM_CLIENTS))
    monkeypatch.setattr(gateway, "_wake", lambda: None)
    return sessions


def test_shadow_creates_idempotent_gateway_request_with_system_requester(monkeypatch):
    sessions = _install_gateway_db(monkeypatch)
    params = {
        "model": "test-qwen",
        "max_tokens": 64,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    monkeypatch.setattr(runtime_module, "request_params", lambda: params)
    monkeypatch.setattr(runtime_module, "poll", lambda _ticket_id: {
        "status": "succeeded", "queue_wait_ms": 12, "backend_ms": 34,
        "result": {"choices": [{"message": {"content":
            '{"classification":"direct_tutoiement","reason_code":"direct_address"}'}}]},
    })

    async def scenario():
        runtime = AddressDetectorRuntime(
            _config(llm_quota_min=8, llm_queue_limit=12), Reactions(), store=Store(),
        )
        await runtime.start()
        context = {
            "current": {"speaker": "s1", "text": "ты можешь скинуть лог?", "reply_to": None, "mentions": []},
            "context": [],
        }
        await runtime._run_shadow(321, 4, context)
        await runtime._resume_shadow({
            "message_id": 321, "revision": 4, "ticket_id": 1,
            "submitted_counted": True,
            "submitted_at": datetime.now(timezone.utc).isoformat(), "context": context,
        }, allow_submit=True)
        await runtime.stop()

    asyncio.run(scenario())

    db = sessions()
    try:
        row = db.query(AILlmRequest).one()
        messages = row.request_json["payload"]["messages"]
        assert row.client_id == "address_detector"
        assert row.requested_by is None
        assert row.idempotency_key == "address_detector:321:4"
        assert row.priority == 200
        assert row.backend == "dossier"
        assert row.request_json["timeout"] == 25
        assert row.request_json["payload"]["max_tokens"] == 64
        ticket_id = row.id
    finally:
        db.close()

    client = gateway.LLM_CLIENTS["address_detector"]
    assert (client["quota_min"], client["max_queue_depth"]) == (8, 12)

    repeated_id = gateway.submit(
        "address_detector",
        messages,
        params,
        idempotency_key="address_detector:321:4",
        timeout=25,
    )
    assert repeated_id == ticket_id

    db = sessions()
    try:
        assert db.query(func.count(AILlmRequest.id)).scalar() == 1
    finally:
        db.close()


def test_shadow_disabled_does_not_create_gateway_request(monkeypatch):
    sessions = _install_gateway_db(monkeypatch)
    runtime = AddressDetectorRuntime(_config(shadow=False), Reactions(), store=Store())

    runtime._schedule_shadow(DetectorJob(message=SimpleNamespace(id=321)), _context())

    assert runtime.context_analyzer.on_context is None
    assert runtime._shadow_tasks == set()
    db = sessions()
    try:
        assert db.query(func.count(AILlmRequest.id)).scalar() == 0
    finally:
        db.close()


def test_gateway_submit_failure_cannot_replace_production_verdict_or_signal(monkeypatch):
    _install_gateway_db(monkeypatch)
    monkeypatch.setattr(runtime_module, "request_params", lambda: {
        "model": "test-qwen", "max_tokens": 64, "temperature": 0,
        "response_format": {"type": "json_object"},
    })
    monkeypatch.setattr(runtime_module, "submit", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        gateway.LLMGatewayError("gateway unavailable", 503)
    ))
    signals = []
    store = Store()
    runtime = AddressDetectorRuntime(
        _config(), Reactions(), store=store,
        on_signal=lambda _context, result, _record: signals.append(result.verdict),
    )
    context = _context()
    job = DetectorJob(message=SimpleNamespace(id=context.message_id), revision=1, context=context)

    async def scenario():
        runtime._schedule_shadow(job, context)
        production_result = AnalysisResult(
            Verdict.UNCERTAIN, classifier_version="onnx", reason="production result",
        )
        await runtime._classified(job, production_result)
        await asyncio.gather(*tuple(runtime._shadow_tasks))
        return production_result

    result = asyncio.run(scenario())

    assert store.results == [result]
    assert signals == [Verdict.UNCERTAIN]
    assert runtime.status()["counters"]["qwen_shadow_failed"] == 1
