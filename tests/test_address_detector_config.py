from types import SimpleNamespace

import pytest

from discord_bot.address_detector.config import DetectorConfig, load_detector_config


def settings(**overrides):
    values = {
        "moderation_address_detector_enabled": False,
        "moderation_signals_channel_id": "",
        "moderator_role_id": "",
        "redadmin_emoji_id": "",
        "monitored_channel_ids": "",
        "monitored_category_ids": "",
        "excluded_channel_ids": "",
        "address_context_messages": 4,
        "address_incident_window_seconds": 120,
        "address_analyzer_mode": "hybrid",
        "address_likely_threshold": 0.75,
        "address_clean_threshold": 0.80,
        "address_analysis_timeout_seconds": 15,
        "onnx_model_path": "",
        "onnx_tokenizer_path": "",
        "address_queue_size": 128,
        "address_worker_count": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_db_overrides_win_and_scope_is_explicit_channel_ids() -> None:
    config = load_detector_config(
        settings(
            moderation_address_detector_enabled=False,
            monitored_channel_ids="10",
        ),
        {
            "moderation_address_detector_enabled": "true",
            "monitored_channel_ids": "[11, 12]",
            "address_likely_threshold": "0.8",
        },
    )

    assert config.enabled is True
    assert config.monitored_channel_ids == {11, 12}
    assert config.likely_threshold == 0.8


@pytest.mark.parametrize("key,value", [("moderator_role_id", "abc"), ("address_likely_threshold", "1.4")])
def test_invalid_detector_values_are_rejected(key: str, value: str) -> None:
    with pytest.raises(ValueError):
        load_detector_config(settings(**{key: value}), {})


def test_detector_config_defaults_to_disabled_without_whitelist() -> None:
    config = DetectorConfig.from_settings(settings())

    assert config.enabled is False
    assert config.monitored_channel_ids == set()
    assert config.llm_shadow_enabled is False
    assert config.llm_quota_min == 6
    assert config.llm_queue_limit == 10


def test_empty_db_override_can_clear_env_whitelist() -> None:
    config = load_detector_config(
        settings(monitored_channel_ids="10", moderation_signals_channel_id="20"),
        {"monitored_channel_ids": "", "moderation_signals_channel_id": ""},
    )

    assert config.monitored_channel_ids == set()
    assert config.signals_channel_id == ""
