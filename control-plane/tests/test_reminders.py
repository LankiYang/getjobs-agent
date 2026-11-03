"""Tests for durable, isolated one-shot work-item reminders."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, reminders, work_items


class RemindersTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = patch.object(db, "CONTROL_DB", Path(self.directory.name) / "control.db")
        self.db_path.start()
        self.addCleanup(self.db_path.stop)
        self.addCleanup(reminders.stop)
        db.init_db()
        with db.get_conn() as conn:
            reminders.init_db(conn)
            reminders.init_db(conn)
        self.publish = patch.object(reminders.hub, "publish")
        self.mock_publish = self.publish.start()
        self.addCleanup(self.publish.stop)

    def item(self, user=1, kind="task", fields=None):
        return work_items.execute(user, kind, "create", {"fields": fields or {"name": "面试"}})["item"]

    def set_reminder(self, item, user=1, due="2000-01-01T08:00:00+08:00"):
        return reminders.execute(user, "set", {"kind": item["kind"], "item_id": item["id"], "due_at": due})

    def test_fire_persists_snapshot_offline_and_marks_read(self):
        item = self.item(fields={"name": "面试"})
        result = self.set_reminder(item)
        self.assertEqual(result["reminder"]["due_at"], "2000-01-01T00:00:00.000000+00:00")
        reminders._process_due()
        reminders._process_due()
        page = reminders.execute(1, "notifications", {})
        self.assertEqual(page["total"], 1)
        notice = page["notifications"][0]
        self.assertEqual(notice["fields"], {"name": "面试"})
        self.assertIsNone(notice["read_at"])
        self.mock_publish.assert_called_once_with(1, {"type": "reminder", "notification": notice})
        self.assertEqual(reminders.execute(1, "list", {})["reminders"], [])
        self.assertEqual(reminders.execute(2, "notifications", {})["total"], 0)
        self.assertFalse(reminders.execute(2, "mark_read", {"id": notice["id"]})["ok"])
        read = reminders.execute(1, "mark_read", {"id": notice["id"]})["notification"]
        self.assertIsNotNone(read["read_at"])
        self.assertEqual(reminders.execute(1, "mark_read", {"id": notice["id"]})["notification"], read)
        self.assertEqual(reminders.execute(1, "notifications", {"unread_only": True})["total"], 0)
        work_items.execute(1, "task", "delete", {"id": item["id"]})
        self.assertEqual(reminders.execute(1, "notifications", {})["notifications"][0], read)

    def test_replace_cancel_and_item_isolation(self):
        first, other = self.item(), self.item(2, "event")
        self.assertTrue(self.set_reminder(first)["ok"])
        self.assertTrue(self.set_reminder(first, due="2999-01-01T00:00:00Z")["ok"])
        self.assertFalse(self.set_reminder(first, 2)["ok"])
        self.assertFalse(reminders.execute(1, "set", {"kind": "event", "item_id": first["id"],
                                                      "due_at": "2000-01-01T00:00:00Z"})["ok"])
        self.assertTrue(self.set_reminder(other, 2)["ok"])
        self.assertFalse(reminders.execute(1, "cancel", {"kind": "event", "item_id": other["id"]})["ok"])
        reminders._process_due()
        self.assertEqual(reminders.execute(1, "notifications", {})["total"], 0)
        self.assertEqual(reminders.execute(2, "notifications", {})["total"], 1)
        self.assertTrue(reminders.execute(1, "cancel", {"kind": "task", "item_id": first["id"]})["ok"])
        self.assertFalse(reminders.execute(1, "cancel", {"kind": "task", "item_id": first["id"]})["ok"])

    def test_deleted_item_is_not_notified(self):
        item = self.item()
        self.set_reminder(item)
        work_items.execute(1, "task", "delete", {"id": item["id"]})
        self.assertEqual(reminders.execute(1, "list", {})["reminders"], [])
        reminders._process_due()
        self.assertEqual(reminders.execute(1, "notifications", {})["total"], 0)
        self.mock_publish.assert_not_called()

    def test_bad_inputs_do_not_mutate(self):
        item = self.item()
        for due in (None, "2026-01-01", "2026-01-01T12:00:00", "bad", 123):
            with self.subTest(due=due):
                self.assertFalse(self.set_reminder(item, due=due)["ok"])
        for user, action, data in [(True, "list", {}), (1, "bad", {}), (1, "list", {"extra": 1}),
                                   (1, "set", {"kind": "task", "item_id": True, "due_at": "2000-01-01T00:00:00Z"}),
                                   (1, "mark_read", {"id": True}), (1, "notifications", {"limit": 101}),
                                   (1, "notifications", {"offset": -1}), (1, "notifications", {"unread_only": 1})]:
            with self.subTest(action=action, data=data):
                self.assertFalse(reminders.execute(user, action, data)["ok"])
        self.assertEqual(reminders.execute(1, "list", {})["reminders"], [])

    def test_pagination_and_publish_failure_preserves_notification(self):
        self.mock_publish.side_effect = RuntimeError("offline")
        for _ in range(3):
            self.set_reminder(self.item())
        reminders._process_due()
        page = reminders.execute(1, "notifications", {"limit": 1, "offset": 1})
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["notifications"]), 1)
        self.assertEqual(self.mock_publish.call_count, 3)

    def test_competing_workers_only_create_one_notification(self):
        self.set_reminder(self.item())
        barrier = threading.Barrier(3)
        errors = []
        def process():
            barrier.wait()
            try:
                reminders._process_due()
            except Exception as exc:
                errors.append(exc)
        workers = [threading.Thread(target=process) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join()
        self.assertEqual(errors, [])
        self.assertEqual(reminders.execute(1, "notifications", {})["total"], 1)
        self.mock_publish.assert_called_once()

    def test_start_initializes_and_poller_survives_restart(self):
        item = self.item()
        with db.get_conn() as conn:
            conn.execute("DROP TABLE reminder_notifications")
            conn.execute("DROP TABLE reminders")
        reminders.start()
        reminders.start()
        self.set_reminder(item, due="2999-01-01T00:00:00Z")
        reminders.stop()
        self.set_reminder(item)
        # Persisted overdue reminders are processed by the next poll after restart.
        with patch.object(reminders, "POLL_SECONDS", 0.01):
            reminders.start()
            for _ in range(200):
                if reminders.execute(1, "notifications", {})["total"]:
                    break
                threading.Event().wait(0.01)
            reminders.stop()
        self.assertEqual(reminders.execute(1, "notifications", {})["total"], 1)


if __name__ == "__main__":
    unittest.main()
