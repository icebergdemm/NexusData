import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import discord_bot.address_detector.runtime as runtime_module
from discord_bot.address_detector.analyzer import AnalysisResult, Verdict
from discord_bot.address_detector.config import DetectorConfig
from discord_bot.address_detector.runtime import AddressDetectorRuntime


class Reactions:
    def __init__(self):
        self.events = []

    async def add(self, message):
        self.events.append(("add", message.id))

    async def remove(self, message):
        self.events.append(("remove", message.id))


class Store:
    def __init__(self):
        self.upserts = 0
        self.analyses = []
        self.clean_edits = []
        self.revisions = {}
        self.statuses = {}

    def upsert_candidate(self, context, tokens):
        self.upserts += 1
        if context.message_id in self.revisions:
            return False
        self.revisions[context.message_id] = 1
        self.statuses[context.message_id] = "queued"
        return True

    def update_candidate(self, context, tokens):
        self.upserts += 1
        revision = self.revisions.get(context.message_id, 0) + 1
        self.revisions[context.message_id] = revision
        self.statuses[context.message_id] = "queued"
        return revision

    def set_classifier_context(self, context, revision):
        return self.is_current(context.message_id, revision)

    def get_signal(self, message_id):
        return SimpleNamespace(message_id=message_id) if message_id in self.revisions else None

    def is_current(self, message_id, revision):
        return self.revisions.get(message_id) == revision and self.statuses.get(message_id) != "deleted"

    def record_analysis(self, context, result, window, revision=1):
        if not self.is_current(context.message_id, revision):
            return None
        self.analyses.append((context.message_id, result.verdict))
        self.statuses[context.message_id] = "classified"
        return SimpleNamespace(incident_id=1, should_ping_moderators=True, message_count=1)

    def mark_terminal(self, message_id, revision, status, note):
        if self.is_current(message_id, revision):
            self.statuses[message_id] = status
            return True
        return False

    def mark_edit_clean(self, message_id, context=None):
        self.clean_edits.append(message_id)
        self.revisions[message_id] = self.revisions.get(message_id, 0) + 1
        self.statuses[message_id] = "clean"
        return True

    def mark_deleted(self, message_id):
        self.revisions[message_id] = self.revisions.get(message_id, 0) + 1
        self.statuses[message_id] = "deleted"
        return True

    def pending_shadow(self):
        return []


class Analyzer:
    async def analyze(self, context):
        return AnalysisResult(Verdict.UNCERTAIN, 0.5, "test", "test")


class CleanAnalyzer:
    async def analyze(self, context):
        return AnalysisResult(Verdict.CLEAN, 0.99, "quoted example", "test")


class LikelyAnalyzer:
    async def analyze(self, context):
        return AnalysisResult(Verdict.LIKELY_VIOLATION, 0.99, "direct address", "test")


def msg(message_id, content):
    async def rows():
        if False:
            yield None

    return SimpleNamespace(
        id=message_id,
        content=content,
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=2, history=lambda **_: rows()),
        author=SimpleNamespace(id=3, display_name="User", bot=False),
        mentions=[],
        created_at=datetime.now(timezone.utc),
        jump_url=f"https://discord.com/channels/1/2/{message_id}",
        type=None,
        webhook_id=None,
    )


def config():
    return DetectorConfig(enabled=True, monitored_channel_ids={2}, llm_enabled=True)


def _fake_qwen_response(monkeypatch, classification, reason_code):
    import discord_bot.address_detector.qwen_shadow as qwen

    captured = {}
    monkeypatch.setattr(runtime_module, "configure_limits", lambda *args: None)
    monkeypatch.setattr(qwen, "find_ticket", lambda *_args: None)

    def submit(_client, messages, _params, **_kwargs):
        captured["payload"] = json.loads(messages[1]["content"])
        return 901

    def poll(ticket_id):
        index = captured["payload"]["classify_indexes"][0]
        content = json.dumps({"results": [{
            "index": index, "classification": classification, "reason_code": reason_code,
        }]})
        return {
            "ticket_id": ticket_id, "status": "succeeded", "queue_wait_ms": 0, "backend_ms": 2,
            "result": {"choices": [{"message": {"content": content}}]},
        }

    monkeypatch.setattr(qwen, "submit", submit)
    monkeypatch.setattr(qwen, "poll", poll)
    return captured


