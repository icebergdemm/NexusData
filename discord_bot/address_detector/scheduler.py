from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("NexusBot.address_detector")


class AddressDetectorScheduler:
    """One durable scheduler loop; a prepared batch survives process restarts."""

    def __init__(self, config, store, prepare_batch, process_batch, on_event):
        self.config, self.store = config, store
        self.prepare_batch, self.process_batch, self.on_event = prepare_batch, process_batch, on_event
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._last_channel_id: int | None = None
        self._wake_version = 0

    async def start(self):
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="address-detector-scheduler")
        self._wake.set()

    async def stop(self):
        self._stopping = True
        self._wake.set()
        if self._task:
            await self._task
            self._task = None

    def wake(self):
        self._wake_version += 1
        self._wake.set()

    async def _run(self):
        while not self._stopping:
            wake_version = self._wake_version
            try:
                active, work = await asyncio.gather(
                    asyncio.to_thread(self.store.detector_channel_states),
                    asyncio.to_thread(self.store.pending_work),
                )
                active = [row for row in active if row["pending_batch"]]
                if active:
                    channel_id = self._next_channel_id(active)
                    row = next(row for row in active if int(row["channel_id"]) == channel_id)
                    keep_waiting = await self.process_batch(row["channel_id"], row["pending_batch"])
                    if keep_waiting:
                        await self._wait(2, wake_version)
                    continue
                if work:
                    channel_id = self._next_channel_id(work)
                    channel = [item for item in work if item["channel_id"] == channel_id]
                    ages = [item["age_seconds"] for item in channel]
                    newest_age = min(ages)
                    oldest_age = max(ages)
                    delay = min(
                        max(0.0, self.config.llm_idle_debounce_seconds - newest_age),
                        max(0.0, self.config.llm_hard_batch_delay_seconds - oldest_age),
                    )
                    if delay:
                        await self._wait(delay, wake_version)
                        continue
                    # Keep only the newest durable revision of each message.
                    latest = {}
                    for item in channel:
                        old = latest.get(item["message_id"])
                        if old is None or item["revision"] > old["revision"]:
                            latest[item["message_id"]] = item
                    selected = sorted(latest.values(), key=lambda item: item["event_order"])[:self.config.llm_max_new_messages]
                    batch = await self.prepare_batch(channel_id, selected)
                    await asyncio.to_thread(self.store.save_active_batch, channel_id, batch)
                    continue
                await self._wait(60, wake_version)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("address_detector scheduler iteration failed")
                self.on_event("scheduler_error", type(exc).__name__)
                await self._wait(2, wake_version)

    def _next_channel_id(self, rows):
        channel_ids = sorted({int(row["channel_id"]) for row in rows})
        channel_id = next(
            (value for value in channel_ids if self._last_channel_id is None or value > self._last_channel_id),
            channel_ids[0],
        )
        self._last_channel_id = channel_id
        return channel_id

    async def _wait(self, seconds, wake_version):
        if self._stopping:
            return
        self._wake.clear()
        if self._stopping or self._wake_version != wake_version:
            return
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
