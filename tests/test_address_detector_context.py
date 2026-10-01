import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from discord_bot.address_detector.analyzer import RuleAnalyzer, Verdict
from discord_bot.address_detector.context import ContextCollector
from discord_bot.address_detector.filter import find_triggers
from discord_bot.address_detector.persistence import _compact_context
from discord_bot.address_detector.qwen_shadow import build_request_context


class FakeChannel:
    def __init__(self, previous):
        self.previous = previous

    async def fetch_message(self, message_id):
        return next(message for message in self.previous if message.id == message_id)

    def history(self, *, limit, before):
        async def rows():
            for message in self.previous[:limit]:
                yield message

        return rows()


def message(message_id, content, *, author_id=1, reply=None, mentions=(), previous=()):
    author = SimpleNamespace(id=author_id, display_name=f"User {author_id}", name=f"user{author_id}")
    return SimpleNamespace(
        id=message_id,
        guild=SimpleNamespace(id=100),
        channel=FakeChannel(list(previous)),
        author=author,
        content=content,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        jump_url=f"https://discord.com/channels/100/200/{message_id}",
        reference=SimpleNamespace(message_id=getattr(reply, "id", None), resolved=reply),
        mentions=[SimpleNamespace(id=user_id) for user_id in mentions],
    )


def test_context_collects_reply_mentions_and_previous_messages() -> None:
    replied = message(10, "Я уже объяснил Вам причину.", author_id=2)
    previous = [message(9, "До этого", author_id=3), message(8, "Ещё раньше", author_id=4)]
    current = message(11, "<@2> ты вообще читал?", author_id=1, reply=replied, mentions=(2,), previous=previous)

    context = asyncio.run(ContextCollector(history_limit=4).collect(current, find_triggers(current.content)))

    assert context.message_id == 11
    assert context.reply is not None and context.reply.author_id == 2
    assert context.mentions == ("2",)
    assert [row.message_id for row in context.previous_messages] == [9, 8]
    assert context.trigger_tokens == ("ты",)


def test_resolved_reply_survives_context_collection_and_serializes_target_once() -> None:
    target = message(100, "Что случилось?", author_id=2)
    current = message(101, "А ты чё?", author_id=1, reply=target, previous=(target,))

    context = asyncio.run(ContextCollector().collect(current, find_triggers(current.content)))
    payload = build_request_context(context)

    assert payload == {
        "messages": [
            {"index": 0, "speaker": "s1", "text": "Что случилось?", "reply_to": None, "mentions": []},
            {"index": 1, "speaker": "s2", "text": "А ты чё?", "reply_to": 0, "mentions": []},
        ],
        "classify_indexes": [1],
    }


def test_unresolved_reply_is_fetched_before_context_serialization() -> None:
    target = message(100, "Что случилось?", author_id=2)
    current = message(101, "А ты чё?", author_id=1, previous=(target,))
    current.reference = SimpleNamespace(message_id=target.id, resolved=None)

    context = asyncio.run(ContextCollector().collect(current, find_triggers(current.content)))
    payload = build_request_context(context)

    assert context.reply is not None and context.reply.message_id == 100
    assert payload["messages"][payload["classify_indexes"][0]]["reply_to"] == 0
    assert sum(row["text"] == "Что случилось?" for row in payload["messages"]) == 1


def test_failed_reply_fetch_does_not_invent_target_or_relation() -> None:
    class MissingChannel:
        async def fetch_message(self, _message_id):
            raise LookupError("not available")

        def history(self, *, limit, before):
            async def rows():
                if False:
                    yield None
            return rows()

    current = message(101, "А ты чё?", author_id=1)
    current.channel = MissingChannel()
    current.reference = SimpleNamespace(message_id=100, resolved=None)

    context = asyncio.run(ContextCollector().collect(current, find_triggers(current.content)))
    payload = build_request_context(context)

    assert context.reply is None
    assert payload == {
        "messages": [
            {"index": 0, "speaker": "s1", "text": "А ты чё?", "reply_to": None, "mentions": []},
        ],
        "classify_indexes": [0],
    }


def test_compact_classifier_context_uses_relative_speakers_and_reply_relation() -> None:
    replied = message(10, "Что ещё нужно?", author_id=2)
    previous = [message(9, "Перед этим", author_id=4)]
    current = message(11, "ты лог приложи", author_id=1, reply=replied, mentions=(2, 4), previous=previous)
    context = asyncio.run(ContextCollector().collect(current, find_triggers(current.content)))

    compact = _compact_context(context)

    assert compact["current"] == {
        "speaker": "s1", "text": "ты лог приложи", "reply_to": 1, "mentions": ["s2", "s3"],
    }
    assert compact["context"] == [
        {"index": 1, "speaker": "s2", "text": "Что ещё нужно?"},
        {"index": 2, "speaker": "s3", "text": "Перед этим"},
    ]
    assert "author_id" not in str(compact)
    assert "display_name" not in str(compact)


def test_compact_classifier_context_redacts_discord_identifiers_from_text() -> None:
    content = "<@123456789012345678> ты смотри <@&223456789012345678> в <#323456789012345678>"
    current = message(40, content, author_id=1, mentions=(123456789012345678,))
    context = asyncio.run(ContextCollector().collect(current, find_triggers(content)))

    compact = _compact_context(context)

    assert compact["current"]["text"] == "<@s2> ты смотри <@role> в <#channel>"
    assert compact["current"]["mentions"] == ["s2"]
    assert "123456789012345678" not in str(compact)


def test_rule_analyzer_prioritises_reply_and_mention() -> None:
    replied = message(10, "Когда сможете?", author_id=2)
    current = message(11, "<@2> тебе уже ответили", reply=replied, mentions=(2,))
    context = asyncio.run(ContextCollector().collect(current, find_triggers(current.content)))

    result = asyncio.run(RuleAnalyzer().analyze(context))

    assert result.verdict is Verdict.LIKELY_VIOLATION
    assert result.confidence is not None and result.confidence >= 0.75


def test_rule_analyzer_cleans_meta_discussion_and_quotes() -> None:
    examples = (
        "Он буквально написал: «ты идиот», после чего ушёл.",
        "В русском языке местоимение «ты» используется в примерах.",
        "Правило запрещает обращаться на ты?",
    )

    for content in examples:
        current = message(20, content)
        context = asyncio.run(ContextCollector().collect(current, find_triggers(content)))
        result = asyncio.run(RuleAnalyzer().analyze(context))
        assert result.verdict is Verdict.CLEAN


def test_rule_analyzer_keeps_isolated_address_uncertain() -> None:
    current = message(30, "а ты?")
    context = asyncio.run(ContextCollector().collect(current, find_triggers(current.content)))

    result = asyncio.run(RuleAnalyzer().analyze(context))

    assert result.verdict is Verdict.UNCERTAIN


def test_rule_analyzer_ignores_standalone_uh_ty_exclamation() -> None:
    current = message(31, "УХ ты!")
    context = asyncio.run(ContextCollector().collect(current, find_triggers(current.content)))

    result = asyncio.run(RuleAnalyzer().analyze(context))

    assert result.verdict is Verdict.CLEAN
    assert result.matched_signals == ("idiomatic_exclamation",)


def test_rule_analyzer_does_not_ignore_uh_ty_with_direct_address() -> None:
    current = message(32, "ух ты, ты опять это сделал")
    context = asyncio.run(ContextCollector().collect(current, find_triggers(current.content)))

    result = asyncio.run(RuleAnalyzer().analyze(context))

    assert result.verdict is Verdict.UNCERTAIN
