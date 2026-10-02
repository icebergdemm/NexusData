import pytest
from copy import deepcopy
from datetime import datetime, timedelta

import services_llm_gateway as gateway
import services_ai_client as ai_client
from services_llm_gateway import LLMGatewayError, _validate


def test_validate_preserves_tool_call_payload_and_tool_messages():
    messages = [
        {"role": "user", "content": "Look up 42."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_42",
                    "type": "function",
                    "function": {"name": "lookup_test_value", "arguments": '{"id":42}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_42", "content": "TEST-42"},
    ]
    tools = [{
        "type": "function",
        "function": {
            "name": "lookup_test_value",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
                "additionalProperties": False,
            },
        },
    }]
    params = {
        "model": "qwen2.5-3b-instruct",
        "tools": tools,
        "tool_choice": "required",
        "parallel_tool_calls": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "final",
                "schema": {"type": "object", "properties": {"value": {"type": "string"}}},
            },
        },
    }

    _client, payload = _validate("future", messages, params)

    assert payload["messages"] == messages
    assert payload["tools"] == tools
    assert payload["tool_choice"] == "required"
    assert payload["parallel_tool_calls"] is False
    assert payload["response_format"]["type"] == "json_schema"


def test_validate_still_rejects_unknown_transport_parameters():
    with pytest.raises(LLMGatewayError, match="Unsupported LLM parameter"):
        _validate("future", [{"role": "user", "content": "test"}], {"not_a_field": True})


def test_services_ai_client_passes_schema_response_format_unchanged(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

    class Client:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return Response()

    payload = {
        "messages": [{"role": "system", "content": "fixed"}],
        "max_tokens": 64,
        "response_format": {"type": "json_schema", "json_schema": {"name": "strict", "strict": True}},
    }
    monkeypatch.setattr(ai_client, "get_settings", lambda: type("Settings", (), {
        "ai_dossier_url": "https://llm.invalid/v1/chat/completions",
        "ai_dossier_api_key": "test-key",
    })())
    monkeypatch.setattr(ai_client, "_get_ai_client", lambda *_: Client())

    ai_client.post_chat_completions(payload, timeout=5)

    assert captured["json"] is payload
    assert captured["json"]["response_format"] == payload["response_format"]


def test_address_detector_fast_limits_are_isolated_to_its_gateway_client(monkeypatch):
    monkeypatch.setattr(gateway, "LLM_CLIENTS", deepcopy(gateway.LLM_CLIENTS))
    dossier_before = deepcopy(gateway.LLM_CLIENTS["dossier"])
    dispatcher_before = deepcopy(gateway.LLM_CLIENTS["dispatcher"])
    monster_before = deepcopy(gateway.LLM_CLIENTS["monster_llm"])

    gateway.configure_client("address_detector_fast", quota_min=8, max_queue_depth=12)

    assert gateway.LLM_CLIENTS["address_detector_fast"]["quota_min"] == 8
    assert gateway.LLM_CLIENTS["address_detector_fast"]["max_queue_depth"] == 12
    assert gateway.LLM_CLIENTS["address_detector_background"]["quota_min"] == 2
    assert gateway.LLM_CLIENTS["dossier"] == dossier_before
    assert gateway.LLM_CLIENTS["dispatcher"] == dispatcher_before
    assert gateway.LLM_CLIENTS["monster_llm"] == monster_before


def test_gateway_clock_is_utc_naive_for_database_datetime_columns(monkeypatch):
    from datetime import timezone

    fixed_utc = datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)
    class FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return fixed_utc

    monkeypatch.setattr(gateway, "datetime", FakeDateTime)

    now = gateway._utc_now()

    assert now == datetime(2026, 9, 18, 10, 0, 0)
    assert now.tzinfo is None
