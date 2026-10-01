"""Compatibility checks for the retired shadow naming surface."""

from discord_bot.address_detector.config import load_detector_config
from discord_bot.address_detector.qwen_shadow import (
    CLASSIFICATIONS,
    RESPONSE_FORMAT,
    parse_content,
    parse_results,
    shadow_verdict,
    verdict_for,
)
from discord_bot.address_detector.analyzer import Verdict


def test_legacy_shadow_helpers_alias_the_production_semantic_contract():
    assert parse_content is parse_results
    assert shadow_verdict is verdict_for
    assert "direct_familiar_address" in CLASSIFICATIONS
    assert RESPONSE_FORMAT["json_schema"]["strict"] is True
    assert verdict_for("direct_familiar_address") is Verdict.LIKELY_VIOLATION


def test_legacy_shadow_flag_does_not_enable_phase_a():
    config = load_detector_config(object(), {"address_llm_shadow_enabled": "true"})
    assert config.llm_shadow_enabled is True
    assert config.llm_enabled is False
    assert config.llm_background_enabled is False
