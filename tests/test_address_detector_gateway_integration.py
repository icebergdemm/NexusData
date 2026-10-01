import asyncio
from copy import deepcopy
import time as time_module
import pytest

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import discord_bot.address_detector.qwen_shadow as qwen
import services_llm_gateway as gateway
from discord_bot.address_detector.analyzer import Verdict
from discord_bot.address_detector.config import DetectorConfig
from discord_bot.address_detector.qwen_shadow import FAST_CLIENT_ID, QwenSemanticAnalyzer
from models import AILlmRequest


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


def test_fast_qwen_request_uses_durable_gateway_policy_and_idempotency(monkeypatch):
    sessions = _install_gateway_db(monkeypatch)
    monkeypatch.setattr(qwen, "request_params", lambda _max_tokens=64: {
        "model": "test-qwen", "max_tokens": 64, "temperature": 0,
        "response_format": qwen.RESPONSE_FORMAT,
    })
    monkeypatch.setattr(qwen, "poll", lambda ticket_id: {
        "ticket_id": ticket_id, "status": "succeeded", "queue_wait_ms": 12, "backend_ms": 34,
        "result": {"choices": [{"message": {"content":
            '{"results":[{"index":0,"classification":"direct_familiar_address","reason_code":"informal_imperative"}]}'
        }}]},
    })
    qwen.configure_limits(FAST_CLIENT_ID, 8, 12)
    analyzer = QwenSemanticAnalyzer(DetectorConfig(llm_fast_quota_min=8, llm_fast_queue_limit=12))

    async def invoke():
        return await analyzer.classify_batch(_payload(), "address_detector_fast:321:4", client_id=FAST_CLIENT_ID)

    first, _ = asyncio.run(invoke())
    db = sessions()
    try:
        row = db.query(AILlmRequest).one()
        ticket_id = row.id
        assert row.client_id == "address_detector_fast"
        assert row.requested_by is None
        assert row.idempotency_key == "address_detector_fast:321:4"
        assert row.priority == 50
        assert row.backend == "dossier"
        assert row.request_json["timeout"] == 25
        assert row.request_json["payload"]["max_tokens"] == 64
    finally:
        db.close()
    assert first[0]["classification"] == "direct_familiar_address"
    assert gateway.LLM_CLIENTS[FAST_CLIENT_ID]["quota_min"] == 8
    assert gateway.LLM_CLIENTS[FAST_CLIENT_ID]["max_queue_depth"] == 12

    asyncio.run(invoke())
    db = sessions()
    try:
        assert db.query(func.count(AILlmRequest.id)).scalar() == 1
        assert db.query(AILlmRequest).one().id == ticket_id
    finally:
        db.close()


def test_legacy_shadow_true_does_not_enable_or_submit_qwen(monkeypatch):
    sessions = _install_gateway_db(monkeypatch)
    config = __import__("discord_bot.address_detector.config", fromlist=["load_detector_config"]).load_detector_config(
        object(), {"address_llm_shadow_enabled": "true"}
    )
    assert config.llm_enabled is False
    assert config.llm_background_enabled is False
    db = sessions()
    try:
        assert db.query(func.count(AILlmRequest.id)).scalar() == 0
    finally:
        db.close()


def test_fast_client_priority_is_higher_than_background_and_other_clients_unchanged(monkeypatch):
    monkeypatch.setattr(gateway, "LLM_CLIENTS", deepcopy(gateway.LLM_CLIENTS))
    dossier = deepcopy(gateway.LLM_CLIENTS["dossier"])
    dispatcher = deepcopy(gateway.LLM_CLIENTS["dispatcher"])
    monster = deepcopy(gateway.LLM_CLIENTS["monster_llm"])

    assert gateway.LLM_CLIENTS["address_detector_fast"]["priority"] < gateway.LLM_CLIENTS["address_detector_background"]["priority"]
    assert gateway.LLM_CLIENTS["address_detector_fast"]["priority"] < dossier["priority"]
    assert gateway.LLM_CLIENTS["dossier"] == dossier
    assert gateway.LLM_CLIENTS["dispatcher"] == dispatcher
    assert gateway.LLM_CLIENTS["monster_llm"] == monster


def test_waiting_fast_ticket_is_claimed_before_background_ticket(monkeypatch):
    _install_gateway_db(monkeypatch)
    gateway.submit(
        "address_detector_background", [{"role": "user", "content": "batch"}], {"model": "test", "max_tokens": 160},
        idempotency_key="background-ticket",
    )
    gateway.submit(
        "address_detector_fast", [{"role": "user", "content": "fast"}], {"model": "test", "max_tokens": 64},
        idempotency_key="fast-ticket",
    )

    claimed = gateway.claim_next()

    assert claimed.client_id == "address_detector_fast"
