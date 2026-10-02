from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from .analyzer import AnalysisResult, Analyzer, Verdict
from .context import AddressContext
from .filter import TriggerMatch

logger = logging.getLogger("NexusBot.address_detector")


class ReactionPort(Protocol):
    async def add(self, message: object) -> None: ...

    async def remove(self, message: object) -> None: ...


@dataclass
class DetectorJob:
    message: object
    match: TriggerMatch | None = None
    revision: int = 1
    candidate_context: AddressContext | None = None
    context: AddressContext | None = None
    is_edit: bool = False

    @property
    def message_id(self) -> int:
        return int(getattr(self.message, "id", getattr(self.message, "message_id", 0)))


@dataclass(frozen=True)
class SubmitResult:
    accepted: bool
    reason: str | None = None

    def __bool__(self) -> bool:
        return self.accepted


SignalCallback = Callable[[object, AnalysisResult], Awaitable[None] | None]
EventCallback = Callable[[str, str | None], None]
ResultCallback = Callable[[DetectorJob, AnalysisResult], Awaitable[bool | None] | bool | None]


class AddressDetectorService:
    """Small bounded worker queue for the reaction/analyzer lifecycle."""

    def __init__(
        self,
        analyzer: Analyzer,
        reactions: ReactionPort,
        on_signal: SignalCallback,
        *,
        queue_size: int = 128,
        worker_count: int = 1,
        analysis_timeout: float = 15.0,
        on_event: EventCallback | None = None,
        on_result: ResultCallback | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.reactions = reactions
        self.on_signal = on_signal
        self.on_result = on_result
        self.queue: asyncio.Queue[DetectorJob | None] = asyncio.Queue(maxsize=max(1, queue_size))
        self.worker_count = max(1, worker_count)
        self.analysis_timeout = max(0.01, analysis_timeout)
        self.on_event = on_event
        self._workers: list[asyncio.Task[None]] = []
        self._stopping = False

    async def start(self) -> None:
        if self._workers:
            return
        self._stopping = False
        self._workers = [asyncio.create_task(self._worker(), name="address-detector") for _ in range(self.worker_count)]

    async def stop(self) -> None:
        if not self._workers:
            return
        self._stopping = True
        for _ in self._workers:
            await self.queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    async def submit(
        self, message: object, match: TriggerMatch | None = None, *, revision: int = 1,
        candidate_context: AddressContext | None = None,
        is_edit: bool = False,
    ) -> SubmitResult:
        """Enqueue a candidate without taking a moderator action."""

        if self._stopping:
            return SubmitResult(False, "stopping")

        try:
            self.queue.put_nowait(DetectorJob(message, match, revision, candidate_context, is_edit=is_edit))
        except asyncio.QueueFull:
            logger.error("address_detector queue_overflow")
            self._event("queue_overflow", "очередь заполнена")
            return SubmitResult(False, "queue_overflow")
        return SubmitResult(True)

    async def add_marker(self, message: object) -> None:
        try:
            await self.reactions.add(message)
            self._event("reaction_added", "redadmin")
        except Exception:
            # A missing emoji or Add Reactions permission must not discard the
            # candidate; the worker can still deliver a moderator signal.
            logger.exception("address_detector reaction_add_error")
            self._event("reaction_error", "reaction lifecycle unavailable")

    async def remove_marker(self, message: object) -> None:
        await self._remove_reaction(message)

    async def join(self) -> None:
        await self.queue.join()

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                if job is None:
                    return
                result = await self._analyze(job)
                accepted = True
                if self.on_result is not None:
                    callback_result = self.on_result(job, result)
                    if inspect.isawaitable(callback_result):
                        callback_result = await callback_result
                    accepted = callback_result is not False
                if not accepted:
                    continue
                if result.operational_failure:
                    continue
                if result.verdict is Verdict.LIKELY_VIOLATION:
                    callback_result = self.on_signal(job.message, result)
                    if inspect.isawaitable(callback_result):
                        await callback_result
            except Exception:
                logger.exception("address_detector worker_error")
                # Only a validated CLEAN result may remove the marker.
            finally:
                self.queue.task_done()

    async def _analyze(self, job: DetectorJob) -> AnalysisResult:
        try:
            analyzer_input = job if job.match is not None else job.message
            return await asyncio.wait_for(self.analyzer.analyze(analyzer_input), timeout=self.analysis_timeout)
        except asyncio.TimeoutError:
            logger.warning("address_detector analysis_timeout")
            self._event("analysis_timeout", "analysis timeout")
            return AnalysisResult.failed("analysis_timeout")
        except Exception:
            logger.exception("address_detector analysis_error")
            self._event("analysis_error", "analysis error")
            return AnalysisResult.failed("analysis_error")

    async def _remove_reaction(self, message: object) -> None:
        if message is None:
            return
        try:
            await self.reactions.remove(message)
            self._event("reaction_removed", "redadmin")
        except Exception:
            logger.exception("address_detector reaction_remove_error")

    def _event(self, name: str, detail: str | None = None) -> None:
        if self.on_event:
            self.on_event(name, detail)
