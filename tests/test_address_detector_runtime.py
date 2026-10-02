import asyncio
from datetime import datetime, timedelta, timezone
import time
from types import SimpleNamespace

import discord_bot.address_detector.runtime as runtime_module
from discord_bot.address_detector.analyzer import Verdict
from discord_bot.address_detector.config import DetectorConfig
from discord_bot.address_detector.runtime import AddressDetectorRuntime


class Store:
    def __init__(self):
        self.work, self.states, self.analyses, self.signals = {}, {}, [], {}
        self.created_batches = []
        self.saved_audit_outcomes = []

    def put_message_work(self, channel_id, message_id, author_id, content, reply_target_id, payload, *, edit=False):
        key = (channel_id, message_id)
        old = self.work.get(key)
        revision = old["revision"] + 1 if old and edit else old["revision"] if old else 1
        self.work[key] = {"channel_id": channel_id, "message_id": message_id, "author_id": author_id,
                          "content": content, "reply_target_id": reply_target_id, "revision": revision,
                          "event_order": message_id, "payload": payload,
                          "created_at": (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat(),
                          "age_seconds": 3,
                          "state": "pending"}
        return revision

    def pending_work(self, channel_id=None):
        return sorted([dict(row) for row in self.work.values() if row["state"] == "pending"
                       and (channel_id is None or row["channel_id"] == channel_id)],
                      key=lambda row: (row["channel_id"], row["event_order"]))

    def detector_channel_states(self):
        return [{"channel_id": key, "last_message_id": 0, "pending_batch": value}
                for key, value in self.states.items()]

    def clear_legacy_batch(self, channel_id, key):
        if self.states.get(channel_id, {}).get("key") == key:
            self.states[channel_id] = None

    def save_active_batch(self, channel_id, batch):
        self.states[channel_id] = batch
        self.created_batches.append(batch)

    def update_active_batch(self, channel_id, batch):
        self.states[channel_id] = batch

    def update_batch_state(self, channel_id, batch, state, **values):
        value = {**batch, **values, "state": state}
        self.states[channel_id] = value
        return value

    def set_batch_ticket(self, _key, _ticket_id):
        pass

    def save_batch_outcomes(self, _key, outcomes):
        self.saved_audit_outcomes.append([dict(row) for row in outcomes])

    def complete_detector_batch(self, channel_id, batch, _outcomes):
        for item in batch["items"]:
            row = self.work[(channel_id, item["message_id"])]
            if row["revision"] == item["revision"]:
                row["state"] = "terminal"
        self.states[channel_id] = None

    def current_work_revision(self, channel_id, message_id):
        row = self.work.get((channel_id, message_id))
        return row["revision"] if row else None

    def set_candidate_revision(self, context, _tokens, revision):
        self.signals[context.message_id] = revision
        return True

    def get_signal(self, message_id):
        return SimpleNamespace(channel_id=2, message_id=message_id) if message_id in self.signals else None

    def record_analysis(self, context, result, _window, _revision):
        self.analyses.append((context.message_id, result.classification))
        return SimpleNamespace(incident_id=1, should_ping_moderators=True, message_count=1)

    def mark_deleted(self, _message_id):
        return True


class Reactions:
    def __init__(self):
        self.events = []

    async def add(self, message):
        self.events.append(("add", message.id))

    async def remove(self, message):
        self.events.append(("remove", message.id))


class Analyzer:
    def __init__(self, classification="no_violation"):
        self.classification = classification
        self.active, self.max_active, self.tickets = 0, 0, 0

    async def classify_batch(self, payload, _key, **_kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.tickets += 1
        try:
            result = [{"index": index, "classification": self.classification,
                       "reason_code": "informal_imperative" if self.classification == "direct_familiar_address" else "other",
                       "evidence": "глянь" if self.classification == "direct_familiar_address" else None}
                      for index in payload["classify_indexes"]]
            return result, {"ticket_id": self.tickets, "queue_wait_ms": 1, "backend_ms": 2, "total_ms": 3}
        finally:
            self.active -= 1


def message(message_id, content, *, author_id=3, reply=None):
    async def history(**_kwargs):
        if False:
            yield None
    channel = SimpleNamespace(id=2, history=history)
    return SimpleNamespace(id=message_id, content=content, guild=SimpleNamespace(id=1), channel=channel,
                           author=SimpleNamespace(id=author_id, display_name="User", bot=False),
                           mentions=[], reference=SimpleNamespace(message_id=reply, resolved=None) if reply else None,
                           created_at=datetime.now(timezone.utc), jump_url=f"https://discord.com/channels/1/2/{message_id}",
                           type=None, webhook_id=None)


def cfg(**values):
    defaults = {"enabled": True, "monitored_channel_ids": {2}, "llm_enabled": True,
                "llm_idle_debounce_seconds": 0.5, "llm_hard_batch_delay_seconds": 2.0}
    return DetectorConfig(**(defaults | values))


def test_unified_mode_sends_clean_and_lexical_edge_cases_to_classifier(monkeypatch):
    monkeypatch.setattr(runtime_module, "active_legacy_detector_tickets", lambda: [])
    store, analyzer, reactions, signals = Store(), Analyzer(), Reactions(), []
    messages = [message(10, "`ты`"), message(11, "> ты где?"), message(12, "Фраза ты считается фамильярной?")]
    async def scenario():
        runtime = AddressDetectorRuntime(cfg(), reactions, analyzer=analyzer, store=store,
                                         resolve_message=lambda _c, mid: asyncio.sleep(0, result=next(m for m in messages if m.id == mid)),
                                         on_signal=lambda *args: signals.append(args))
        for item in messages:
            assert await runtime.handle_message(item)
        await runtime.start()
        await asyncio.wait_for(runtime.join(), 5)
        await runtime.stop()
    asyncio.run(scenario())
    assert analyzer.tickets == 1
    assert len(store.work) == 3
    assert store.analyses == []
    assert signals == [] and reactions.events == []


def test_direct_result_promotes_only_moderator_relevant_message(monkeypatch):
    monkeypatch.setattr(runtime_module, "active_legacy_detector_tickets", lambda: [])
    item, store, analyzer, reactions, signals = message(31, "глянь журнал"), Store(), Analyzer("direct_familiar_address"), Reactions(), []
    def on_signal(context, result, _record):
        assert store.states[2]["action_intents"]["0"].endswith(":31:1")
        assert store.saved_audit_outcomes[-1][0]["action"] == "moderator_signal_pending"
        signals.append((context.message_id, result.verdict))
    async def scenario():
        runtime = AddressDetectorRuntime(cfg(), reactions, analyzer=analyzer, store=store,
                                         resolve_message=lambda _c, _mid: asyncio.sleep(0, result=item),
                                         on_signal=on_signal)
        await runtime.start(); await runtime.handle_message(item); await runtime.join(); await runtime.stop()
    asyncio.run(scenario())
    assert store.analyses == [(31, "direct_familiar_address")]
    assert reactions.events == [("add", 31)]
    assert signals == [(31, Verdict.LIKELY_VIOLATION)]


def test_one_mixed_batch_applies_three_direct_actions_and_five_non_direct_results(monkeypatch):
    monkeypatch.setattr(runtime_module, "active_legacy_detector_tickets", lambda: [])
    texts = [
        "Ты можешь посмотреть третий раздел?", "Ты терпила", "Определитесь уже",
        "Спасибо, я придержу вам место", "по моему",
        "Он написал: «ты можешь посмотреть третий раздел?»",
        "Фраза «ты можешь посмотреть третий раздел?» считается фамильярной?",
        "Глянь третий раздел",
    ]
    proposals = [
        ("direct_familiar_address", "informal_pronoun", "Ты"),
        ("direct_familiar_address", "informal_pronoun", "Ты"),
        ("direct_familiar_address", "informal_imperative", "Определитесь"),
        ("direct_familiar_address", "informal_pronoun", "вам"),
        ("no_violation", "other", None),
        ("quoted_or_reported", "quoted_content", None),
        ("direct_familiar_address", "informal_pronoun", "ты"),
        ("direct_familiar_address", "informal_imperative", "Глянь"),
    ]
    messages = [message(100 + i, text) for i, text in enumerate(texts)]
    by_id = {item.id: item for item in messages}
    store, reactions, signals = Store(), Reactions(), []
    runtimes = []

    class MixedAnalyzer:
        async def classify_batch(self, payload, _key, **_kwargs):
            assert payload["classify_indexes"] == list(range(8))
            return ([{"index": i, "classification": value, "reason_code": reason, "evidence": evidence}
                     for i, (value, reason, evidence) in enumerate(proposals)],
                    {"ticket_id": 99, "queue_wait_ms": 1, "backend_ms": 2, "total_ms": 3})

    async def scenario():
        runtime = AddressDetectorRuntime(
            cfg(), reactions, analyzer=MixedAnalyzer(), store=store,
            resolve_message=lambda _c, mid: asyncio.sleep(0, result=by_id.get(mid)),
            on_signal=lambda context, *_: signals.append(context.message_id),
        )
        runtimes.append(runtime)
        for item in messages:
            await runtime.handle_message(item)
        await runtime.start()
        await runtime.join()
        await runtime.stop()

    asyncio.run(scenario())
    direct_ids = [100, 101, 107]
    assert len(store.created_batches) == 1
    assert store.analyses == [
        (100, "direct_familiar_address"), (101, "direct_familiar_address"),
        (102, "ambiguous"), (103, "ambiguous"), (106, "ambiguous"),
        (107, "direct_familiar_address"),
    ]
    assert reactions.events == [("add", item) for item in direct_ids]
    assert signals == direct_ids
    assert runtimes[0].status()["counters"]["direct_evidence_verified"] == 3
    assert runtimes[0].status()["counters"]["direct_evidence_rejected"] == 3
    outcomes = store.saved_audit_outcomes[-1]
    direct_outcomes = [row for row in outcomes if row["action"] == "moderator_signal"]
    assert {row["message_id"] for row in direct_outcomes} == set(direct_ids)
    assert {row["message_id"] for row in outcomes if row["action"] != "moderator_signal"} == set(range(102, 107))
    rejected = [row for row in outcomes if row.get("status") == "direct_evidence_rejected"]
    assert {row["message_id"] for row in rejected} == {102, 103, 106}
    assert all(row["model_classification"] == "direct_familiar_address" for row in rejected)
    assert all(row["action_eligible"] is False for row in rejected)
    assert all(row["evidence_validation"] == "rejected" for row in rejected)
    assert {row["evidence"] for row in rejected} == {"Определитесь", "вам", "ты"}
    assert len({row["action_token"] for row in direct_outcomes}) == 3
    assert all(row["action_token"].endswith(f":{row['message_id']}:1") for row in direct_outcomes)


def test_scheduler_never_classifies_durable_work_from_signal_channel(monkeypatch):
    monkeypatch.setattr(runtime_module, "active_legacy_detector_tickets", lambda: [])
    store, analyzer = Store(), Analyzer()
    store.put_message_work(2, 32, 3, "обычный текст", None, {})

    async def scenario():
        runtime = AddressDetectorRuntime(
            cfg(signals_channel_id="2"), Reactions(), analyzer=analyzer, store=store,
        )
        await runtime.start()
        await asyncio.sleep(0.05)
        await runtime.stop()

    asyncio.run(scenario())
    assert analyzer.tickets == 0
    assert store.created_batches == []
    assert store.work[(2, 32)]["state"] == "pending"


def test_101_messages_drain_as_13_semantic_batches_and_one_active_ticket(monkeypatch):
    monkeypatch.setattr(runtime_module, "active_legacy_detector_tickets", lambda: [])
    store, analyzer = Store(), Analyzer()
    messages = [message(1000 + i, f"ordinary {i}", author_id=i + 1) for i in range(101)]
    resolve = {item.id: item for item in messages}
    async def scenario():
        runtime = AddressDetectorRuntime(cfg(), Reactions(), analyzer=analyzer, store=store,
                                         resolve_message=lambda _c, mid: asyncio.sleep(0, result=resolve.get(mid)))
        for item in messages:
            await runtime.handle_message(item)
        await runtime.start()
        await runtime.join(); await runtime.stop()
    asyncio.run(scenario())
    assert analyzer.tickets == 13
    assert analyzer.max_active == 1
    assert [len(batch["items"]) for batch in store.created_batches] == [8] * 12 + [5]


def test_message_edit_creates_revision_even_after_initial_order_would_be_past(monkeypatch):
    monkeypatch.setattr(runtime_module, "active_legacy_detector_tickets", lambda: [])
    store, analyzer, item = Store(), Analyzer(), message(55, "first")
    async def scenario():
        runtime = AddressDetectorRuntime(cfg(), Reactions(), analyzer=analyzer, store=store,
                                         resolve_message=lambda _c, _mid: asyncio.sleep(0, result=item))
        await runtime.handle_message(item)
        item.content = "edited after watermark"
        await runtime.handle_edit(item)
        await runtime.start()
        await runtime.join(); await runtime.stop()
    asyncio.run(scenario())
    assert store.work[(2, 55)]["revision"] == 2
    assert analyzer.tickets == 1
    assert store.created_batches[0]["items"][0]["revision"] == 2


def test_upgrade_cancels_and_requeues_legacy_background_ticket_without_old_batch(monkeypatch):
    tickets = [{"ticket_id": 91, "client_id": "address_detector_background",
                "idempotency_key": "legacy-batch", "status": "queued"}]
    monkeypatch.setattr(runtime_module, "active_legacy_detector_tickets", lambda: list(tickets))
    monkeypatch.setattr(runtime_module, "cancel_llm_ticket", lambda ticket_id: tickets.clear() or ticket_id == 91)
    store, item = Store(), message(90, "ordinary message")
    store.states[2] = {"key": "legacy-batch", "channel_id": 2,
                       "items": [{"message_id": 90, "revision": 1}]}
    async def scenario():
        runtime = AddressDetectorRuntime(cfg(), Reactions(), analyzer=Analyzer(), store=store,
                                         resolve_message=lambda _c, _mid: asyncio.sleep(0, result=item))
        await runtime._reconcile_legacy_tickets()
    asyncio.run(scenario())
    assert store.states[2] is None
    assert store.work[(2, 90)]["state"] == "pending"
    assert store.work[(2, 90)]["revision"] == 1
    assert tickets == []


def test_legacy_ticket_is_cancelled_but_not_requeued_when_unified_mode_is_disabled(monkeypatch):
    tickets = [{"ticket_id": 92, "client_id": "address_detector_background",
                "idempotency_key": "legacy-batch", "status": "running"}]
    monkeypatch.setattr(runtime_module, "active_legacy_detector_tickets", lambda: list(tickets))
    monkeypatch.setattr(runtime_module, "cancel_llm_ticket", lambda ticket_id: tickets.clear() or ticket_id == 92)
    store = Store()
    store.states[2] = {"key": "legacy-batch", "channel_id": 2,
                       "items": [{"message_id": 90, "revision": 1}]}

    async def scenario():
        runtime = AddressDetectorRuntime(
            cfg(llm_enabled=False), Reactions(), analyzer=Analyzer(), store=store,
            resolve_message=lambda *_args: asyncio.sleep(0, result=None),
        )
        await runtime._reconcile_legacy_tickets(requeue=False)

    asyncio.run(scenario())
    assert store.states[2] is None
    assert store.work == {}
    assert tickets == []


def test_restart_resumes_saved_batch_without_building_a_duplicate(monkeypatch):
    monkeypatch.setattr(runtime_module, "active_legacy_detector_tickets", lambda: [])
    item, store, analyzer, reactions = message(93, "Ты можешь посмотреть третий раздел?"), Store(), Analyzer(), Reactions()
    store.put_message_work(2, 93, 3, item.content, None, {"created_at": None})
    batch = {"key": "address_detector:saved", "channel_id": 2, "state": "submitted",
             "payload": {"classify_indexes": [0]},
             "items": [{"message_id": 93, "revision": 1, "index": 0, "content": item.content,
                        "author_id": 3, "reply_target_id": None, "payload": {}}],
             "attempt": 0, "ticket_id": 44, "applied_indexes": []}
    store.states[2] = batch

    async def scenario():
        runtime = AddressDetectorRuntime(cfg(), reactions, analyzer=analyzer, store=store,
                                         resolve_message=lambda _c, _mid: asyncio.sleep(0, result=item))
        await runtime.start()
        await asyncio.wait_for(runtime.join(), 5)
        await runtime.stop()

    asyncio.run(scenario())
    assert analyzer.tickets == 1
    assert store.created_batches == []
    assert store.states[2] is None
    assert reactions.events == []


def test_scheduler_selects_pending_channels_round_robin():
    from discord_bot.address_detector.scheduler import AddressDetectorScheduler

    scheduler = AddressDetectorScheduler(None, None, None, None, None)
    rows = [{"channel_id": channel_id} for channel_id in (1, 2, 3)]

    assert [scheduler._next_channel_id(rows) for _ in range(7)] == [1, 2, 3, 1, 2, 3, 1]


def test_scheduler_does_not_lose_wake_that_arrives_before_wait():
    from discord_bot.address_detector.scheduler import AddressDetectorScheduler

    async def scenario():
        scheduler = AddressDetectorScheduler(None, None, None, None, None)
        observed = scheduler._wake_version
        scheduler.wake()
        started = asyncio.get_running_loop().time()
        await scheduler._wait(60, observed)
        return asyncio.get_running_loop().time() - started

    assert asyncio.run(scenario()) < 0.1


class TimedWorkStore:
    def __init__(self):
        self.items = []
        self.flushed = asyncio.Event()
        self.loop = None
        self.prepared_at = None

    def enqueue(self, message_id):
        self.items.append({"channel_id": 2, "message_id": message_id, "revision": 1,
                           "event_order": message_id, "created": time.monotonic()})

    def pending_work(self):
        now = time.monotonic()
        return [{**item, "age_seconds": now - item["created"]} for item in self.items]

    def detector_channel_states(self):
        return []

    def save_active_batch(self, _channel_id, batch):
        self.prepared_at = time.monotonic()
        selected = {item["message_id"] for item in batch["items"]}
        self.items = [item for item in self.items if item["message_id"] not in selected]
        self.loop.call_soon_threadsafe(self.flushed.set)


def _run_timed_scheduler(arrival_interval=None):
    from discord_bot.address_detector.scheduler import AddressDetectorScheduler

    async def scenario():
        store = TimedWorkStore()
        store.loop = asyncio.get_running_loop()
        store.enqueue(1)
        config = SimpleNamespace(llm_idle_debounce_seconds=0.5, llm_hard_batch_delay_seconds=2.0,
                                 llm_max_new_messages=8,
                                 scope=SimpleNamespace(monitored_channel_ids={2}, excluded_channel_ids=set()))

        async def prepare(_channel_id, items):
            return {"items": items}

        scheduler = AddressDetectorScheduler(config, store, prepare, lambda *_: None, lambda *_: None)
        started = time.monotonic()
        await scheduler.start()
        try:
            if arrival_interval is not None:
                for message_id in range(2, 9):
                    await asyncio.sleep(arrival_interval)
                    store.enqueue(message_id)
                    scheduler.wake()
            await asyncio.wait_for(store.flushed.wait(), timeout=1.2 if arrival_interval is None else 1.0)
            return store.prepared_at - started
        finally:
            await scheduler.stop()

    return asyncio.run(scenario())


def test_single_message_flushes_after_idle_debounce():
    elapsed = _run_timed_scheduler()
    assert 0.4 <= elapsed < 0.8


def test_continuous_arrivals_flush_at_hard_deadline():
    elapsed = _run_timed_scheduler(arrival_interval=0.25)
    assert 1.8 <= elapsed < 2.4


def test_terminal_result_persistence_error_keeps_batch_active(monkeypatch):
    store, analyzer = Store(), Analyzer("direct_familiar_address")
    item = message(77, "глянь сюда")
    batch = {
        "key": "stable-batch", "channel_id": 2, "state": "prepared", "payload": {"classify_indexes": [0]},
        "items": [{"message_id": 77, "revision": 1, "index": 0, "content": item.content,
                   "author_id": 3, "reply_target_id": None, "payload": {}}],
        "attempt": 0, "ticket_id": None, "applied_indexes": [],
    }
    store.states[2] = batch
    update = store.update_batch_state

    def fail_terminal(_channel_id, _batch, state, **values):
        if state == "terminal_received":
            raise OSError("database unavailable")
        return update(_channel_id, _batch, state, **values)

    monkeypatch.setattr(store, "update_batch_state", fail_terminal)

    async def scenario():
        runtime = AddressDetectorRuntime(cfg(), Reactions(), analyzer=analyzer, store=store,
                                         resolve_message=lambda _c, _mid: asyncio.sleep(0, result=item))
        return await runtime._process_batch(2, batch), runtime

    keep_waiting, runtime = asyncio.run(scenario())
    assert keep_waiting is True
    assert store.states[2]["state"] == "submitted"
    assert store.work == {}
    assert runtime.metrics["terminal_failures"] == 0


def test_message_resolution_outage_keeps_terminal_batch_for_retry():
    store = Store()
    batch = {
        "key": "stable-batch", "channel_id": 2, "state": "terminal_received",
        "payload": {"classify_indexes": [0]}, "results": [{"index": 0,
        "classification": "direct_familiar_address", "reason_code": "informal_imperative"}],
        "timing": {"ticket_id": 44, "queue_wait_ms": 0, "backend_ms": 1, "total_ms": 2},
        "items": [{"message_id": 77, "revision": 1, "index": 0, "content": "глянь сюда",
                   "author_id": 3, "reply_target_id": None, "payload": {}}],
        "applied_indexes": [],
    }
    store.states[2] = batch
    store.work[(2, 77)] = {"channel_id": 2, "message_id": 77, "revision": 1,
                           "content": "глянь сюда", "state": "pending"}

    async def unavailable(*_args):
        raise OSError("Discord history unavailable")

    async def scenario():
        runtime = AddressDetectorRuntime(cfg(), Reactions(), analyzer=Analyzer(), store=store,
                                         resolve_message=unavailable)
        try:
            await runtime._process_batch(2, batch)
        except OSError:
            return
        raise AssertionError("resolution outage should keep the result pending")

    asyncio.run(scenario())
    assert store.states[2]["state"] == "terminal_received"
    assert store.states[2]["applied_indexes"] == []


def test_missing_new_message_still_reserves_its_reply_target_payload_slot():
    item = {"message_id": 200, "revision": 1, "author_id": 3, "content": "ты где?",
            "reply_target_id": 100, "event_order": 200,
            "payload": {"created_at": None, "mentions": [], "reply_target": None}}

    async def scenario():
        runtime = AddressDetectorRuntime(cfg(), Reactions(), analyzer=Analyzer(), store=Store(),
                                         resolve_message=lambda *_args: asyncio.sleep(0, result=None))
        return await runtime._prepare_batch(2, [item])

    batch = asyncio.run(scenario())
    assert len(batch["payload"]["messages"]) == 2
    assert batch["payload"]["messages"][1]["reply_to"] == 0
