"""Bounded, cross-event-loop event bus for the Discord viewer.

The Discord bot and FastAPI do not share an event loop.  An asyncio.Queue
would therefore be the wrong primitive here: it is not safe to fill from the
Gateway loop and drain from a browser WebSocket loop.  The registry is guarded
by a small thread lock, while each subscription owns its queue on its web
event loop and receives publications through call_soon_threadsafe().
"""

import asyncio
import threading
from collections import deque
from typing import Any


MAX_QUEUED_EVENTS = 128


class ViewerSubscription:
    def __init__(self, channel_id: int, loop: asyncio.AbstractEventLoop):
        self.channel_id = int(channel_id)
        self.loop = loop
        self._queue: deque[dict[str, Any]] = deque()
        self._wakeup = asyncio.Event()
        self._closed = False

    def enqueue(self, event: dict[str, Any]) -> None:
        """Run on this subscription's event loop only."""
        if self._closed:
            return
        if len(self._queue) >= MAX_QUEUED_EVENTS:
            # Once the browser misses an event, replaying a partial stream is
            # worse than one cheap history refetch.  Keep the queue bounded and
            # make the client repair its view from Discord history.
            self._queue.clear()
            self._queue.append({"type": "resync", "channel_id": str(self.channel_id)})
        else:
            self._queue.append(event)
        self._wakeup.set()

    async def next_event(self) -> dict[str, Any]:
        while not self._queue:
            if self._closed:
                raise RuntimeError("viewer subscription is closed")
            await self._wakeup.wait()
            self._wakeup.clear()
        event = self._queue.popleft()
        if not self._queue:
            self._wakeup.clear()
        return event

    def close(self) -> None:
        self._closed = True
        self._queue.clear()
        self._wakeup.set()


_lock = threading.Lock()
_subscriptions: set[ViewerSubscription] = set()


def subscribe(channel_id: int, loop: asyncio.AbstractEventLoop) -> ViewerSubscription:
    subscription = ViewerSubscription(channel_id, loop)
    with _lock:
        _subscriptions.add(subscription)
    return subscription


def unsubscribe(subscription: ViewerSubscription) -> None:
    with _lock:
        _subscriptions.discard(subscription)
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if current_loop is subscription.loop:
        subscription.close()
        return
    try:
        subscription.loop.call_soon_threadsafe(subscription.close)
    except RuntimeError:
        # The loop is already gone; only touch the scalar flag from this
        # thread.  Queue/Event state belongs to the dead loop and must not be
        # mutated here.
        subscription._closed = True


def has_subscribers(channel_id: int) -> bool:
    channel_id = int(channel_id)
    with _lock:
        return any(sub.channel_id == channel_id and not sub._closed for sub in _subscriptions)


def publish(channel_id: int, event: dict[str, Any]) -> int:
    """Schedule an event for active viewers and return scheduled count.

    The caller is normally the Discord Gateway loop.  No message serialization
    should happen before checking this count; that is the important hot-path
    optimization for a bot that handles every guild message.
    """
    channel_id = int(channel_id)
    with _lock:
        targets = [
            sub for sub in _subscriptions
            if sub.channel_id == channel_id and not sub._closed
        ]
    for subscription in targets:
        try:
            subscription.loop.call_soon_threadsafe(subscription.enqueue, event)
        except RuntimeError:
            # The browser connection's loop may have just shut down.  Remove
            # it lazily; the WebSocket finally block normally handles this.
            unsubscribe(subscription)
    return len(targets)
