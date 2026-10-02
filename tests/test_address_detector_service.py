import asyncio
from dataclasses import dataclass

from discord_bot.address_detector.analyzer import AnalysisResult, Verdict
from discord_bot.address_detector.service import AddressDetectorService


@dataclass
class Job:
    message_id: int


class FakeReactions:
    def __init__(self) -> None:
        self.events: list[tuple[str, int]] = []

    async def add(self, message: Job) -> None:
        self.events.append(("add", message.message_id))

    async def remove(self, message: Job) -> None:
        self.events.append(("remove", message.message_id))


class FakeAnalyzer:
    def __init__(self, result: AnalysisResult, gate: asyncio.Event | None = None) -> None:
        self.result = result
        self.gate = gate
        self.calls: list[int] = []

    async def analyze(self, job: Job) -> AnalysisResult:
        self.calls.append(job.message_id)
        if self.gate:
            await self.gate.wait()
        return self.result


def run(coro):
    return asyncio.run(coro)


def result(verdict: Verdict) -> AnalysisResult:
    return AnalysisResult(verdict=verdict, confidence=0.9, reason="test", classifier_version="test")


def test_clean_removes_reaction_and_sends_no_signal() -> None:
    async def scenario():
        reactions = FakeReactions()
        signals: list[tuple[int, Verdict]] = []
        service = AddressDetectorService(
            FakeAnalyzer(result(Verdict.CLEAN)), reactions,
            lambda job, analysis: signals.append((job.message_id, analysis.verdict)), queue_size=2,
        )
        await service.start()
        assert await service.submit(Job(1))
        await service.join()
        await service.stop()
        return reactions.events, signals

    events, signals = run(scenario())
    assert events == [("add", 1), ("remove", 1)]
    assert signals == []


def test_uncertain_keeps_reaction_and_sends_signal() -> None:
    async def scenario():
        reactions = FakeReactions()
        signals: list[tuple[int, Verdict]] = []
        service = AddressDetectorService(
            FakeAnalyzer(result(Verdict.UNCERTAIN)), reactions,
            lambda job, analysis: signals.append((job.message_id, analysis.verdict)),
        )
        await service.start()
        assert await service.submit(Job(2))
        await service.join()
        await service.stop()
        return reactions.events, signals

    events, signals = run(scenario())
    assert events == [("add", 2)]
    assert signals == [(2, Verdict.UNCERTAIN)]


def test_timeout_is_operational_failure_not_a_semantic_uncertain_signal() -> None:
    async def scenario():
        reactions = FakeReactions()
        signals: list[tuple[int, Verdict]] = []
        service = AddressDetectorService(
            FakeAnalyzer(result(Verdict.LIKELY_VIOLATION), asyncio.Event()),
            reactions,
            lambda job, analysis: signals.append((job.message_id, analysis.verdict)),
            analysis_timeout=0.01,
        )
        await service.start()
        await service.submit(Job(3))
        await service.join()
        await service.stop()
        return reactions.events, signals

    events, signals = run(scenario())
    assert events == [("add", 3)]
    assert signals == []


def test_queue_overflow_does_not_start_unbounded_work() -> None:
    async def scenario():
        reactions = FakeReactions()
        gate = asyncio.Event()
        analyzer = FakeAnalyzer(result(Verdict.UNCERTAIN), gate)
        service = AddressDetectorService(analyzer, reactions, lambda _job, _analysis: None, queue_size=1)
        await service.start()
        assert await service.submit(Job(4))
        assert not await service.submit(Job(5))
        gate.set()
        await service.join()
        await service.stop()
        return analyzer.calls, reactions.events

    calls, events = run(scenario())
    assert calls == [4]
    assert events[-1] == ("add", 5)
