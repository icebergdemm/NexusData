import pytest
from datetime import datetime, timedelta

import services_llm_gateway as gateway
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


def test_database_quota_uses_naive_local_database_time(monkeypatch):
    local_now = datetime(2026, 9, 18, 12, 0, 0)

    class FakeDateTime:
        @classmethod
        def now(cls):
            return local_now

        @classmethod
        def utcnow(cls):
            return local_now - timedelta(hours=2)

    monkeypatch.setattr(gateway, "datetime", FakeDateTime)

    cutoff = gateway._database_local_now() - timedelta(minutes=1)

    assert cutoff == datetime(2026, 9, 18, 11, 59, 0)
    assert datetime(2026, 9, 18, 11, 58, 59) < cutoff
