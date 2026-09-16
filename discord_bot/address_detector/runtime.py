from __future__ import annotations

import asyncio
import inspect
import logging
from collections import Counter
from typing import Awaitable, Callable

from .analyzer import AnalysisResult, Analyzer, RuleAnalyzer, Verdict
from .config import DetectorConfig
from .context import AddressContext, ContextCollector
from .filter import TriggerMatch, find_triggers
from .persistence import AddressDetectorStore, AnalysisRecord
from .scope import in_scope
from .service import AddressDetectorService, DetectorJob, ReactionPort

logger = logging.getLogger("NexusBot.address_detector")


class ContextAnalyzer:
    def __init__(self, collector: ContextCollector, analyzer: Analyzer) -> None:
        self.collector = collector
        self.analyzer = analyzer
        self.contexts: dict[int, AddressContext] = {}

    async def analyze(self, job: DetectorJob) -> AnalysisResult:
        if job.match is None:
            return AnalysisResult.uncertain("missing trigger match")
        context = await self.collector.collect(job.message, job.match)
        self.contexts[context.message_id] = context
        result = await self.analyzer.analyze(context)
        if result.verdict is Verdict.CLEAN:
            self.contexts.pop(context.message_id, None)
        return result

    def pop_context(self, message_id: int) -> AddressContext | None:
        return self.contexts.pop(message_id, None)


SignalHandler = Callable[[AddressContext, AnalysisResult, AnalysisRecord], Awaitable[None] | None]
EditCleanHandler = Callable[[int], Awaitable[None] | None]
DeleteHandler = Callable[[int], Awaitable[None] | None]


class AddressDetectorRuntime:
    """Discord-independent orchestration for scope, queue, context and storage."""

    def __init__(
        self,
        config: DetectorConfig,
        reactions: ReactionPort,
        *,
        analyzer: Analyzer | None = None,
        store: AddressDetectorStore | None = None,
        on_signal: SignalHandler | None = None,
        on_edit_clean: EditCleanHandler | None = None,
        on_delete: DeleteHandler | None = None,
    ) -> None:
        self.config = config
        self.store = store or AddressDetectorStore()
        self.context_analyzer = ContextAnalyzer(
            ContextCollector(config.context_messages), analyzer or RuleAnalyzer()
        )
        self.on_signal = on_signal
        self.on_edit_clean = on_edit_clean
        self.on_delete = on_delete
        self.metrics: Counter[str] = Counter()
        self._classification_lock = asyncio.Lock()
        self.service = AddressDetectorService(
            self.context_analyzer,
            reactions,
            lambda _message, _result: None,
            queue_size=config.queue_size,
            worker_count=config.worker_count,
            analysis_timeout=config.analysis_timeout_seconds,
            on_event=self._event,
            on_result=self._classified,
        )

    async def start(self) -> None:
        if self.config.enabled and self.config.monitored_channel_ids:
            await self.service.start()

    async def stop(self) -> None:
        await self.service.stop()

    async def handle_message(self, message: object) -> bool:
        if not self.config.enabled or not in_scope(message, self.config.scope):
            return False
        match = find_triggers(getattr(message, "content", ""))
        if not match.tokens:
            return False
        self._event("candidate_detected")
        await self.service.add_marker(message)
        accepted = await asyncio.to_thread(self.store.upsert_candidate, self._candidate_context(message, match), match.tokens)
        if not accepted:
            await self.service.remove_marker(message)
            return False
        return await self.service.submit(message, match, marker_added=True)

    async def handle_edit(self, message: object) -> bool:
        if not self.config.enabled or not in_scope(message, self.config.scope):
            return False
        match = find_triggers(getattr(message, "content", ""))
        if not match.tokens:
            await asyncio.to_thread(self.store.mark_edit_clean, int(getattr(message, "id")))
            await self.service.remove_marker(message)
            if self.on_edit_clean:
                result = self.on_edit_clean(int(getattr(message, "id")))
                if inspect.isawaitable(result):
                    await result
            return False
        context = self._candidate_context(message, match)
        await asyncio.to_thread(self.store.update_candidate, context, match.tokens)
        await self.service.add_marker(message)
        return await self.service.submit(message, match, marker_added=True)

    async def handle_delete(self, message_id: int) -> None:
        await asyncio.to_thread(self.store.mark_deleted, message_id)
        if self.on_delete:
            result = self.on_delete(message_id)
            if inspect.isawaitable(result):
                await result

    async def join(self) -> None:
        await self.service.join()

    async def _classified(self, message: object, result: AnalysisResult) -> None:
        message_id = int(getattr(message, "id"))
        context = self.context_analyzer.pop_context(message_id)
        if context is None:
            logger.error("address_detector missing_context message_id=%s", message_id)
            return
        async with self._classification_lock:
            record = await asyncio.to_thread(
                self.store.record_analysis,
                context,
                result,
                self.config.incident_window_seconds,
            )
        self._event({
            Verdict.CLEAN: "analysis_clean",
            Verdict.UNCERTAIN: "analysis_uncertain",
            Verdict.LIKELY_VIOLATION: "analysis_likely",
        }[result.verdict])
        if self.on_signal and result.verdict is not Verdict.CLEAN:
            callback_result = self.on_signal(context, result, record)
            if inspect.isawaitable(callback_result):
                await callback_result
            self._event("signal_sent" if record.should_ping_moderators else "signal_aggregated")

    def status(self) -> dict:
        return {"counters": dict(self.metrics)}

    def _event(self, name: str) -> None:
        self.metrics[name] += 1
        logger.info("address_detector event=%s", name)

    @staticmethod
    def _candidate_context(message: object, match: TriggerMatch) -> AddressContext:
        # The durable candidate row needs only a cheap snapshot. Full reply and
        # history context are collected exactly once by the worker.
        author = getattr(message, "author", None)
        guild = getattr(message, "guild", None)
        channel = getattr(message, "channel", None)
        display_name = (
            getattr(author, "display_name", None)
            or getattr(author, "global_name", None)
            or getattr(author, "name", None)
            or str(getattr(author, "id", "unknown"))
        )
        return AddressContext(
            message_id=int(getattr(message, "id")),
            guild_id=getattr(guild, "id", None),
            channel_id=getattr(channel, "id", None),
            author_id=getattr(author, "id", None),
            author_display_name=str(display_name),
            timestamp=getattr(message, "created_at", None),
            raw_content=str(getattr(message, "content", "")),
            cleaned_content=match.cleaned_text,
            trigger_tokens=tuple(match.tokens),
            is_reply=False,
            reply=None,
            reply_author_id=None,
            mentions=tuple(str(getattr(item, "id")) for item in getattr(message, "mentions", ())),
            previous_messages=(),
            jump_url=getattr(message, "jump_url", None),
        )
