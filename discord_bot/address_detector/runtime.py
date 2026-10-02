from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import math
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .analyzer import AnalysisResult, Analyzer, Verdict
from .background import AddressDetectorBackgroundScanner
from .config import DetectorConfig
from .context import AddressContext, ContextCollector
from .filter import TriggerMatch, find_triggers
from .persistence import AddressDetectorStore, AnalysisRecord
from .qwen_shadow import (
    BACKGROUND_CLIENT_ID, FAST_CLIENT_ID, QwenSemanticAnalyzer, build_batch_context,
    configure_limits, verdict_for,
)
from .scope import in_scope
from .service import AddressDetectorService, DetectorJob, ReactionPort

logger = logging.getLogger("NexusBot.address_detector")


class ContextAnalyzer:
    def __init__(
        self, collector: ContextCollector, analyzer: Analyzer, store: AddressDetectorStore,
        on_event: Callable[[str, str | None], None] | None = None,
    ) -> None:
        self.collector = collector
        self.analyzer = analyzer
        self.store = store
        self.on_event = on_event

    async def analyze(self, job: DetectorJob) -> AnalysisResult:
        if job.match is None or job.candidate_context is None:
            return AnalysisResult.failed("missing_trigger_snapshot", classifier_version="qwen")
        context = await self.collector.collect(job.message, job.match, job.candidate_context)
        job.context = context
        try:
            saved = await asyncio.to_thread(self.store.set_classifier_context, context, job.revision)
        except Exception:
            logger.exception("address_detector context_snapshot_write_failed message_id=%s", job.message_id)
            if self.on_event:
                self.on_event("context_snapshot_write_failed", f"message_id={job.message_id}")
            # Audit storage failure must not replace the production classifier's result.
            saved = False
            current = True
        else:
            current = saved
        if not current:
            return AnalysisResult.uncertain("stale candidate revision")
        analyze_job = getattr(self.analyzer, "analyze_job", None)
        if analyze_job is not None:
            from .qwen_shadow import build_request_context
            payload = build_request_context(context)
            try:
                save_request = getattr(self.store, "set_classifier_request", None)
                if save_request:
                    saved = await asyncio.to_thread(save_request, context, job.revision, payload)
                else:
                    saved = await asyncio.to_thread(self.store.set_classifier_context, context, job.revision)
            except Exception:
                logger.exception("address_detector classifier_request_write_failed message_id=%s", job.message_id)
                return AnalysisResult.failed("audit_write_failed", classifier_version="qwen")
            if not saved:
                return AnalysisResult.failed("stale_revision", classifier_version="qwen")
            return await analyze_job(job, context, payload)
        return await self.analyzer.analyze(context)


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
        resolve_message: Callable[[int, int], Awaitable[object | None]] | None = None,
        load_recent: Callable[[int, int, int], Awaitable[list[object]]] | None = None,
    ) -> None:
        self.config = config
        self.store = store or AddressDetectorStore()
        self.on_signal = on_signal
        self.on_edit_clean = on_edit_clean
        self.on_delete = on_delete
        self.resolve_message = resolve_message
        self.load_recent = load_recent
        self.metrics: Counter[str] = Counter()
        self.event_counts: Counter[str] = Counter()
        self.events: deque[dict[str, object]] = deque(maxlen=80)
        self._classification_lock = asyncio.Lock()
        self._rates: dict[str, deque[float]] = defaultdict(deque)
        self._durations: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=512))
        self._message_revisions: dict[int, int] = {}
        self._deleted_message_ids: set[int] = set()
        self.context_analyzer = ContextAnalyzer(
            ContextCollector(config.context_messages), analyzer or QwenSemanticAnalyzer(config), self.store,
            self._event,
        )
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
        self.background = AddressDetectorBackgroundScanner(
            config, self.store, self._background_batch, self._event, on_metric=self._count,
            load_recent=load_recent,
        )

    async def start(self) -> None:
        if self.config.enabled and self.config.llm_enabled and self.config.monitored_channel_ids:
            await self.service.start()
            if self.config.llm_enabled:
                configure_limits(FAST_CLIENT_ID, self.config.llm_fast_quota_min, self.config.llm_fast_queue_limit)
                configure_limits(BACKGROUND_CLIENT_ID, self.config.llm_background_quota_min, self.config.llm_background_queue_limit)
                await self._recover_fast_candidates()
            if self.config.llm_background_enabled:
                await self.background.start()

    async def stop(self) -> None:
        await self.background.stop()
        await self.service.stop()

    async def handle_message(self, message: object) -> bool:
        if not self.config.enabled or not self.config.llm_enabled or not in_scope(message, self.config.scope):
            return False
        self._count("scoped_messages")
        match = find_triggers(getattr(message, "content", ""))
        if not match.tokens:
            if self.config.llm_background_enabled:
                self._message_revisions.setdefault(int(getattr(message, "id")), 1)
                await self.background.observe(message)
            return False
        self._message_revisions.setdefault(int(getattr(message, "id")), 1)
        self._count("trigger_candidates", rate=True)
        self._event("candidate_detected", f"signals={','.join(match.tokens)}")
        context = self._candidate_context(message, match)
        accepted = await asyncio.to_thread(self.store.upsert_candidate, context, match.tokens)
        if not accepted:
            self._message_revisions.pop(context.message_id, None)
            return False
        submission = await self.service.submit(message, match, revision=1, candidate_context=context)
        enqueued = bool(submission)
        if enqueued:
            self._count("qwen_candidates")
            self._count("fast_llm_requests", rate=True)
        if not enqueued:
            status = submission.reason or "queue_rejected"
            await asyncio.to_thread(self.store.mark_terminal, context.message_id, 1, status, status)
            self._message_revisions.pop(context.message_id, None)
            if status != "queue_overflow":
                self._count("candidate_queue_rejected")
        return enqueued

    async def handle_edit(self, message: object) -> bool:
        message_id = int(getattr(message, "id"))
        if not self.config.enabled or not self.config.llm_enabled or not in_scope(message, self.config.scope):
            return False
        self._message_revisions[int(getattr(message, "id"))] = self._message_revisions.get(int(getattr(message, "id")), 1) + 1
        match = find_triggers(getattr(message, "content", ""))
        context = self._candidate_context(message, match)
        signal = await asyncio.to_thread(self.store.get_signal, message_id) if hasattr(self.store, "get_signal") else None
        if not match.tokens and signal is None:
            if self.config.llm_background_enabled:
                await self.background.observe(message, force=True)
            return False
        async with self._classification_lock:
            revision = await asyncio.to_thread(self.store.update_candidate, context, match.tokens)
            self._count("edited_candidates")
        submission = await self.service.submit(
            message, match, revision=revision, candidate_context=context, is_edit=True
        )
        enqueued = bool(submission)
        if enqueued:
            self._count("qwen_candidates")
            self._count("fast_llm_requests", rate=True)
        if not enqueued:
            status = submission.reason or "queue_rejected"
            await asyncio.to_thread(self.store.mark_terminal, message_id, revision, status, status)
            self._message_revisions.pop(message_id, None)
            if status != "queue_overflow":
                self._count("candidate_queue_rejected")
        return enqueued

    async def handle_delete(self, message_id: int) -> None:
        if int(message_id) in self._message_revisions:
            self._deleted_message_ids.add(int(message_id))
            self._message_revisions[int(message_id)] += 1
        async with self._classification_lock:
            deleted = await asyncio.to_thread(self.store.mark_deleted, message_id)
            if deleted:
                self._count("deleted_candidates")
            if self.on_delete:
                result = self.on_delete(message_id)
                if inspect.isawaitable(result):
                    await result

    async def join(self) -> None:
        await self.service.join()

    async def _recover_fast_candidates(self) -> None:
        if self.resolve_message is None or not hasattr(self.store, "pending_candidates"):
            return
        for pending in await asyncio.to_thread(self.store.pending_candidates):
            channel_id = pending["channel_id"]
            message_id = pending["message_id"]
            if channel_id not in self.config.scope.monitored_channel_ids or channel_id in self.config.scope.excluded_channel_ids:
                await asyncio.to_thread(self.store.mark_terminal, message_id, pending["revision"], "out_of_scope", "channel is no longer monitored")
                continue
            message = await self.resolve_message(channel_id, message_id)
            if message is None:
                await asyncio.to_thread(self.store.mark_deleted, message_id)
                self._count("deleted_candidates")
                continue
            match = find_triggers(getattr(message, "content", ""))
            context = self._candidate_context(message, match)
            revision = pending["revision"]
            if context.raw_content != pending["content"]:
                revision = await asyncio.to_thread(self.store.update_candidate, context, match.tokens)
                self._count("edited_candidates")
            self._message_revisions[message_id] = revision
            submission = await self.service.submit(
                message, match, revision=revision, candidate_context=context,
            )
            if submission:
                self._count("recovered_candidates")
                self._count("qwen_candidates")
                self._count("fast_llm_requests", rate=True)
            else:
                status = submission.reason or "queue_rejected"
                await asyncio.to_thread(self.store.mark_terminal, message_id, revision, status, status)

    async def _classified(self, job: DetectorJob, result: AnalysisResult) -> bool:
        context = job.context or job.candidate_context
        if context is None:
            context = self._candidate_context(job.message, job.match or find_triggers(getattr(job.message, "content", "")))
        async with self._classification_lock:
            if result.operational_failure:
                try:
                    saved = await asyncio.to_thread(
                        self.store.mark_terminal, job.message_id, job.revision,
                        "classification_failed", result.reason or "Qwen classification failed",
                    )
                except Exception:
                    logger.exception("address_detector classification_failure_persist_failed message_id=%s", job.message_id)
                    saved = False
                if not saved:
                    self._count("stale_result_ignored")
                    return False
                self._count("qwen_failed")
                failure_metric = {
                    "timeout": "qwen_timeout",
                    "invalid_response": "qwen_invalid_response",
                    "backend_failure": "qwen_backend_failure",
                }.get(result.reason, "qwen_gateway_failure")
                self._count(failure_metric)
                self._message_revisions.pop(job.message_id, None)
                self._event("classification_failed", result.reason)
                return True
            try:
                record = await asyncio.to_thread(
                    self.store.record_analysis,
                    context, result, self.config.incident_window_seconds, job.revision,
                )
            except Exception:
                logger.exception("address_detector record_analysis_failed message_id=%s", job.message_id)
                try:
                    await asyncio.to_thread(
                        self.store.mark_terminal, job.message_id, job.revision,
                        "analysis_error", "analysis result could not be persisted",
                    )
                except Exception:
                    logger.exception("address_detector terminal_state_write_failed message_id=%s", job.message_id)
                self._event("analysis_persistence_error")
                return False
            if record is None:
                self._count("stale_result_ignored")
                self._event("stale_result_ignored", f"message_id={job.message_id} revision={job.revision}")
                return False

            self._message_revisions.pop(job.message_id, None)
            self._deleted_message_ids.discard(job.message_id)

            counter = {
                Verdict.CLEAN: "analysis_clean",
                Verdict.UNCERTAIN: "analysis_uncertain",
                Verdict.LIKELY_VIOLATION: "analysis_likely",
            }[result.verdict]
            self._count(counter)
            if result.verdict is Verdict.LIKELY_VIOLATION:
                self._count("qwen_direct")
                await self.service.add_marker(job.message)
            elif result.verdict is Verdict.CLEAN:
                self._count("qwen_clean")
                if getattr(job, "is_edit", False):
                    await self.service.remove_marker(job.message)
            elif result.classification == "ambiguous":
                self._count("qwen_ambiguous")
            if result.classification:
                self._count(f"classification_{result.classification}")
            if result.queue_wait_ms is not None:
                self._observe("fast_queue_wait_ms", result.queue_wait_ms)
            if result.backend_ms is not None:
                self._observe("backend_latency_ms", result.backend_ms)
            if result.total_ms is not None:
                self._observe("fast_total_latency_ms", result.total_ms)
            self._event(counter, f"classifier=qwen reason_code={result.reason_code or '-'}")
            if result.verdict is Verdict.LIKELY_VIOLATION and self.on_signal:
                callback_result = self.on_signal(context, result, record)
                if inspect.isawaitable(callback_result):
                    await callback_result
                self._event(
                    "signal_sent" if record.should_ping_moderators else "signal_aggregated",
                    f"incident_messages={record.message_count}",
                )
            elif result.verdict is Verdict.CLEAN and getattr(job, "is_edit", False) and self.on_edit_clean:
                callback_result = self.on_edit_clean(job.message_id)
                if inspect.isawaitable(callback_result):
                    await callback_result
            return True

    def status(self) -> dict:
        now = time.monotonic()
        for values in self._rates.values():
            while values and now - values[0] > 60:
                values.popleft()
        return {
            "counters": {**dict(self.metrics), "candidate_queue_depth": self.service.queue.qsize()},
            "events": list(self.events),
            "candidate_queue_depth": self.service.queue.qsize(),
            "llm_enabled": self.config.llm_enabled,
            "llm_background_enabled": self.config.llm_background_enabled,
            "fast_candidates_per_minute": len(self._rates["trigger_candidates"]),
            "fast_llm_requests_per_minute": len(self._rates["fast_llm_requests"]),
            "background_messages_per_minute": len(self._rates["background_messages"]),
            "background_batches_per_minute": len(self._rates["background_batches"]),
            "background_requests_per_minute": len(self._rates["background_llm_requests"]),
            "fast_queue_wait_ms": self._duration_summary("fast_queue_wait_ms"),
            "fast_total_latency_ms": self._duration_summary("fast_total_latency_ms"),
            "background_queue_wait_ms": self._duration_summary("background_queue_wait_ms"),
            "background_messages_per_batch": self._duration_summary("background_messages_per_batch"),
            "backend_latency_ms": self._duration_summary("backend_latency_ms"),
            "background_queue_depth": self.background.status()["queue_depth"],
            "background_in_flight": self.background.status()["in_flight"],
            "last_classifier_error": next((item.get("detail") for item in reversed(self.events) if "failed" in str(item.get("event")) or "timeout" in str(item.get("event"))), None),
        }

    async def _background_batch(self, channel_id: int, messages: list[object], pending: dict | None) -> None:
        analyzer = self.context_analyzer.analyzer
        if pending is None:
            if not messages:
                return
            payload, items = await self._background_payload(messages)
            if not items:
                return
            identity = ":".join(
                f"{item['message_id']}:{hashlib.sha256(item['content'].encode()).hexdigest()[:12]}"
                for item in items
            )
            key = f"address_detector_background:{channel_id}:{hashlib.sha256(identity.encode()).hexdigest()[:32]}"
            pending = {
                "key": key,
                "last_message_id": max(int(item["message_id"]) for item in items),
                "payload": payload,
                "items": items,
            }
            await asyncio.to_thread(self.store.save_background_pending, channel_id, pending)
        payload = pending["payload"]
        self._count("background_llm_requests", rate=True)
        try:
            results, timing = await analyzer.classify_batch(payload, pending["key"], client_id=BACKGROUND_CLIENT_ID)
        except Exception as exc:
            name = "qwen_invalid_response" if isinstance(exc, ValueError) else "qwen_background_timeout" if isinstance(exc, TimeoutError) else "qwen_background_failure"
            self._count(name)
            self._event("background_classification_failed", name)
            for item in pending["items"]:
                message_id = int(item["message_id"])
                self._message_revisions.pop(message_id, None)
                self._deleted_message_ids.discard(message_id)
            return
        self._observe("background_queue_wait_ms", timing["queue_wait_ms"])
        self._observe("backend_latency_ms", timing["backend_ms"])
        self._observe("background_total_latency_ms", timing["total_ms"])
        self._observe("background_messages_per_batch", len(pending["items"]))
        by_index = {int(item["index"]): item for item in pending["items"]}
        live_messages = {int(getattr(message, "id")): message for message in messages}
        for result in results:
            item = by_index[result["index"]]
            message = live_messages.get(int(item["message_id"]))
            if message is None and self.resolve_message:
                message = await self.resolve_message(channel_id, int(item["message_id"]))
            await self._apply_background_result(message, item, result, payload, timing)
        for item in pending["items"]:
            message_id = int(item["message_id"])
            self._message_revisions.pop(message_id, None)
            self._deleted_message_ids.discard(message_id)

    async def _background_payload(self, messages: list[object]) -> tuple[dict, list[dict]]:
        from .qwen_shadow import build_batch_context

        messages = sorted(messages, key=lambda item: int(getattr(item, "id", 0)))
        channel = getattr(messages[0], "channel", None)
        records: dict[int, dict] = {}

        def add(message: object) -> None:
            message_id = int(getattr(message, "id", 0))
            author = getattr(message, "author", None)
            reference = getattr(message, "reference", None)
            records[message_id] = {
                "id": message_id,
                "author_id": getattr(author, "id", None),
                "text": str(getattr(message, "content", "")),
                "reply_to": getattr(reference, "message_id", None),
                "mentions": [str(getattr(item, "id", "")) for item in getattr(message, "mentions", ())],
                "created_at": getattr(message, "created_at", None),
            }

        for message in messages:
            add(message)
            reference = getattr(message, "reference", None)
            replied = getattr(reference, "resolved", None) if reference is not None else None
            if replied is None and reference is not None and getattr(reference, "message_id", None) is not None:
                try:
                    replied = await channel.fetch_message(reference.message_id)
                except Exception:
                    replied = None
            if replied is not None:
                add(replied)

        try:
            async for old in channel.history(limit=self.config.context_messages, before=messages[0]):
                add(old)
        except Exception:
            pass
        history = sorted(records.values(), key=lambda row: (row["created_at"] is None, row["created_at"] or 0, row["id"]))[-16:]
        classify_ids = [int(getattr(message, "id")) for message in messages]
        payload = build_batch_context(history, classify_ids)
        indexes = dict(zip(classify_ids, payload["classify_indexes"]))
        items = []
        for message in messages:
            message_id = int(getattr(message, "id"))
            content = str(getattr(message, "content", ""))
            items.append({
                "message_id": message_id,
                "index": indexes[message_id],
                "content": content,
                "revision": self._message_revisions.get(message_id, 1),
                "created_at": getattr(message, "created_at", None).isoformat() if getattr(message, "created_at", None) else None,
                "jump_url": getattr(message, "jump_url", None),
            })
        return payload, items

    async def _apply_background_result(self, message: object | None, item: dict, result: dict, payload: dict, timing: dict) -> None:
        message_id = int(item["message_id"])
        if message is None or message_id in self._deleted_message_ids:
            self._count("deleted_candidates")
            return
        if self._message_revisions.get(message_id, item["revision"]) != item["revision"] or str(getattr(message, "content", "")) != item["content"]:
            self._count("stale_result_ignored")
            return
        classification = result["classification"]
        reason_code = result["reason_code"]
        self._count(f"classification_{classification}")
        if classification not in {"direct_familiar_address", "ambiguous"}:
            return
        match = find_triggers(item["content"])
        base = self._candidate_context(message, match)
        context = await self.context_analyzer.collector.collect(message, match, base)
        signal = await asyncio.to_thread(self.store.get_signal, message_id)
        if signal is None:
            accepted = await asyncio.to_thread(self.store.upsert_candidate, context, match.tokens)
            if not accepted:
                self._count("stale_result_ignored")
                return
            revision = 1
        else:
            revision = await asyncio.to_thread(self.store.update_candidate, context, match.tokens)
        await asyncio.to_thread(self.store.set_classifier_request, context, revision, payload)
        result_obj = AnalysisResult(
            verdict=verdict_for(classification),
            reason=reason_code,
            classifier_version="qwen",
            matched_signals=(reason_code,),
            classification=classification,
            reason_code=reason_code,
            ticket_id=timing["ticket_id"],
            queue_wait_ms=timing["queue_wait_ms"],
            backend_ms=timing["backend_ms"],
            total_ms=timing["total_ms"],
            request_chars=len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        )
        await self._classified(DetectorJob(message, match, revision, context, context), result_obj)

    def _count(self, name: str, value: int = 1, *, rate: bool = False) -> None:
        self.metrics[name] += value
        if name in {"background_batches", "background_messages", "background_llm_requests", "fast_llm_requests"}:
            rate = True
        if rate:
            now = time.monotonic()
            values = self._rates[name]
            while values and now - values[0] > 60:
                values.popleft()
            values.append(now)

    def _observe(self, name: str, value: float) -> None:
        self._durations[name].append(value)

    def _duration_summary(self, name: str) -> dict:
        values = sorted(self._durations[name])
        if not values:
            return {"count": 0, "avg": None, "p95": None}
        return {
            "count": len(values),
            "avg": round(sum(values) / len(values), 1),
            "p95": round(values[max(0, math.ceil(len(values) * 0.95) - 1)], 1),
        }

    def _event(self, name: str, detail: str | None = None) -> None:
        if name == "queue_overflow":
            self._count("candidate_queue_overflow")
        elif name == "reaction_added":
            self._count("reactions_after_verdict")
        self.event_counts[name] += 1
        count = self.event_counts[name]
        level = "error" if ("error" in name or "overflow" in name or "failed" in name) else "warning" if "timeout" in name or "warning" in name else "info"
        self.events.append({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": name,
            "count": count,
            "level": level,
            **({"detail": detail} if detail else {}),
        })
        logger.info("address_detector event=%s detail=%s", name, detail)

    @staticmethod
    def _candidate_context(message: object, match: TriggerMatch) -> AddressContext:
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
