from types import SimpleNamespace

import pytest

from discord_bot.address_detector.filter import find_triggers
from discord_bot.address_detector.scope import ScopeConfig, in_scope


@pytest.mark.parametrize(
    "content",
    [
        "ты где?",
        "я тебе сказал",
        "это твоя проблема",
        "тебя спрашивают",
        "что ты делаешь?",
        "<@123> ты зачем это сделал?",
        "ТЫ",
        "Ты",
        "тЫ",
        "твоему решению это не поможет",
    ],
)
def test_find_triggers_detects_direct_pronouns(content: str) -> None:
    result = find_triggers(content)

    assert result.tokens


@pytest.mark.parametrize(
    "content",
    [
        "Что означает слово `ты`?",
        "> ты зачем это сделал",
        "пример обращения на «ты»",
        "[https://example.org/ты](https://example.org/ты)",
        "бутылка",
        "тыква",
        "```\nты зачем это сделал\n```",
    ],
)
def test_find_triggers_ignores_non_addressing_text(content: str) -> None:
    assert find_triggers(content).tokens == []


def test_find_triggers_covers_unaccented_possessive_form_and_multiline_quotes() -> None:
    assert find_triggers("это в твоем сообщении").tokens == ["твоем"]
    assert find_triggers(">>> цитата\nты внутри цитаты").tokens == []


def test_find_triggers_ignores_bare_discord_invite_urls() -> None:
    assert find_triggers("discord.gg/ты").tokens == []


def _message(**kwargs):
    author = kwargs.pop("author", SimpleNamespace(bot=False))
    channel = kwargs.pop("channel", SimpleNamespace(id=10, category_id=None))
    return SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=channel,
        author=author,
        webhook_id=kwargs.pop("webhook_id", None),
        type=kwargs.pop("type", None),
        **kwargs,
    )


def test_scope_requires_explicit_monitored_channel() -> None:
    config = ScopeConfig(monitored_channel_ids={10}, monitored_category_ids={20})

    assert in_scope(_message(), config)
    assert not in_scope(_message(channel=SimpleNamespace(id=11, category_id=20)), config)
    assert not in_scope(_message(channel=SimpleNamespace(id=11, category_id=None)), config)


def test_scope_honours_exclusions_and_ignores_automated_messages() -> None:
    config = ScopeConfig(monitored_channel_ids={10}, excluded_channel_ids={10})

    assert not in_scope(_message(), config)
    assert not in_scope(_message(author=SimpleNamespace(bot=True)), ScopeConfig(monitored_channel_ids={10}))
    assert not in_scope(_message(webhook_id=42), ScopeConfig(monitored_channel_ids={10}))


@pytest.mark.parametrize("message_type", ["default", "reply"])
def test_scope_allows_normal_and_reply_human_messages(message_type: str) -> None:
    message = _message(type=SimpleNamespace(name=message_type))

    assert in_scope(message, ScopeConfig(monitored_channel_ids={10}))


@pytest.mark.parametrize("message_type", ["system", "pinned_message", "application_command"])
def test_scope_rejects_special_message_types(message_type: str) -> None:
    message = _message(type=SimpleNamespace(name=message_type))

    assert not in_scope(message, ScopeConfig(monitored_channel_ids={10}))
