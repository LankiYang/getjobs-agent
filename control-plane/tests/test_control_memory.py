"""Memory-capacity and lifecycle behavior of the control plane."""
import asyncio
import gc
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app import activity, main
from app.events import EventCapacityError, EventHub


class GuardTests(unittest.TestCase):
    def test_guard_is_shared_while_referenced_and_reclaimed_afterwards(self):
        lock = activity.guard(987654)
        self.assertIs(lock, activity.guard(987654))
        del lock
        gc.collect()
        self.assertNotIn(987654, activity._guards)

    def test_sync_chat_rejects_without_starting_runner_when_full(self):
        capacity = threading.BoundedSemaphore(1)
        capacity.acquire()
        with patch.object(main, "_chat_capacity", capacity), \
             patch.object(main.runners, "ensure_running") as start:
            with self.assertRaises(HTTPException) as error:
                main.agent_chat(main.ChatRequest(message="hello"), 1)
            self.assertEqual(error.exception.status_code, 503)
            start.assert_not_called()
        capacity.release()

    def test_oversized_message_is_rejected(self):
        with patch.object(main, "_chat_message_limit", 3):
            with self.assertRaises(HTTPException) as error:
                main.agent_chat(main.ChatRequest(message="abcd"), 1)
        self.assertEqual(error.exception.status_code, 413)

    def test_event_subscriber_limits_and_release(self):
        hub = EventHub()
        hub._max_subscribers = 2
        hub._max_user_subscribers = 1
        first = hub.subscribe(1)
        with self.assertRaises(EventCapacityError):
            hub.subscribe(1)
        second = hub.subscribe(2)
        with self.assertRaises(EventCapacityError):
            hub.subscribe(3)
        hub.unsubscribe(1, first)
        third = hub.subscribe(3)
        hub.unsubscribe(2, second)
        hub.unsubscribe(3, third)



class AsyncCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_overflow_is_bounded_and_reported(self):
        hub = EventHub()
        hub._max_size = 2
        hub.set_loop(asyncio.get_running_loop())
        queue = hub.subscribe(1)
        for number in range(20):
            hub.publish(1, {"type": "message", "number": number})
        self.assertEqual(hub._pending[queue], 2)
        await asyncio.sleep(0)
        self.assertLessEqual(queue.qsize(), 2)
        self.assertIn("overflow", [queue.get_nowait()["type"] for _ in range(queue.qsize())])
        hub.unsubscribe(1, queue)
        self.assertNotIn(queue, hub._pending)

    async def test_unsubscribed_pending_events_are_discarded(self):
        hub = EventHub()
        hub.set_loop(asyncio.get_running_loop())
        queue = hub.subscribe(1)
        hub.publish(1, {"type": "message"})
        hub.unsubscribe(1, queue)
        await asyncio.sleep(0)
        self.assertTrue(queue.empty())
        self.assertNotIn(queue, hub._pending)

    async def test_oversized_chat_upload_is_bounded_and_closed(self):
        class Upload:
            closed = False

            async def read(self, size):
                self.size = size
                return b"x" * size

            async def close(self):
                self.closed = True

        upload = Upload()
        with patch.object(main.chat_files, "MAX_SIZE", 16):
            with self.assertRaises(HTTPException) as error:
                await main.upload_chat_file(upload, 1)
        self.assertEqual(error.exception.status_code, 413)
        self.assertEqual(upload.size, 17)
        self.assertTrue(upload.closed)

    async def test_upload_capacity_shared_and_released(self):
        class Upload:
            closed = False

            async def close(self):
                self.closed = True

        capacity = threading.BoundedSemaphore(1)
        first, second = Upload(), Upload()
        with patch.object(main, "_upload_capacity", capacity):
            async with main._upload_slot(first):
                with self.assertRaises(HTTPException) as error:
                    await main.upload_resume_image("boss", second, 1)
                self.assertEqual(error.exception.status_code, 503)
                self.assertTrue(second.closed)
            self.assertTrue(first.closed)
            self.assertTrue(capacity.acquire(blocking=False))
            capacity.release()

    async def test_stream_releases_capacity_when_closed_before_worker(self):
        capacity = threading.BoundedSemaphore(1)
        with patch.object(main, "_chat_capacity", capacity):
            response = await main.agent_chat_stream(main.ChatRequest(message="hello", session_id="capacity-test"), 1)
            self.assertFalse(capacity.acquire(blocking=False))
            self.assertIn('session', await response.body_iterator.__anext__())
            await response.body_iterator.aclose()
            self.assertTrue(capacity.acquire(blocking=False))
            capacity.release()
            self.assertNotIn((1, "capacity-test"), main._chat_turns)

    async def test_stream_rejects_at_capacity(self):
        capacity = threading.BoundedSemaphore(1)
        capacity.acquire()
        with patch.object(main, "_chat_capacity", capacity):
            with self.assertRaises(HTTPException) as error:
                await main.agent_chat_stream(main.ChatRequest(message="hello", session_id="full-test"), 1)
            self.assertEqual(error.exception.status_code, 503)
            self.assertNotIn((1, "full-test"), main._chat_turns)
        capacity.release()
