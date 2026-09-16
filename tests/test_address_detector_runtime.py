import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

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

    def upsert_candidate(self, context, tokens):
        self.upserts += 1
        return self.upserts == 1

    def update_candidate(self, context, tokens):
        self.upserts += 1

    def record_analysis(self, context, result, window):
        self.analyses.append((context.message_id, result.verdict))
        return SimpleNamespace(incident_id=1, should_ping_moderators=True, message_count=1)

    def mark_edit_clean(self, message_id):
        self.clean_edits.append(message_id)

    def mark_deleted(self, message_id):
        pass


class Analyzer:
    async def analyze(self, context):
        return AnalysisResult(Verdict.UNCERTAIN, 0.5, "test", "test")


class CleanAnalyzer:
    async def analyze(self, context):
        return AnalysisResult(Verdict.CLEAN, 0.99, "quoted example", "test")


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
    return DetectorConfig(enabled=True, monitored_channel_ids={2})


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
    assert events == [("add", 3), ("remove", 3), ("remove", 3)]
    assert edits == [3]
