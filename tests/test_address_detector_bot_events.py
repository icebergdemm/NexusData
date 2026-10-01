import asyncio
from types import SimpleNamespace

from discord_bot import bot as bot_module


def test_raw_edit_passes_updated_message_to_detector(monkeypatch) -> None:
    received = []

    class Runtime:
        async def handle_edit(self, message):
            received.append(message)

    monkeypatch.setattr(bot_module, "_address_detector_runtime", Runtime())
    monkeypatch.setattr(bot_module, "GUILD_ID", 1)
    monkeypatch.setattr(bot_module.viewer_events, "has_subscribers", lambda _channel_id: False)
    updated = SimpleNamespace(id=3, content="обычный текст")
    payload = SimpleNamespace(
        guild_id=1,
        channel_id=2,
        message_id=3,
        message=updated,
        cached_message=SimpleNamespace(id=3, content="ты где?"),
    )

    asyncio.run(bot_module.on_raw_message_edit(payload))

    assert received == [updated]


def test_runtime_status_exposes_shadow_telemetry_to_admin_api(monkeypatch) -> None:
    runtime_status = {
        "counters": {"qwen_shadow_completed": 3},
        "shadow_enabled": False,
        "shadow_in_flight": 1,
        "qwen_requests_per_minute": 2,
        "qwen_shadow_queue_wait_ms": {"count": 3, "avg": 10, "p95": 20},
        "qwen_shadow_backend_ms": {"count": 3, "avg": 100, "p95": 150},
        "last_shadow_error": None,
        "events": [],
    }
    runtime = SimpleNamespace(
        status=lambda: runtime_status,
        service=SimpleNamespace(queue=SimpleNamespace(qsize=lambda: 4, maxsize=128)),
    )
    monkeypatch.setattr(bot_module, "_address_detector_runtime", runtime)
    monkeypatch.setattr(bot_module, "_address_detector_startup_events", [])

    status = bot_module.get_address_detector_status()

    assert status["shadow_enabled"] is False
    assert status["shadow_in_flight"] == 1
    assert status["qwen_shadow_queue_wait_ms"]["p95"] == 20
    assert status["counters"]["qwen_shadow_completed"] == 3
