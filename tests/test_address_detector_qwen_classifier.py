"""Qwen classifier protocol checks."""

import asyncio

import pytest

from discord_bot.address_detector.config import load_detector_config
from discord_bot.address_detector.qwen_classifier import (
    CLASSIFICATIONS,
    RESPONSE_FORMAT,
    parse_content,
    parse_results,
    verdict_for,
)
from discord_bot.address_detector.analyzer import Verdict


def test_classifier_helpers_use_the_compact_production_contract():
    assert parse_content is parse_results
    assert verdict_for("no_violation") is Verdict.CLEAN
    assert "direct_familiar_address" in CLASSIFICATIONS
    assert RESPONSE_FORMAT["json_schema"]["strict"] is True
    assert verdict_for("direct_familiar_address") is Verdict.LIKELY_VIOLATION


def test_legacy_shadow_flag_is_ignored():
    config = load_detector_config(object(), {"address_llm_shadow_enabled": "true"})
    assert config.llm_enabled is False
    assert config.llm_max_new_messages == 8


def test_ticket_metadata_persistence_failure_preserves_submitted_ticket(monkeypatch):
    import discord_bot.address_detector.qwen_classifier as qwen
    from discord_bot.address_detector.config import DetectorConfig
    from services_llm_gateway import LLMGatewayError

    submitted = []
    monkeypatch.setattr(qwen, "find_ticket", lambda *_args: None)
    monkeypatch.setattr(qwen, "submit", lambda *_args, **_kwargs: submitted.append(44) or 44)
    monkeypatch.setattr(qwen, "request_params", lambda _tokens: {})

    def fail_metadata(_ticket_id, _attempt):
        raise OSError("database unavailable")

    async def scenario():
        analyzer = qwen.QwenSemanticAnalyzer(DetectorConfig())
        await analyzer.classify_batch(
            {"messages": [], "classify_indexes": [0]}, "stable-key", on_ticket=fail_metadata,
        )

    with pytest.raises(LLMGatewayError, match="ticket metadata persistence failed"):
        asyncio.run(scenario())
    assert submitted == [44]


def test_cancel_persistence_failure_keeps_live_ticket_in_recovery(monkeypatch):
    import discord_bot.address_detector.qwen_classifier as qwen
    from discord_bot.address_detector.config import DetectorConfig
    from services_llm_gateway import LLMGatewayError

    submitted = []
    monkeypatch.setattr(qwen, "find_ticket", lambda *_args: None)
    monkeypatch.setattr(qwen, "submit", lambda *_args, **_kwargs: submitted.append(45) or 45)
    monkeypatch.setattr(qwen, "poll", lambda _ticket_id: {"status": "queued", "request_age_ms": 1000})

    def fail_cancel(_ticket_id):
        raise OSError("database unavailable")

    monkeypatch.setattr(qwen, "cancel", fail_cancel)
    monkeypatch.setattr(qwen, "request_params", lambda _tokens: {})

    async def scenario():
        analyzer = qwen.QwenSemanticAnalyzer(DetectorConfig(llm_max_request_age_seconds=1))
        await analyzer.classify_batch({"messages": [], "classify_indexes": [0]}, "stable-key")

    with pytest.raises(LLMGatewayError, match="cancellation failed"):
        asyncio.run(scenario())
    assert submitted == [45]