def _qwen_message(message_id, content, *, author_id=3, message_type="default", history=(), reply=None):
    message = msg(message_id, content)
    message.author.id = author_id
    message.type = SimpleNamespace(name=message_type)
    message.reference = SimpleNamespace(message_id=reply.id, resolved=reply) if reply else None

    async def rows():
        for item in history:
            yield item

    message.channel.history = lambda **_kwargs: rows()
    if reply:
        reply.channel = message.channel
    return message


def test_candidate_uses_queue_and_signal_callback() -> None:
    async def scenario():
        reactions = Reactions()
        store = Store()
        signals = []
        runtime = AddressDetectorRuntime(
            config(), reactions, analyzer=Analyzer(), store=store,
            on_signal=lambda context, result, record: signals.append((context.message_id, result.verdict)),
        )
        await runtime.start()
        assert await runtime.handle_message(msg(1, "ты где?"))
        await runtime.join()
        await runtime.stop()
        return reactions.events, store, signals

    events, store, signals = asyncio.run(scenario())
    assert events == [("add", 1)]
    assert store.analyses == [(1, Verdict.UNCERTAIN)]
    assert signals == [(1, Verdict.UNCERTAIN)]


def test_reply_passes_scope_prefilter_and_serializes_target_before_current(monkeypatch) -> None:
    captured = _fake_qwen_response(monkeypatch, "direct_familiar_address", "informal_pronoun")
    target = msg(100, "Что именно мне проверить?")
    target.author.id = 20
    target.created_at = datetime.now(timezone.utc)
    current = _qwen_message(
        101, "А ты сам не видишь?", author_id=21, message_type="reply",
        history=(target,), reply=target,
    )
    reactions, store, signals = Reactions(), Store(), []

    async def scenario():
        runtime = AddressDetectorRuntime(
            config(), reactions, store=store,
            on_signal=lambda context, result, _record: signals.append((context.message_id, result.verdict)),
        )
        await runtime.start()
        accepted = await runtime.handle_message(current)
        await runtime.join()
        await runtime.stop()
        return accepted

    assert asyncio.run(scenario()) is True
    assert store.upserts == 1
    assert captured["payload"] == {
        "messages": [
            {"index": 0, "speaker": "s1", "text": "Что именно мне проверить?", "reply_to": None, "mentions": []},
            {"index": 1, "speaker": "s2", "text": "А ты сам не видишь?", "reply_to": 0, "mentions": []},
        ],
        "classify_indexes": [1],
    }
    assert reactions.events == [("add", 101)]
    assert signals == [(101, Verdict.LIKELY_VIOLATION)]


def test_direct_reply_stays_direct_after_same_phrase_was_discussed_in_context(monkeypatch) -> None:
    captured = _fake_qwen_response(monkeypatch, "direct_familiar_address", "informal_pronoun")
    meta = msg(98, 'Фраза «а ты сам не видишь?» тут считается фамильярной?')
    meta.author.id = 30
    target = msg(100, "Что именно мне проверить?")
    target.author.id = 20
    duplicate_text = msg(101, "Что именно мне проверить?")
    duplicate_text.author.id = 20
    current = _qwen_message(
        102, "А ты сам не видишь?", author_id=21, message_type="reply",
        history=(meta, target, duplicate_text), reply=target,
    )
    reactions, store, signals = Reactions(), Store(), []

    async def scenario():
        runtime = AddressDetectorRuntime(
            config(), reactions, store=store,
            on_signal=lambda context, result, _record: signals.append((context.message_id, result.verdict)),
        )
        await runtime.start()
        accepted = await runtime.handle_message(current)
        await runtime.join()
        await runtime.stop()
        return accepted

    assert asyncio.run(scenario()) is True
    messages = captured["payload"]["messages"]
    assert captured["payload"]["classify_indexes"] == [3]
    assert messages[0]["text"] == meta.content
    assert messages[1]["text"] == target.content
    assert messages[1]["speaker"] == "s2"
    assert messages[2]["text"] == duplicate_text.content
    assert messages[2]["speaker"] == "s2"
    assert messages[3]["speaker"] == "s3"
    assert messages[3]["reply_to"] == 1
    assert reactions.events == [("add", 102)]
    assert signals == [(102, Verdict.LIKELY_VIOLATION)]


