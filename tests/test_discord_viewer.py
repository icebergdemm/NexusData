import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import discord

from discord_bot import bot as bot_module


def _channel(channel_type, *, channel_id, position, can_view=True, can_send=True):
    channel_class = {
        "text": discord.TextChannel,
        "voice": discord.VoiceChannel,
        "stage": discord.StageChannel,
    }[channel_type]
    channel = Mock(spec=channel_class)
    channel.id = channel_id
    channel.name = f"{channel_type}-{channel_id}"
    channel.position = position
    channel.category_id = None
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=can_view,
        send_messages=can_send,
    )
    return channel


def test_viewer_tree_includes_visible_text_voice_and_stage_channels(monkeypatch):
    text = _channel("text", channel_id=10, position=2)
    voice = _channel("voice", channel_id=20, position=1)
    stage = _channel("stage", channel_id=30, position=3)
    hidden = _channel("voice", channel_id=40, position=4, can_view=False)
    guild = SimpleNamespace(
        me=object(),
        channels=[text, voice, stage, hidden],
        text_channels=[text],
        categories=[],
        id=123,
        name="Test guild",
        icon=None,
    )
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)

    result = asyncio.run(bot_module.viewer_get_tree())

    assert [(channel["id"], channel["type"], channel["can_send"]) for channel in result["channels"]] == [
        ("20", "voice", True),
        ("10", "text", True),
        ("30", "stage", True),
    ]


def test_viewer_message_operations_support_vocal_channels(monkeypatch):
    voice = Mock(spec=discord.VoiceChannel)
    message = SimpleNamespace(
        id=501,
        content="voice chat message",
        author=SimpleNamespace(
            id=601,
            name="User",
            display_name="User",
            bot=False,
            display_avatar=SimpleNamespace(url="https://cdn.example/avatar.png"),
        ),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        edited_at=None,
        attachments=[],
        embeds=[],
        reference=None,
    )

    async def history(*_args, **_kwargs):
        yield message

    async def send(**_kwargs):
        return message

    voice.history.side_effect = history
    voice.send.side_effect = send
    monkeypatch.setattr(bot_module.bot, "get_channel", lambda _channel_id: voice)

    messages = asyncio.run(bot_module.viewer_get_messages(20, None, 50))
    sent = asyncio.run(bot_module.viewer_send_message(20, "hello", "hello.txt", b"file"))
    exported = asyncio.run(bot_module.viewer_export_messages(20, 50))

    assert messages[0]["id"] == "501"
    assert sent["content"] == "voice chat message"
    assert exported[0]["author"]["display_name"] == "User"
    voice.send.assert_awaited_once()
