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
