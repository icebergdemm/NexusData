from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from discord import PermissionOverwrite

from discord_bot import bot as discord_bot
from discord_bot import data as bot_data


def test_get_active_mutes_does_not_delete_expired_records_needed_for_cleanup():
    expired = datetime.utcnow() - timedelta(minutes=1)
    stored = {"42": {"expires_at": expired.isoformat(), "reason": "test"}}

    with patch.object(bot_data, "get_json_bot_config", return_value=stored), \
         patch.object(bot_data, "set_json_bot_config") as save:
        assert bot_data.get_active_mutes() == {}

    save.assert_not_called()


def test_remove_mute_removes_expired_record():
    expired = datetime.utcnow() - timedelta(minutes=1)
    stored = {"42": {"expires_at": expired.isoformat(), "reason": "test"}}

    with patch.object(bot_data, "get_json_bot_config", return_value=stored), \
         patch.object(bot_data, "set_json_bot_config") as save:
        assert bot_data.remove_mute("42") is True

    save.assert_called_once_with("discord_mutes", {})


async def _restore_voice(member):
    await discord_bot._restore_voice_on_unmute(member, "test")


def test_restore_voice_unmutes_member_even_when_not_in_voice():
    class FakeVoiceChannel:
        def __init__(self):
            self.overwrite = PermissionOverwrite(view_channel=True, connect=False, speak=False)
            self.set_permissions = AsyncMock()

        def overwrites_for(self, member):
            return self.overwrite

    channel = FakeVoiceChannel()
    member = SimpleNamespace(
        guild=SimpleNamespace(channels=[channel]),
        voice=None,
        edit=AsyncMock(),
    )

    with patch.object(discord_bot, "VocalGuildChannel", FakeVoiceChannel):
        import asyncio
        asyncio.run(_restore_voice(member))

    member.edit.assert_awaited_once()
    assert member.edit.await_args.kwargs["mute"] is False
    channel.set_permissions.assert_awaited_once()
    restored = channel.set_permissions.await_args.kwargs["overwrite"]
    assert restored.view_channel is True
    assert restored.connect is None
    assert restored.speak is None


def test_refresh_ticket_status_attaches_technical_button_to_status_message():
    message = SimpleNamespace(id=987, edit=AsyncMock())
    channel = SimpleNamespace(
        id=1507830881832931482,
        fetch_message=AsyncMock(return_value=message),
        send=AsyncMock(return_value=message),
    )

    with patch.object(discord_bot.bot_data, "get_bot_config", return_value={}), \
         patch.object(discord_bot.bot_data, "get_open_tickets", return_value=[]), \
         patch.object(discord_bot.bot, "get_channel", return_value=channel), \
         patch.object(discord_bot, "_ticket_status_msg_id", None):
        import asyncio
        asyncio.run(discord_bot.refresh_ticket_status())

    channel.send.assert_awaited_once()
    view = channel.send.await_args.kwargs["view"]
    assert isinstance(view, discord_bot.TechnicalTicketView)
    assert view.children[0].custom_id == "ticket_technical"


def test_technical_ticket_access_uses_the_three_configured_roles():
    allowed = {"1535306271568306216", "1536021581258821642", "1543720354520899817"}
    user = SimpleNamespace(roles=[SimpleNamespace(id=1536021581258821642)])
    denied = SimpleNamespace(roles=[SimpleNamespace(id=1)])

    assert discord_bot._user_has_technical_ticket_permission(user) is True
    assert discord_bot._user_has_technical_ticket_permission(denied) is False
    assert allowed == discord_bot.TECHNICAL_TICKET_ADMIN_ROLE_IDS
