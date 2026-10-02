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
import discord
from services_llm_gateway import LLMGatewayError, active_legacy_detector_tickets, cancel as cancel_llm_ticket

from .analyzer import AnalysisResult, Analyzer, Verdict
from .config import DetectorConfig
from .context import AddressContext, ContextCollector
from .filter import TriggerMatch, find_triggers
from .persistence import AddressDetectorStore, AnalysisRecord
from .qwen_classifier import CLIENT_ID, QwenSemanticAnalyzer, batch_idempotency_key, build_batch_context, configure_limits, verdict_for
from .scheduler import AddressDetectorScheduler
from .scope import in_scope
from .service import DetectorJob, ReactionPort

logger = logging.getLogger("NexusBot.address_detector")


SignalHandler = Callable[[AddressContext, AnalysisResult, AnalysisRecord], Awaitable[None] | None]
EditCleanHandler = Callable[[int], Awaitable[None] | None]
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
    ) -> None:
        self.config = config
        self.store = store or AddressDetectorStore()
        self.on_signal = on_signal
        self.on_edit_clean = on_edit_clean
        self.on_delete = on_delete
        self.resolve_message = resolve_message
        self.metrics: Counter[str] = Counter()
        self.event_counts: Counter[str] = Counter()
        self.events: deque[dict[str, object]] = deque(maxlen=80)
        self._classification_lock = asyncio.Lock()
        self._rates: dict[str, deque[float]] = defaultdict(deque)
        self._durations: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=512))
        self.reactions = reactions
        self.analyzer = analyzer or QwenSemanticAnalyzer(config)
        self.collector = ContextCollector(config.context_messages)
        self.scheduler = AddressDetectorScheduler(config, self.store, self._prepare_batch, self._process_batch, self._event)

    async def start(self, *, reconcile: bool = True) -> None:
        if reconcile:
            await self._reconcile_legacy_tickets()
        if self.config.enabled and self.config.llm_enabled and self.config.monitored_channel_ids:
            configure_limits(CLIENT_ID, self.config.llm_quota_min, 1)
            await self.scheduler.start()

    async def stop(self) -> None:
        await self.scheduler.stop()

    async def handle_message(self, message: object) -> bool:
        if not self.config.enabled or not self.config.llm_enabled or not in_scope(message, self.config.scope):
            return False
        self._count("scoped_messages")
        return await self._queue_message(message, edit=False)

    async def handle_edit(self, message: object) -> bool:
        message_id = int(getattr(message, "id"))
        if not self.config.enabled or not self.config.llm_enabled or not in_scope(message, self.config.scope):
            return False
        return await self._queue_message(message, edit=True)

    async def handle_delete(self, message_id: int) -> None:
        async with self._classification_lock:
            await asyncio.to_thread(self.store.mark_work_deleted, message_id)
            deleted = await asyncio.to_thread(self.store.mark_deleted, message_id)
            if deleted:
                self._count("deleted_candidates")
            if self.on_delete:
                result = self.on_delete(message_id)
                if inspect.isawaitable(result):
                    await result
        self.scheduler.wake()

    async def join(self) -> None:
        while await asyncio.to_thread(self.store.pending_work) or any(
            row["pending_batch"] for row in await asyncio.to_thread(self.store.detector_channel_states)
        ):
            await asyncio.sleep(0.01)

    async def _queue_message(self, message: object, *, edit: bool) -> bool:
        channel_id = int(getattr(getattr(message, "channel", None), "id", 0))
        message_id = int(getattr(message, "id", 0))
        author_id = int(getattr(getattr(message, "author", None), "id", 0))
        if not channel_id or not message_id or not author_id:
            return False
        reference = getattr(message, "reference", None)
        reply_target_id = getattr(reference, "message_id", None) if reference is not None else None
        resolved_reply = getattr(reference, "resolved", None) if reference is not None else None
        reply_snapshot = None
        if resolved_reply is not None:
            reply_author = getattr(resolved_reply, "author", None)
            reply_snapshot = {
                "id": int(getattr(resolved_reply, "id", reply_target_id or 0)),
                "author_id": getattr(reply_author, "id", None),
                "text": str(getattr(resolved_reply, "content", "")),
                "reply_to": getattr(getattr(resolved_reply, "reference", None), "message_id", None),
                "mentions": [str(getattr(item, "id", "")) for item in getattr(resolved_reply, "mentions", ())],
            }
        created_at = getattr(message, "created_at", None)
        payload = {
            "content": str(getattr(message, "content", "")), "author_id": author_id,
            "reply_target_id": reply_target_id,
            "reply_target": reply_snapshot,
            "created_at": created_at.isoformat() if created_at else None,
            "jump_url": getattr(message, "jump_url", None),
            "mentions": [int(getattr(item, "id")) for item in getattr(message, "mentions", ()) if getattr(item, "id", None)],
        }
        async with self._classification_lock:
            revision = await asyncio.to_thread(
                self.store.put_message_work, channel_id, message_id, author_id, payload["content"],
                reply_target_id, payload, edit=edit,
            )
        self._count("scoped_revisions")
        self.scheduler.wake()
        return True

    async def _reconcile_legacy_tickets(self, *, requeue: bool | None = None) -> None:
        if requeue is None:
            requeue = bool(self.config.enabled and self.config.llm_enabled and self.config.monitored_channel_ids)
        states = await asyncio.to_thread(self.store.detector_channel_states)
        by_key = {row["pending_batch"].get("key"): (row["channel_id"], row["pending_batch"])
                  for row in states if row["pending_batch"]}
        for ticket in await asyncio.to_thread(active_legacy_detector_tickets):
            if not await asyncio.to_thread(cancel_llm_ticket, ticket["ticket_id"]):
                continue
            key = ticket["idempotency_key"]
            saved = by_key.get(key)
            if saved:
                channel_id, batch = saved
                await asyncio.to_thread(self.store.clear_legacy_batch, channel_id, key)
                for item in batch.get("items", []) if requeue else []:
                    message = await self.resolve_message(channel_id, int(item["message_id"])) if self.resolve_message else None
                    if message is not None:
                        await self._queue_message(message, edit=False)
                    else:
                        await asyncio.to_thread(
                            self.store.enqueue_work, channel_id, int(item["message_id"]), int(item["revision"]),
                            int(item["author_id"]), str(item["content"]), item.get("reply_target_id"),
                            item.get("payload") or {},
                        )
                continue
            parts = key.split(":")
            if requeue and ticket["client_id"] == "address_detector_fast" and len(parts) == 3 and parts[1].isdigit():
                signal = await asyncio.to_thread(self.store.get_signal, int(parts[1]))
                if signal is None or self.resolve_message is None:
                    continue
                message = await self.resolve_message(int(signal.channel_id), int(signal.message_id))
                if message is not None:
                    await self._queue_message(message, edit=False)
        if any(ticket["client_id"] in {"address_detector_fast", "address_detector_background"}
               for ticket in await asyncio.to_thread(active_legacy_detector_tickets)):
            self._event("legacy_ticket_reconciliation_incomplete")

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


            counter = {
                Verdict.CLEAN: "analysis_clean",
                Verdict.UNCERTAIN: "analysis_uncertain",
                Verdict.LIKELY_VIOLATION: "analysis_likely",
            }[result.verdict]
            self._count(counter)
            if result.verdict is Verdict.LIKELY_VIOLATION:
                self._count("qwen_direct")
                await self._add_marker(job.message)
            elif result.classification == "ambiguous":
                self._count("qwen_ambiguous")
            if result.queue_wait_ms is not None:
                self._observe("queue_wait_ms", result.queue_wait_ms)
            if result.backend_ms is not None:
                self._observe("backend_latency_ms", result.backend_ms)
            if result.total_ms is not None:
                self._observe("total_latency_ms", result.total_ms)
            self._event(counter, f"classifier=qwen reason_code={result.reason_code or '-'}")
            if result.verdict is Verdict.LIKELY_VIOLATION and self.on_signal:
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
            "counters": {**dict(self.metrics), "pending_revisions": len(self.store.pending_work())},
            "events": list(self.events),
            "queue_depth": len(self.store.pending_work()),
            "llm_enabled": self.config.llm_enabled,
            "scoped_messages_per_minute": len(self._rates["scoped_revisions"]),
            "batches_per_minute": len(self._rates["batches"]),
            "queue_wait_ms": self._duration_summary("queue_wait_ms"),
            "total_latency_ms": self._duration_summary("total_latency_ms"),
            "backend_latency_ms": self._duration_summary("backend_latency_ms"),
            "last_classifier_error": next((item.get("detail") for item in reversed(self.events) if "failed" in str(item.get("event")) or "timeout" in str(item.get("event"))), None),
        }

    async def _prepare_batch(self, channel_id: int, selected: list[dict]) -> dict:
        records = {}
        live = {}
        protected_ids = {item["message_id"] for item in selected}

        def add(message, *, protect=False):
            message_id = int(getattr(message, "id", 0))
            author = getattr(message, "author", None)
            reference = getattr(message, "reference", None)
            value = {
                "id": message_id, "author_id": getattr(author, "id", None),
                "text": str(getattr(message, "content", "")),
                "reply_to": getattr(reference, "message_id", None),
                "mentions": [str(getattr(item, "id", "")) for item in getattr(message, "mentions", ())],
                "created_at": getattr(message, "created_at", None),
            }
            if protect or message_id not in protected_ids:
                records[message_id] = value
            live[message_id] = message

        messages = []
        for item in selected:
            message = await self.resolve_message(channel_id, item["message_id"]) if self.resolve_message else None
            if message is not None:
                messages.append(message)
                add(message, protect=True)
            else:
                records[item["message_id"]] = {
                    "id": item["message_id"], "author_id": item["author_id"], "text": item["content"],
                    "reply_to": item["reply_target_id"], "mentions": item["payload"].get("mentions", []),
                    "created_at": item["payload"].get("created_at"),
                }
                target = item["payload"].get("reply_target")
                if target and target.get("id"):
                    protected_ids.add(int(target["id"]))
                    records[int(target["id"])] = target
        channel = getattr(messages[0], "channel", None) if messages else None
        for message in messages:
            reference = getattr(message, "reference", None)
            target_id = getattr(reference, "message_id", None) if reference else None
            if target_id and target_id not in records:
                protected_ids.add(int(target_id))
                replied = getattr(reference, "resolved", None)
                if replied is None and channel is not None:
                    try:
                        replied = await channel.fetch_message(target_id)
                    except discord.NotFound:
                        replied = None
                if replied is not None:
                    add(replied, protect=True)
                else:
                    records[target_id] = {"id": target_id, "author_id": None, "text": "", "reply_to": None, "mentions": []}
        for item in selected:
            target_id = item["reply_target_id"]
            if target_id and int(target_id) not in records:
                protected_ids.add(int(target_id))
                records[int(target_id)] = {
                    "id": int(target_id), "author_id": None, "text": "", "reply_to": None, "mentions": [],
                }
        if channel is not None:
            try:
                async for old in channel.history(limit=self.config.llm_max_payload_messages):
                    add(old)
            except Exception:
                pass
        new_ids = [item["message_id"] for item in selected]
        new_set = set(new_ids)
        targets = list(dict.fromkeys(item["reply_target_id"] for item in selected if item["reply_target_id"]))
        mandatory = [records[key] for key in new_ids]
        mandatory += [records[key] for key in targets if key in records and key not in new_set]
        remaining = [row for key, row in records.items() if key not in new_set and key not in targets]
        remaining.sort(key=lambda row: int(row["id"]), reverse=True)
        chosen = mandatory + remaining[:max(0, self.config.llm_max_payload_messages - len(mandatory))]
        chosen.sort(key=lambda row: int(row["id"]))
        payload = build_batch_context(chosen, new_ids)
        indexes = dict(zip(new_ids, payload["classify_indexes"]))
        items = [{
            "message_id": item["message_id"], "revision": item["revision"],
            "index": indexes[item["message_id"]], "content": item["content"],
            "author_id": item["author_id"], "reply_target_id": item["reply_target_id"],
            "payload": item["payload"],
        } for item in selected]
        key = batch_idempotency_key(channel_id, items)
        return {"key": key, "channel_id": channel_id, "state": "prepared", "payload": payload,
                "items": items, "attempt": 0, "ticket_id": None, "applied_indexes": []}

    async def _process_batch(self, channel_id: int, batch: dict) -> None:
        if batch.get("state") == "terminal_received":
            results, timing = batch["results"], batch["timing"]
        else:
            batch = await asyncio.to_thread(self.store.update_batch_state, channel_id, batch, "submitted")
            metadata = {"batch_key": batch["key"], "channel_id": channel_id, "items": batch["items"]}
            def ticket_seen(ticket_id, attempt):
                batch.update(state="submitted", ticket_id=ticket_id, attempt=attempt)
                self.store.update_active_batch(channel_id, batch)
                self.store.set_batch_ticket(batch["key"], ticket_id)
            try:
                results, timing = await self.analyzer.classify_batch(
                    batch["payload"], batch["key"], application_metadata=metadata, on_ticket=ticket_seen,
                )
            except LLMGatewayError as exc:
                self._event("gateway_recovery_wait", str(getattr(exc, "status_code", 0)))
                return True
            except Exception as exc:
                outcomes = [{"index": item["index"], "message_id": item["message_id"], "revision": item["revision"],
                             "status": "terminal_operational_failure", "classification": None,
                             "reason_code": type(exc).__name__, "action": "none"}
                            for item in batch["items"]]
                batch = await asyncio.to_thread(self.store.update_batch_state, channel_id, batch,
                                                "terminal_received", outcomes=outcomes)
                await asyncio.to_thread(self.store.save_batch_outcomes, batch["key"], outcomes)
                await asyncio.to_thread(self.store.complete_detector_batch, channel_id, batch, outcomes)
                self._count("terminal_failures", len(outcomes))
                self._event("classification_failed", type(exc).__name__)
                return
            try:
                batch = await asyncio.to_thread(self.store.update_batch_state, channel_id, batch, "terminal_received",
                                                results=results, timing=timing)
            except Exception:
                logger.exception("address_detector terminal_result_persist_failed batch=%s", batch["key"])
                self._event("batch_persistence_wait", "terminal result not saved")
                return True
        outcomes = list(batch.get("outcomes", []))
        for result in results:
            item = next(row for row in batch["items"] if row["index"] == result["index"])
            if result["index"] not in batch.get("applied_indexes", []):
                if result["classification"] == "direct_familiar_address":
                    intents = batch.setdefault("action_intents", {})
                    intent_key = str(result["index"])
                    token = intents.get(intent_key) or f"{batch['key']}:{item['message_id']}:{item['revision']}"
                    intents[intent_key] = token
                    await asyncio.to_thread(self.store.update_active_batch, channel_id, batch)
                    intent = {
                        "index": item["index"], "message_id": item["message_id"],
                        "revision": item["revision"], "classification": result["classification"],
                        "reason_code": result["reason_code"], "action": "moderator_signal_pending",
                        "action_token": token,
                    }
                    audit_outcomes = [row for row in outcomes if row.get("index") != item["index"]]
                    await asyncio.to_thread(self.store.save_batch_outcomes, batch["key"], [*audit_outcomes, intent])
                outcome = await self._apply_batch_result(channel_id, batch, item, result, timing)
                batch.setdefault("applied_indexes", []).append(result["index"])
                outcomes.append(outcome)
                batch["outcomes"] = outcomes
                await asyncio.to_thread(self.store.update_active_batch, channel_id, batch)
        await asyncio.to_thread(self.store.save_batch_outcomes, batch["key"], outcomes)
        await asyncio.to_thread(self.store.complete_detector_batch, channel_id, batch, outcomes)
        self._count("batches", rate=True)
        self._observe("queue_wait_ms", timing["queue_wait_ms"])
        self._observe("backend_latency_ms", timing["backend_ms"])
        self._observe("total_latency_ms", timing["total_ms"])

    async def _apply_batch_result(self, channel_id: int, batch: dict, item: dict, result: dict, timing: dict) -> dict:
        message_id, revision = item["message_id"], item["revision"]
        current = await asyncio.to_thread(self.store.current_work_revision, channel_id, message_id)
        if current != revision:
            self._count("stale_result_ignored")
            return {"index": item["index"], "message_id": message_id, "revision": revision,
                    "classification": result["classification"], "reason_code": result["reason_code"],
                    "action": "none", "status": "superseded"}
        message = await self.resolve_message(channel_id, message_id) if self.resolve_message else None
        if message is not None and str(getattr(message, "content", "")) != item["content"]:
            self._count("stale_result_ignored")
            return {"index": item["index"], "message_id": message_id, "revision": revision,
                    "classification": result["classification"], "reason_code": result["reason_code"],
                    "action": "none", "status": "content_fenced"}
        classification = result["classification"]
        outcome = {"index": item["index"], "message_id": message_id, "revision": revision, "classification": classification,
                   "reason_code": result["reason_code"], "action": "none",
                   **({"action_token": batch["action_intents"][str(item["index"])]}
                      if str(item["index"]) in batch.get("action_intents", {}) else {})}
        self._count(f"classification_{classification}")
        if message is None:
            return outcome
        if classification not in {"direct_familiar_address", "ambiguous"}:
            self._count("qwen_clean")
            async with self._classification_lock:
                current = await asyncio.to_thread(self.store.current_work_revision, channel_id, message_id)
                signal = await asyncio.to_thread(self.store.get_signal, message_id)
                if current == revision and signal is not None and classification in {"no_violation", "quoted_or_reported", "example_or_metadiscussion"}:
                    match = find_triggers(item["content"])
                    context = await self.collector.collect(message, match, self._candidate_context(message, match))
                    if await asyncio.to_thread(self.store.mark_edit_clean, message_id, context, revision):
                        await self._remove_marker(message)
                        if self.on_edit_clean:
                            callback = self.on_edit_clean(message_id)
                            if inspect.isawaitable(callback):
                                await callback
                        outcome["action"] = "clear_existing_moderator_signal"
            return outcome
        match = find_triggers(item["content"])
        context = await self.collector.collect(message, match, self._candidate_context(message, match))
        if not await asyncio.to_thread(self.store.set_candidate_revision, context, match.tokens, revision):
            return {**outcome, "status": "superseded"}
        result_obj = AnalysisResult(
            verdict=verdict_for(classification), reason=result["reason_code"], classifier_version="qwen",
            matched_signals=(result["reason_code"],), classification=classification,
            reason_code=result["reason_code"], ticket_id=timing["ticket_id"],
            idempotency_key=batch.get("action_intents", {}).get(str(item["index"]), batch["key"]),
            queue_wait_ms=timing["queue_wait_ms"],
            backend_ms=timing["backend_ms"], total_ms=timing["total_ms"],
            request_chars=len(json.dumps(batch["payload"], ensure_ascii=False, separators=(",", ":"))),
        )
        await self._classified(DetectorJob(message, match, revision, context, context), result_obj)
        outcome["action"] = "moderator_signal" if classification == "direct_familiar_address" else "review_record"
        return outcome

    def _count(self, name: str, value: int = 1, *, rate: bool = False) -> None:
        self.metrics[name] += value
        if name in {"batches", "scoped_revisions"}:
            rate = True
        if rate:
            now = time.monotonic()
            values = self._rates[name]
            while values and now - values[0] > 60:
                values.popleft()
            values.append(now)

    def _observe(self, name: str, value: float) -> None:
        self._durations[name].append(value)

    async def _add_marker(self, message: object) -> None:
        try:
            await self.reactions.add(message)
            self._event("reaction_added", "redadmin")
        except Exception:
            logger.exception("address_detector reaction_add_error")
            self._event("reaction_error", "reaction lifecycle unavailable")

    async def _remove_marker(self, message: object) -> None:
        try:
            await self.reactions.remove(message)
            self._event("reaction_removed", "redadmin")
        except Exception:
            logger.exception("address_detector reaction_remove_error")

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