def test_neighbor_quote_does_not_override_direct_classification_of_current_index(monkeypatch) -> None:
    captured = _fake_qwen_response(monkeypatch, "direct_familiar_address", "informal_pronoun")
    quote = msg(99, "Он мне написал: «глянь третий раздел». ")
    quote.author.id = 3
    current = _qwen_message(
        101, "Ты можешь посмотреть третий раздел?", history=(quote,),
    )
    reactions, store, signals = Reactions(), Store(), []

    async def scenario():
        runtime = AddressDetectorRuntime(
            config(), reactions, store=store,
            on_signal=lambda context, result, _record: signals.append((context.message_id, result.verdict)),
        )
        await runtime.start()
        assert await runtime.handle_message(current)
        await runtime.join()
        await runtime.stop()

    asyncio.run(scenario())
    assert captured["payload"]["classify_indexes"] == [1]
    assert captured["payload"]["messages"][0]["text"] == quote.content
    assert captured["payload"]["messages"][1]["text"] == "Ты можешь посмотреть третий раздел?"
    assert reactions.events == [("add", 101)]
    assert signals == [(101, Verdict.LIKELY_VIOLATION)]


def test_meta_reply_is_a_candidate_and_valid_meta_result_removes_marker(monkeypatch) -> None:
    captured = _fake_qwen_response(monkeypatch, "example_or_metadiscussion", "metadiscussion")
    target = msg(100, "Что именно мне проверить?")
    target.author.id = 20
    current = _qwen_message(
        101, 'Фраза «а ты сам не видишь?» тут считается фамильярной?',
        author_id=21, message_type="reply", reply=target,
    )
    reactions, store, signals = Reactions(), Store(), []

    async def scenario():
        runtime = AddressDetectorRuntime(
            config(), reactions, store=store,
            on_signal=lambda *args: signals.append(args),
        )
        await runtime.start()
        accepted = await runtime.handle_message(current)
        await runtime.join()
        await runtime.stop()
        return accepted

    assert asyncio.run(scenario()) is True
    assert store.upserts == 1
    assert captured["payload"]["messages"][1]["reply_to"] == 0
    assert captured["payload"]["classify_indexes"] == [1]
    assert reactions.events == [("add", 101), ("remove", 101)]
    assert signals == []


def test_invalid_semantic_pair_keeps_marker_and_does_not_signal(monkeypatch) -> None:
    _fake_qwen_response(monkeypatch, "quoted_or_reported", "no_specific_addressee")
    current = _qwen_message(101, 'Он написал: «ты можешь подойти?»')
    reactions, store, signals = Reactions(), Store(), []

    async def scenario():
        runtime = AddressDetectorRuntime(
            config(), reactions, store=store,
            on_signal=lambda *args: signals.append(args),
        )
        await runtime.start()
        accepted = await runtime.handle_message(current)
        await runtime.join()
        await runtime.stop()
        return accepted

    assert asyncio.run(scenario()) is True
    assert reactions.events == [("add", 101)]
    assert store.statuses[101] == "classification_failed"
    assert store.analyses == []
    assert signals == []


def test_standalone_meta_result_is_clean_and_removes_marker(monkeypatch) -> None:
    _fake_qwen_response(monkeypatch, "example_or_metadiscussion", "metadiscussion")
    current = _qwen_message(101, 'Фраза «ты можешь посмотреть третий раздел?» считается фамильярной?')
    reactions, store, signals = Reactions(), Store(), []

    async def scenario():
        runtime = AddressDetectorRuntime(
            config(), reactions, store=store,
            on_signal=lambda *args: signals.append(args),
        )
        await runtime.start()
        accepted = await runtime.handle_message(current)
        await runtime.join()
        await runtime.stop()
        return accepted

    assert asyncio.run(scenario()) is True
    assert store.upserts == 1
    assert reactions.events == [("add", 101), ("remove", 101)]
    assert signals == []


def test_likely_production_result_creates_exactly_one_moderator_signal() -> None:
    async def scenario():
        signals = []
        store = Store()
        runtime = AddressDetectorRuntime(
            config(), Reactions(), analyzer=LikelyAnalyzer(), store=store,
            on_signal=lambda context, result, record: signals.append((context.message_id, result.verdict)),
        )
        await runtime.start()
        assert await runtime.handle_message(msg(17, "ты где?"))
        await runtime.join()
        await runtime.stop()
        return store, signals

    store, signals = asyncio.run(scenario())
    assert store.analyses == [(17, Verdict.LIKELY_VIOLATION)]
    assert signals == [(17, Verdict.LIKELY_VIOLATION)]


