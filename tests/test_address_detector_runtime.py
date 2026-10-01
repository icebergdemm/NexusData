import asyncio
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
