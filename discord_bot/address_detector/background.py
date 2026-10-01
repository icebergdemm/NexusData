from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field

from .scope import in_scope


@dataclass
class _ChannelBuffer:
    watermark: int = 0
    messages: dict[int, object] = field(default_factory=dict)
    timer: asyncio.Task | None = None
    in_flight: bool = False


class AddressDetectorBackgroundScanner:
    """Per-channel bounded debounce; durable ticket state is owned by on_batch."""

    def __init__(self, config, store, on_batch, on_event, *, on_metric=None, load_recent=None) -> None:
        self.config = config
        self.store = store
        self.on_batch = on_batch
        self.on_event = on_event
        self.on_metric = on_metric
        self.load_recent = load_recent
        self._channels: dict[int, _ChannelBuffer] = {}
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        for row in await asyncio.to_thread(self.store.background_states):
            channel_id = int(row["channel_id"])
            if channel_id not in self.config.scope.monitored_channel_ids or channel_id in self.config.scope.excluded_channel_ids:
                continue
            state = self._channels.setdefault(channel_id, _ChannelBuffer())
            state.watermark = int(row.get("last_message_id") or 0)
            pending = row.get("pending_batch")
            if pending:
                state.in_flight = True
                self._track(self._process(channel_id, [], pending))
            if self.load_recent:
                after_id = int((pending or {}).get("last_message_id") or state.watermark)
                try:
                    recent = await self.load_recent(channel_id, after_id, self.config.llm_background_max_messages * 4)
                except Exception:
                    self._event("background_catchup_failed", "history_unavailable")
                    continue
                for message in sorted(recent, key=lambda item: int(getattr(item, "id", 0))):
                    message_id = int(getattr(message, "id", 0))
                    if message_id > after_id and in_scope(message, self.config.scope):
                        state.messages[message_id] = message
                while len(state.messages) > self.config.llm_background_max_messages:
                    state.messages.pop(min(state.messages))
                    self._metric("dropped_superseded_background_batches")
                if state.messages and (state.timer is None or state.timer.done()):
                    state.timer = asyncio.create_task(self._debounce(channel_id), name=f"address-detector-debounce-{channel_id}")
                    self._track(state.timer)

    async def stop(self) -> None:
        for state in self._channels.values():
            if state.timer:
                state.timer.cancel()
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def observe(self, message: object, *, force: bool = False) -> None:
        if not self.config.llm_background_enabled:
            return
        channel_id = int(getattr(getattr(message, "channel", None), "id", 0))
        message_id = int(getattr(message, "id", 0))
        if not channel_id or not message_id:
            return
        state = self._channels.setdefault(channel_id, _ChannelBuffer())
        if message_id <= state.watermark and not force:
            self._metric("background_duplicate_observation")
            return
        if message_id not in state.messages and state.messages:
            self._metric("coalesced_messages")
        state.messages[message_id] = message
        while len(state.messages) > self.config.llm_background_max_messages:
            state.messages.pop(min(state.messages))
            self._metric("dropped_superseded_background_batches")
        if state.timer is None or state.timer.done():
            state.timer = asyncio.create_task(self._debounce(channel_id), name=f"address-detector-debounce-{channel_id}")
            self._track(state.timer)

    async def _debounce(self, channel_id: int) -> None:
        try:
            await asyncio.sleep(self.config.llm_background_debounce_seconds)
            state = self._channels[channel_id]
            if state.in_flight or not state.messages:
                return
            messages = [state.messages[key] for key in sorted(state.messages)]
            state.messages.clear()
            state.in_flight = True
            self._track(self._process(channel_id, messages, None))
        except asyncio.CancelledError:
            raise

    async def _process(self, channel_id: int, messages: list[object], pending: dict | None) -> None:
        last_message_id = int((pending or {}).get("last_message_id") or max(
            (int(getattr(message, "id", 0)) for message in messages), default=0
        ))
        try:
            callback_result = self.on_batch(channel_id, messages, pending)
            if inspect.isawaitable(callback_result):
                await callback_result
            if last_message_id:
                await asyncio.to_thread(self.store.complete_background_batch, channel_id, last_message_id)
                self._channels[channel_id].watermark = max(self._channels[channel_id].watermark, last_message_id)
            self._metric("background_batches")
            self._metric("background_messages", len(messages) or len((pending or {}).get("items", ())))
            self._event("background_batch_completed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._event("background_batch_failed", type(exc).__name__)
            if last_message_id:
                try:
                    await asyncio.to_thread(self.store.complete_background_batch, channel_id, last_message_id)
                    self._channels[channel_id].watermark = max(self._channels[channel_id].watermark, last_message_id)
                except Exception:
                    self._event("background_watermark_write_failed", type(exc).__name__)
        finally:
            state = self._channels[channel_id]
            state.in_flight = False
            state.timer = None
            if state.messages:
                state.timer = asyncio.create_task(self._debounce(channel_id), name=f"address-detector-debounce-{channel_id}")
                self._track(state.timer)

    def _track(self, task: asyncio.Task | object) -> None:
        if not isinstance(task, asyncio.Task):
            task = asyncio.create_task(task)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _metric(self, name: str, value: int = 1) -> None:
        if self.on_metric:
            self.on_metric(name, value)

    def _event(self, name: str, detail: str | None = None) -> None:
        if self.on_event:
            self.on_event(name, detail)

    def status(self) -> dict[str, int]:
        return {
            "queue_depth": sum(len(state.messages) for state in self._channels.values()),
            "in_flight": sum(state.in_flight for state in self._channels.values()),
            "channels": len(self._channels),
        }