def test_clean_candidate_is_persisted_as_terminal_and_counted() -> None:
    async def scenario():
        store = Store()
        reactions = Reactions()
        signals = []
        runtime = AddressDetectorRuntime(
            config(), reactions, analyzer=CleanAnalyzer(), store=store,
            on_signal=lambda *args: signals.append(args),
        )
        await runtime.start()
        assert await runtime.handle_message(msg(10, 'Он написал: "ты можешь скинуть лог?"'))
        await runtime.join()
        status = runtime.status()
        await runtime.stop()
        return store, reactions.events, signals, status

    store, reactions, signals, status = asyncio.run(scenario())
    assert store.statuses[10] == "classified"
    assert store.analyses == [(10, Verdict.CLEAN)]
    assert reactions.count(("remove", 10)) == 1
    assert signals == []
    assert status["counters"]["analysis_clean"] == 1


def test_qwen_operational_failure_keeps_marker_and_sends_no_semantic_signal() -> None:
    class FailedAnalyzer:
        async def analyze(self, _context):
            return AnalysisResult.failed("timeout", classifier_version="qwen")

    async def scenario():
        store = Store()
        reactions = Reactions()
        signals = []
        runtime = AddressDetectorRuntime(
            config(), reactions, analyzer=FailedAnalyzer(), store=store,
            on_signal=lambda context, result, record: signals.append((context.message_id, result.verdict)),
        )
        await runtime.start()
        assert await runtime.handle_message(msg(18, "ты, скинь журнал"))
        await runtime.join()
        status = runtime.status()
        await runtime.stop()
        return store, reactions, signals, status

    store, reactions, signals, status = asyncio.run(scenario())
    assert reactions.events == [("add", 18)]
    assert store.statuses[18] == "classification_failed"
    assert signals == []
    assert status["counters"]["qwen_timeout"] == 1


def test_queue_overflow_gets_terminal_state() -> None:
    async def scenario():
        gate = asyncio.Event()

        class SlowAnalyzer:
            async def analyze(self, context):
                await gate.wait()
                return AnalysisResult.uncertain("test")

        store = Store()
        signals = []
        cfg = DetectorConfig(enabled=True, monitored_channel_ids={2}, queue_size=1, worker_count=1, llm_enabled=True)
        runtime = AddressDetectorRuntime(
            cfg, Reactions(), analyzer=SlowAnalyzer(), store=store,
            on_signal=lambda context, result, record: signals.append(context.message_id),
        )
        await runtime.start()
        assert await runtime.handle_message(msg(11, "ты где?"))
        await asyncio.sleep(0)
        assert await runtime.handle_message(msg(12, "ты тут?"))
        assert not await runtime.handle_message(msg(13, "ты слышишь?"))
        gate.set()
        await runtime.join()
        await runtime.stop()
        return store, signals, runtime.status()

    store, signals, status = asyncio.run(scenario())
    assert store.statuses[13] == "queue_overflow"
    assert 13 not in signals
    assert status["counters"]["candidate_queue_overflow"] == 1


def test_non_candidate_never_touches_store_or_history() -> None:
    async def scenario():
        store = Store()
        runtime = AddressDetectorRuntime(config(), Reactions(), analyzer=Analyzer(), store=store)
        await runtime.start()
        accepted = await runtime.handle_message(msg(2, "обычный текст"))
        await runtime.stop()
        return accepted, store.upserts

    assert asyncio.run(scenario()) == (False, 0)


def test_status_exposes_recent_technical_events_without_message_text() -> None:
    runtime = AddressDetectorRuntime(config(), Reactions(), analyzer=Analyzer(), store=Store())

    runtime._event("candidate_detected")
    runtime._event("analysis_uncertain")

    status = runtime.status()
    assert [(item["event"], item["count"]) for item in status["events"][-2:]] == [
        ("candidate_detected", 1),
        ("analysis_uncertain", 1),
    ]
    assert status["events"][-1]["at"]
    assert "content" not in status["events"][-1]


