"""In-process pub/sub for pushing agent events to connected clients."""

import asyncio
import threading


class EventHub:
    def __init__(self) -> None:
        self._queues: dict[int, set[asyncio.Queue]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, user_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._queues.setdefault(user_id, set()).add(queue)
        return queue

    def unsubscribe(self, user_id: int, queue: asyncio.Queue) -> None:
        with self._lock:
            queues = self._queues.get(user_id)
            if not queues:
                return
            queues.discard(queue)
            if not queues:
                self._queues.pop(user_id, None)

    def publish(self, user_id: int, event: dict) -> None:
        loop = self._loop
        if loop is None:
            return
        with self._lock:
            queues = list(self._queues.get(user_id, ()))
        for queue in queues:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass


hub = EventHub()