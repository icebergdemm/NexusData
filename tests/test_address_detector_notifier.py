import asyncio
from types import SimpleNamespace

from discord_bot.address_detector.analyzer import AnalysisResult, Verdict
from discord_bot.address_detector.config import DetectorConfig
from discord_bot.address_detector.context import AddressContext
from discord_bot.address_detector.notifier import DiscordNotifier
from discord_bot.address_detector.persistence import AnalysisRecord


class Target:
    def __init__(self, role):
        self.guild = SimpleNamespace(get_role=lambda _role_id: role)
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=500)


class Bot:
    def __init__(self, target):
        self.target = target
        self.intents = SimpleNamespace(message_content=True)
        self.user = SimpleNamespace(id=999)

    def get_channel(self, _channel_id):
        return self.target


class Store:
    def get_incident(self, _incident_id):
        return {
            "id": 1, "admin_message_id": None, "message_count": 1,
            "signals": [{"raw_content": "@everyone ты где?", "jump_url": "https://discord.test/1"}],
        }

    def set_admin_message_id(self, _message_id, _admin_message_id):
        pass


def test_signal_allows_only_moderator_role_ping() -> None:
    role = SimpleNamespace(mention="<@&42>")
    target = Target(role)
    notifier = DiscordNotifier(
        Bot(target),
        DetectorConfig(signals_channel_id="10", moderator_role_id="42"),
        Store(),
    )
    context = AddressContext(
        message_id=1, guild_id=1, channel_id=2, author_id=3, author_display_name="@user",
        timestamp=None, raw_content="@everyone ты где?", cleaned_content="ты где?",
        trigger_tokens=("ты",), is_reply=False, reply=None, reply_author_id=None,
        mentions=(), previous_messages=(), jump_url="https://discord.test/1",
    )

    asyncio.run(notifier.send_signal(
        context,
        AnalysisResult(Verdict.UNCERTAIN, 0.5, "unclear", "rules"),
        AnalysisRecord(1, True, 1),
    ))

    sent = target.sent[0]
    assert sent["content"] == "<@&42>"
    assert sent["allowed_mentions"].roles == [role]
    assert sent["allowed_mentions"].users is False
    assert sent["allowed_mentions"].everyone is False
    assert "@\u200buser" in sent["embed"].fields[0].value