def test_edit_invalidates_in_flight_result() -> None:
    async def scenario():
        gate = asyncio.Event()
        started = asyncio.Event()

        class SlowAnalyzer:
            async def analyze(self, context):
                started.set()
                await gate.wait()
                reason = "old version" if context.raw_content == "ты где?" else "new version"
                return AnalysisResult(Verdict.LIKELY_VIOLATION, 0.99, reason, "test")

        store = Store()
        signals = []
        runtime = AddressDetectorRuntime(
            config(), Reactions(), analyzer=SlowAnalyzer(), store=store,
            on_signal=lambda context, result, record: signals.append(context.message_id),
        )
        await runtime.start()
        candidate = msg(14, "ты где?")
        assert await runtime.handle_message(candidate)
        await started.wait()
        candidate.content = "ты там?"
        await runtime.handle_edit(candidate)
        gate.set()
        await runtime.join()
        status = runtime.status()
        await runtime.stop()
        return store, signals, status

    store, signals, status = asyncio.run(scenario())
    assert store.statuses[14] == "classified"
    assert store.revisions[14] == 2
    assert store.analyses == [(14, Verdict.LIKELY_VIOLATION)]
    assert signals == [14]
    assert status["counters"]["stale_result_ignored"] == 1


def test_delete_ignores_in_flight_result() -> None:
    async def scenario():
        gate = asyncio.Event()
        started = asyncio.Event()

        class SlowAnalyzer:
            async def analyze(self, context):
                started.set()
                await gate.wait()
                return AnalysisResult(Verdict.LIKELY_VIOLATION, 0.99, "deleted version", "test")

        store = Store()
        signals = []
        runtime = AddressDetectorRuntime(
            config(), Reactions(), analyzer=SlowAnalyzer(), store=store,
            on_signal=lambda context, result, record: signals.append(context.message_id),
        )
        await runtime.start()
        assert await runtime.handle_message(msg(15, "ты где?"))
        await started.wait()
        await runtime.handle_delete(15)
        gate.set()
        await runtime.join()
        status = runtime.status()
        await runtime.stop()
        return store, signals, status

    store, signals, status = asyncio.run(scenario())
    assert store.statuses[15] == "deleted"
    assert signals == []
    assert status["counters"]["stale_result_ignored"] == 1


def test_qwen_disabled_does_not_start_runtime_or_accept_candidates(monkeypatch) -> None:
    import discord_bot.address_detector.qwen_shadow as qwen_module

    def forbidden(*args, **kwargs):
        raise AssertionError("Phase A disabled must not call the LLM gateway")

    monkeypatch.setattr(qwen_module, "submit", forbidden)
    monkeypatch.setattr(qwen_module, "poll", forbidden)
    monkeypatch.setattr(qwen_module, "find_ticket", forbidden)

    async def scenario():
        disabled = DetectorConfig(enabled=True, monitored_channel_ids={2}, llm_enabled=False)
        reactions = Reactions()
        runtime = AddressDetectorRuntime(disabled, reactions, analyzer=LikelyAnalyzer(), store=Store())
        await runtime.start()
        accepted = await runtime.handle_message(msg(16, "ты где?"))
        tasks = runtime.service._workers
        status = runtime.status()
        await runtime.stop()
        return accepted, tasks, reactions.events, status

    accepted, tasks, events, status = asyncio.run(scenario())
    assert accepted is False
    assert tasks == []
    assert events == []
    assert status["llm_enabled"] is False


def test_background_default_off_does_not_submit_scoped_non_candidates() -> None:
    async def scenario():
        store = Store()
        runtime = AddressDetectorRuntime(
            DetectorConfig(enabled=True, monitored_channel_ids={2}, llm_enabled=True),
            Reactions(), analyzer=LikelyAnalyzer(), store=store,
        )
        await runtime.start()
        accepted = await runtime.handle_message(msg(19, "обычное сообщение без обращения"))
        status = runtime.status()
        await runtime.stop()
        return accepted, store, status

    accepted, store, status = asyncio.run(scenario())
    assert accepted is False
    assert store.upserts == 0
    assert status["background_batches_per_minute"] == 0
    assert status["background_requests_per_minute"] == 0


def test_edit_to_clean_removes_marker_and_updates_signal_state() -> None:
    async def scenario():
        reactions = Reactions()
        store = Store()
        edits = []
        runtime = AddressDetectorRuntime(
            config(), reactions, analyzer=CleanAnalyzer(), store=store,
            on_edit_clean=lambda message_id: edits.append(message_id),
        )
        await runtime.start()
        candidate = msg(3, "ты где?")
        assert await runtime.handle_message(candidate)
        await runtime.join()
        candidate.content = "обычный текст"
        await runtime.handle_edit(candidate)
        await runtime.stop()
        return reactions.events, edits

    events, edits = asyncio.run(scenario())
    assert events == [("add", 3), ("remove", 3), ("add", 3), ("remove", 3)]
    assert edits == [3]
