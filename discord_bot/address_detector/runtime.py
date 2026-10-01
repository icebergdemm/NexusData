from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .analyzer import AnalysisResult, Analyzer, RuleAnalyzer, Verdict
from .config import DetectorConfig
from .context import AddressContext, ContextCollector
from .filter import TriggerMatch, find_triggers
from .persistence import AddressDetectorStore, AnalysisRecord
from .qwen_shadow import (
    CLIENT_ID, LLMGatewayError, cancel, configure_limits, find_ticket,
    idempotency_key, parse_content, poll, request_messages, request_params,
    response_content, shadow_verdict, submit,
)
from .scope import in_scope
from .service import AddressDetectorService, DetectorJob, ReactionPort

logger = logging.getLogger("NexusBot.address_detector")


class ContextAnalyzer:
    def __init__(
        self, collector: ContextCollector, analyzer: Analyzer, store: AddressDetectorStore,
        on_context: Callable[[DetectorJob, AddressContext], None] | None = None,
        on_event: Callable[[str, str | None], None] | None = None,
    ) -> None:
        self.collector = collector
        self.analyzer = analyzer
        self.store = store
        self.on_context = on_context
        self.on_event = on_event

    async def analyze(self, job: DetectorJob) -> AnalysisResult:
        if job.match is None or job.candidate_context is None:
            return AnalysisResult.uncertain("missing trigger snapshot")
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
        if saved and self.on_context:
            self.on_context(job, context)
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
    ) -> None:
        self.config = config
        self.store = store or AddressDetectorStore()
        self.on_signal = on_signal
        self.on_edit_clean = on_edit_clean
        self.on_delete = on_delete
        self.metrics: Counter[str] = Counter()
        self.event_counts: Counter[str] = Counter()
        self.events: deque[dict[str, object]] = deque(maxlen=80)
        self._classification_lock = asyncio.Lock()
        self._rates: dict[str, deque[float]] = defaultdict(deque)
        self._durations: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=512))
        self._shadow_tasks: set[asyncio.Task] = set()
        self._last_shadow_error: str | None = None
        self.context_analyzer = ContextAnalyzer(
            ContextCollector(config.context_messages), analyzer or RuleAnalyzer(), self.store,
            self._schedule_shadow if config.llm_shadow_enabled else None,
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

    async def start(self) -> None:
        if self.config.enabled and self.config.monitored_channel_ids:
            await self.service.start()
            if self.config.llm_shadow_enabled:
                configure_limits(self.config.llm_quota_min, self.config.llm_queue_limit)
            try:
                pending_rows = await asyncio.to_thread(self.store.pending_shadow)
            except Exception:
                logger.exception("address_detector shadow_recovery_scan_failed")
                self._event("qwen_shadow_recovery_failed", "pending request scan failed")
                return
            for pending in pending_rows:
                self._track_shadow(self._resume_shadow(pending, allow_submit=self.config.llm_shadow_enabled))

    async def stop(self) -> None:
        await self.service.stop()
        for task in tuple(self._shadow_tasks):
            task.cancel()
        if self._shadow_tasks:
            await asyncio.gather(*self._shadow_tasks, return_exceptions=True)
        self._shadow_tasks.clear()

    async def handle_message(self, message: object) -> bool:
        if not self.config.enabled or not in_scope(message, self.config.scope):
            return False
        self._count("scoped_messages")
        match = find_triggers(getattr(message, "content", ""))
        if not match.tokens:
            return False
        self._count("trigger_candidates", rate=True)
        self._event("candidate_detected", f"signals={','.join(match.tokens)}")
        context = self._candidate_context(message, match)
        await self.service.add_marker(message)
        accepted = await asyncio.to_thread(self.store.upsert_candidate, context, match.tokens)
        if not accepted:
            await self.service.remove_marker(message)
            return False
        submission = await self.service.submit(message, match, marker_added=True, revision=1, candidate_context=context)
        enqueued = bool(submission)
        if not enqueued:
            status = submission.reason or "queue_rejected"
            await asyncio.to_thread(self.store.mark_terminal, context.message_id, 1, status, status)
            if status != "queue_overflow":
                self._count("candidate_queue_rejected")
        return enqueued

    async def handle_edit(self, message: object) -> bool:
        message_id = int(getattr(message, "id"))
        if not self.config.enabled or not in_scope(message, self.config.scope):
            return False
        match = find_triggers(getattr(message, "content", ""))
        context = self._candidate_context(message, match)
        if not match.tokens:
            async with self._classification_lock:
                edited = await asyncio.to_thread(self.store.mark_edit_clean, message_id, context)
                if edited:
                    self._count("edited_candidates")
                await self.service.remove_marker(message)
                if self.on_edit_clean:
                    result = self.on_edit_clean(message_id)
                    if inspect.isawaitable(result):
                        await result
            return False
        async with self._classification_lock:
            revision = await asyncio.to_thread(self.store.update_candidate, context, match.tokens)
            self._count("edited_candidates")
        await self.service.add_marker(message)
        submission = await self.service.submit(
            message, match, marker_added=True, revision=revision, candidate_context=context
        )
        enqueued = bool(submission)
        if not enqueued:
            status = submission.reason or "queue_rejected"
            await asyncio.to_thread(self.store.mark_terminal, message_id, revision, status, status)
            if status != "queue_overflow":
                self._count("candidate_queue_rejected")
        return enqueued

    async def handle_delete(self, message_id: int) -> None:
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

    async def _classified(self, job: DetectorJob, result: AnalysisResult) -> bool:
        context = job.context or job.candidate_context
        if context is None:
            context = self._candidate_context(job.message, job.match or find_triggers(getattr(job.message, "content", "")))
        async with self._classification_lock:
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

            counter = {
                Verdict.CLEAN: "analysis_clean",
                Verdict.UNCERTAIN: "analysis_uncertain",
                Verdict.LIKELY_VIOLATION: "analysis_likely",
            }[result.verdict]
            self._count(counter)
            verdict_metric = {
                Verdict.CLEAN: "clean",
                Verdict.UNCERTAIN: "uncertain",
                Verdict.LIKELY_VIOLATION: "likely",
            }[result.verdict]
            if result.classifier_version == "rules":
                self._count(f"rules_{verdict_metric}")
            else:
                self._count("rules_uncertain")
                self._count("onnx_requests")
                self._count(f"onnx_{verdict_metric}")
            detail_parts = [f"classifier={result.classifier_version}"]
            if result.confidence is not None:
                detail_parts.append(f"confidence={result.confidence:.2f}")
            if result.matched_signals:
                detail_parts.append(f"signals={','.join(result.matched_signals)}")
            if result.reason:
                detail_parts.append(f"reason={result.reason}")
            self._event(counter, " ".join(detail_parts))
            if result.verdict is not Verdict.CLEAN and self.on_signal:
                callback_result = self.on_signal(context, result, record)
                if inspect.isawaitable(callback_result):
                    await callback_result
                self._event(
                    "signal_sent" if record.should_ping_moderators else "signal_aggregated",
                    f"incident_messages={record.message_count}",
                )
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
            "shadow_enabled": self.config.llm_shadow_enabled,
            "shadow_in_flight": sum(not task.done() for task in self._shadow_tasks),
            "candidates_per_minute": len(self._rates["trigger_candidates"]),
            "qwen_requests_per_minute": len(self._rates["qwen_shadow_submitted"]),
            "qwen_shadow_queue_wait_ms": self._duration_summary("qwen_shadow_queue_wait_ms"),
            "qwen_shadow_backend_ms": self._duration_summary("qwen_shadow_backend_ms"),
            "last_shadow_error": self._last_shadow_error,
        }

    def _schedule_shadow(self, job: DetectorJob, context: AddressContext) -> None:
        if not self.config.llm_shadow_enabled:
            return
        value = self.store_context(context)
        self._track_shadow(self._run_shadow(job.message_id, job.revision, value))

    @staticmethod
    def store_context(context: AddressContext) -> dict:
        from .persistence import _compact_context
        return _compact_context(context)

    def _track_shadow(self, coroutine) -> None:
        task = asyncio.create_task(coroutine, name="address-detector-qwen-shadow")
        self._shadow_tasks.add(task)
        task.add_done_callback(self._shadow_tasks.discard)

    async def _resume_shadow(self, pending: dict, *, allow_submit: bool) -> None:
        if not await asyncio.to_thread(self.store.is_current, pending["message_id"], pending["revision"]):
            ticket_id = pending.get("ticket_id")
            if not ticket_id and pending.get("idempotency_key"):
                try:
                    ticket_id = await asyncio.to_thread(find_ticket, CLIENT_ID, pending["idempotency_key"])
                except Exception:
                    logger.exception("address_detector shadow_recovery_lookup_failed")
            if ticket_id:
                try:
                    await asyncio.to_thread(cancel, int(ticket_id))
                except Exception:
                    logger.exception("address_detector shadow_stale_cancel_failed")
            self._count("stale_result_ignored")
            return
        context = pending.get("context")
        if context:
            await self._run_shadow(pending["message_id"], pending["revision"], context, pending, allow_submit=allow_submit)

    async def _run_shadow(
        self, message_id: int, revision: int, context: dict, previous: dict | None = None,
        *, allow_submit: bool = True,
    ) -> None:
        key = idempotency_key(message_id, revision)
        submitted_at = (previous or {}).get("submitted_at") or datetime.now(timezone.utc).isoformat()
        state = {"status": "submitting", "idempotency_key": key, "submitted_at": submitted_at}
        try:
            params = request_params()
            state["model"] = (previous or {}).get("model") or params.get("model")
            if not await asyncio.to_thread(self.store.set_shadow, message_id, revision, state):
                self._count("stale_result_ignored")
                return
            ticket_id = int((previous or {}).get("ticket_id") or 0)
            if not ticket_id:
                ticket_id = await asyncio.to_thread(find_ticket, CLIENT_ID, key) or 0
            if not ticket_id:
                if not allow_submit:
                    state.update({"status": "disabled", "error": "shadow disabled before gateway ticket creation"})
                    await asyncio.to_thread(self.store.set_shadow, message_id, revision, state)
                    return
                ticket_id = await asyncio.to_thread(
                    submit, CLIENT_ID, request_messages(context), params,
                    idempotency_key=key, timeout=float(self.config.llm_backend_timeout_seconds),
                    requested_by="address_detector",
                )
            if not (previous or {}).get("submitted_counted"):
                self._count("qwen_shadow_submitted", rate=True)
            state.update({"status": "queued", "ticket_id": ticket_id, "submitted_counted": True})
            await asyncio.to_thread(self.store.set_shadow, message_id, revision, state)
            age_start = _parse_time(submitted_at)
            elapsed_age = max(0.0, (datetime.now(timezone.utc) - age_start).total_seconds())
            deadline = time.monotonic() + max(0.0, self.config.llm_max_request_age_seconds - elapsed_age)
            while True:
                if not await asyncio.to_thread(self.store.is_current, message_id, revision):
                    await asyncio.to_thread(cancel, ticket_id)
                    self._count("stale_result_ignored")
                    return
                if time.monotonic() >= deadline:
                    await asyncio.to_thread(cancel, ticket_id)
                    self._count("qwen_shadow_timeout")
                    state.update({"status": "timeout", "ticket_id": ticket_id, "error": "maximum request age exceeded"})
                    await asyncio.to_thread(self.store.set_shadow, message_id, revision, state)
                    self._event("qwen_shadow_timeout", state["error"])
                    return
                ticket = await asyncio.to_thread(poll, ticket_id)
                queue_wait_ms = ticket.get("queue_wait_ms")
                if ticket["status"] == "running":
                    state["status"] = "running"
                    await asyncio.to_thread(self.store.set_shadow, message_id, revision, state)
                if ticket["status"] in {"succeeded", "failed", "cancelled"}:
                    queue_wait = ticket.get("queue_wait_ms")
                    backend_ms = ticket.get("backend_ms")
                    elapsed_ms = queue_wait + backend_ms if queue_wait is not None and backend_ms is not None else None
                    expired = (
                        elapsed_ms >= self.config.llm_max_request_age_seconds * 1000
                        if elapsed_ms is not None else time.monotonic() >= deadline
                    )
                    if expired:
                        self._count("qwen_shadow_timeout")
                        state.update({"status": "timeout", "ticket_id": ticket_id, "error": "maximum request age exceeded"})
                        await asyncio.to_thread(self.store.set_shadow, message_id, revision, state)
                        self._event("qwen_shadow_timeout", state["error"])
                        return
                    break
                age_expired = time.monotonic() >= deadline
                queue_expired = ticket["status"] == "queued" and queue_wait_ms is not None and queue_wait_ms >= self.config.llm_queue_wait_timeout_seconds * 1000
                if age_expired or queue_expired:
                    if queue_wait_ms is not None:
                        self._observe("qwen_shadow_queue_wait_ms", float(queue_wait_ms))
                    if ticket.get("backend_ms") is not None:
                        self._observe("qwen_shadow_backend_ms", float(ticket["backend_ms"]))
                    await asyncio.to_thread(cancel, ticket_id)
                    self._count("qwen_shadow_timeout")
                    state.update({"status": "timeout", "ticket_id": ticket_id, "error": "request age limit" if age_expired else "queue wait limit"})
                    await asyncio.to_thread(self.store.set_shadow, message_id, revision, state)
                    self._event("qwen_shadow_timeout", state["error"])
                    return
                await asyncio.sleep(0.5)

            if queue_wait_ms is not None:
                self._observe("qwen_shadow_queue_wait_ms", float(queue_wait_ms))
            backend_ms = ticket.get("backend_ms")
            if backend_ms is not None:
                self._observe("qwen_shadow_backend_ms", float(backend_ms))
            if ticket["status"] != "succeeded":
                backend_error = str(ticket.get("error") or ticket["status"])
                timed_out = "timeout" in backend_error.lower() or "timed out" in backend_error.lower()
                counter = "qwen_shadow_timeout" if timed_out else "qwen_shadow_failed"
                error = "backend timeout" if timed_out else "gateway request cancelled" if ticket["status"] == "cancelled" else "backend request failed"
                self._count(counter)
                self._last_shadow_error = error
                state.update({"status": "timeout" if timed_out else "failed", "ticket_id": ticket_id, "error": error})
                await asyncio.to_thread(self.store.set_shadow, message_id, revision, state)
                self._event(counter, error)
                return
            try:
                parsed = parse_content(response_content(ticket.get("result")))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._count("qwen_shadow_invalid_response")
                self._last_shadow_error = str(exc)[:240]
                state.update({"status": "invalid_response", "ticket_id": ticket_id, "error": self._last_shadow_error})
                await asyncio.to_thread(self.store.set_shadow, message_id, revision, state)
                self._event("qwen_shadow_invalid_response", self._last_shadow_error)
                return
            state.update({
                "status": "completed", "ticket_id": ticket_id,
                "classification": parsed["classification"], "reason_code": parsed["reason_code"],
                "shadow_verdict": shadow_verdict(parsed["classification"]).value,
                "queue_wait_ms": queue_wait_ms, "backend_ms": backend_ms,
            })
            if await asyncio.to_thread(self.store.set_shadow, message_id, revision, state):
                self._count("qwen_shadow_completed")
                self._event("qwen_shadow_completed", parsed["classification"])
            else:
                self._count("stale_result_ignored")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("address_detector shadow_error message_id=%s", message_id)
            error = str(exc)[:240]
            if not isinstance(exc, LLMGatewayError):
                error = type(exc).__name__
            self._last_shadow_error = error
            counter = "qwen_shadow_failed"
            if isinstance(exc, LLMGatewayError) and getattr(exc, "status_code", 0) in {408, 504}:
                counter = "qwen_shadow_timeout"
            self._count(counter)
            try:
                await asyncio.to_thread(self.store.set_shadow, message_id, revision, {
                    **state, "status": "timeout" if counter.endswith("timeout") else "failed", "error": error,
                })
            except Exception:
                logger.exception("address_detector shadow_status_write_failed message_id=%s", message_id)
            self._event(counter, error)

    def _count(self, name: str, *, rate: bool = False) -> None:
        self.metrics[name] += 1
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


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
