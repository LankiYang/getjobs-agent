"""In-process pub/sub for pushing agent events to connected clients."""

import asyncio
import os
import threading


def _positive_limit(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


class EventCapacityError(Exception):
    """No more event subscriptions can be admitted."""


class EventHub:
    def __init__(self) -> None:
        self._queues: dict[int, set[asyncio.Queue]] = {}
        self._pending: dict[asyncio.Queue, int] = {}
        self._overflow: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._max_size = _positive_limit("CONTROL_EVENT_QUEUE_SIZE", 64)
        self._max_subscribers = _positive_limit("CONTROL_MAX_EVENT_SUBSCRIBERS", 64)
        self._max_user_subscribers = _positive_limit("CONTROL_MAX_USER_EVENT_SUBSCRIBERS", 4)

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, user_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_size)
        with self._lock:
            if (len(self._pending) >= self._max_subscribers or
                    len(self._queues.get(user_id, ())) >= self._max_user_subscribers):
                raise EventCapacityError("事件连接已达上限，请关闭多余页面后重试")
            self._queues.setdefault(user_id, set()).add(queue)
            self._pending[queue] = 0
        return queue

    def unsubscribe(self, user_id: int, queue: asyncio.Queue) -> None:
        with self._lock:
            queues = self._queues.get(user_id)
            if queues:
                queues.discard(queue)
                if not queues:
                    self._queues.pop(user_id, None)
            self._pending.pop(queue, None)
            self._overflow.discard(queue)

    def _deliver(self, queue: asyncio.Queue, event: dict) -> None:
        with self._lock:
            if queue not in self._pending:
                return
            self._pending[queue] -= 1
            overflow = queue in self._overflow
            self._overflow.discard(queue)
            if overflow:
                event = {"type": "overflow", "message": "事件过多，部分更新已丢失，请刷新页面。"}
            if queue.full():
                queue.get_nowait()
                event = {"type": "overflow", "message": "事件过多，部分更新已丢失，请刷新页面。"}
            queue.put_nowait(event)

    def publish(self, user_id: int, event: dict) -> None:
        loop = self._loop
        if loop is None:
            return
        with self._lock:
            for queue in self._queues.get(user_id, ()):
                # Limit both queued items and callbacks awaiting the event loop.
                if self._pending[queue] >= self._max_size:
                    self._overflow.add(queue)
                    continue
                self._pending[queue] += 1
                try:
                    loop.call_soon_threadsafe(self._deliver, queue, event)
                except RuntimeError:
                    self._pending[queue] -= 1


hub = EventHub()
