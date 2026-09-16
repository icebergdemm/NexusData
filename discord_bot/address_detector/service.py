from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from .analyzer import AnalysisResult, Analyzer, Verdict
from .filter import TriggerMatch

logger = logging.getLogger("NexusBot.address_detector")


class ReactionPort(Protocol):
    async def add(self, message: object) -> None: ...

    async def remove(self, message: object) -> None: ...


@dataclass(frozen=True)
class DetectorJob:
    message: object
    match: TriggerMatch | None = None


SignalCallback = Callable[[object, AnalysisResult], Awaitable[None] | None]


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
    ) -> None:
        self.analyzer = analyzer
        self.reactions = reactions
        self.on_signal = on_signal
        self.queue: asyncio.Queue[DetectorJob | None] = asyncio.Queue(maxsize=max(1, queue_size))
        self.worker_count = max(1, worker_count)
        self.analysis_timeout = max(0.01, analysis_timeout)
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

    async def submit(self, message: object, match: TriggerMatch | None = None, *, marker_added: bool = False) -> bool:
        """Add the marker first, then enqueue. False means the queue rejected it."""

        if self._stopping:
            return False
        if not marker_added:
            await self.add_marker(message)

        try:
            self.queue.put_nowait(DetectorJob(message, match))
        except asyncio.QueueFull:
            logger.error("address_detector queue_overflow")
            await self._remove_reaction(message)
            return False
        return True

    async def add_marker(self, message: object) -> None:
        try:
            await self.reactions.add(message)
        except Exception:
            # A missing emoji or Add Reactions permission must not discard the
            # candidate; the worker can still deliver a moderator signal.
            logger.exception("address_detector reaction_add_error")

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
                result = await self._analyze(job if job.match is not None else job.message)
                if result.verdict is Verdict.CLEAN:
                    await self._remove_reaction(job.message)
                else:
                    callback_result = self.on_signal(job.message, result)
                    if inspect.isawaitable(callback_result):
                        await callback_result
            except Exception:
                logger.exception("address_detector worker_error")
                await self._remove_reaction(getattr(job, "message", None)) if job is not None else None
            finally:
                self.queue.task_done()

    async def _analyze(self, message: object) -> AnalysisResult:
        try:
            return await asyncio.wait_for(self.analyzer.analyze(message), timeout=self.analysis_timeout)
        except asyncio.TimeoutError:
            logger.warning("address_detector analysis_timeout")
            return AnalysisResult.uncertain("analysis timeout")
        except Exception:
            logger.exception("address_detector analysis_error")
            return AnalysisResult.uncertain("analysis error")

    async def _remove_reaction(self, message: object) -> None:
        if message is None:
            return
        try:
            await self.reactions.remove(message)
        except Exception:
            logger.exception("address_detector reaction_remove_error")
