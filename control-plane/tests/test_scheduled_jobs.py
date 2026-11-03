"""Behavioral coverage for durable application scheduling."""
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import agent, db, reminders, runners, scheduled_jobs


class ScheduledJobsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        db_patch = patch.object(db, "CONTROL_DB", Path(self.directory.name) / "control.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        self.addCleanup(scheduled_jobs.stop)
        db.init_db()
        with db.get_conn() as conn:
            scheduled_jobs.init_db(conn)
            scheduled_jobs.init_db(conn)
        self.publish = patch.object(scheduled_jobs.hub, "publish")
        self.publisher = self.publish.start()
        self.addCleanup(self.publish.stop)
        self.runner = patch.object(runners, "ensure_running")
        self.ensure = self.runner.start()
        self.addCleanup(self.runner.stop)
        self.tool = patch.object(agent, "execute_tool")
        self.execute_tool = self.tool.start()
        self.addCleanup(self.tool.stop)
        self.execute_tool.side_effect = lambda user, name, args: (
            {"isRunning": False} if name == "get_status" else {"ok": True, "status": "stopped"})

    def schedule(self, user=1, **overrides):
        data = {"operation": "start_application", "channel": "boss",
                "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
        data.update(overrides)
        return scheduled_jobs.execute(user, "schedule", data)

    def make_due(self, job):
        with db.get_conn() as conn:
            conn.execute("UPDATE scheduled_jobs SET due_at=? WHERE id=?",
                         ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="microseconds"), job["id"]))

    def test_stale_job_is_not_executed(self):
        job = self.schedule()["job"]
        with db.get_conn() as conn:
            conn.execute("UPDATE scheduled_jobs SET due_at=? WHERE id=?",
                         ((datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(timespec="microseconds"), job["id"]))
        scheduled_jobs._process_due()
        self.ensure.assert_not_called()
        self.assertEqual(scheduled_jobs.execute(1, "list", {})["jobs"][0]["status"], "failed")
        self.assertIn("未执行", reminders.execute(1, "notifications", {})["notifications"][0]["fields"]["title"])

    def test_validation_and_owner_isolation(self):
        for values in ({"due_at": "2000-01-01T00:00:00Z"}, {"due_at": "2999-01-01"},
                       {"channel": "liepin", "max_count": 1}, {"max_count": True},
                       {"max_count": 101}, {"operation": "interview"},
                       {"operation": "stop_application", "max_count": 1}):
            with self.subTest(values=values):
                self.assertFalse(self.schedule(**values)["ok"])
        job = self.schedule(max_count=2)["job"]
        self.assertEqual(scheduled_jobs.execute(2, "list", {})["jobs"], [])
        self.assertFalse(scheduled_jobs.execute(2, "cancel", {"id": job["id"]})["ok"])
        self.assertTrue(scheduled_jobs.execute(1, "cancel", {"id": job["id"]})["ok"])
        self.assertEqual(scheduled_jobs.execute(1, "list", {})["jobs"][0]["status"], "cancelled")
        self.assertFalse(scheduled_jobs.execute(1, "cancel", {"id": job["id"]})["ok"])

    def test_start_executes_once_and_notification_is_compatible(self):
        job = self.schedule(user=7, max_count=4)["job"]
        self.make_due(job)
        scheduled_jobs._process_due()
        scheduled_jobs._process_due()
        self.ensure.assert_called_once_with(7)
        self.execute_tool.assert_any_call(7, "get_status", {"channel": "boss"})
        self.execute_tool.assert_any_call(7, "start_application",
                                          {"channel": "boss", "confirm": True, "max_count": 4})
        self.assertEqual(self.execute_tool.call_count, 2)
        self.assertEqual(scheduled_jobs.execute(7, "list", {})["jobs"][0]["status"], "started")
        notice = reminders.execute(7, "notifications", {})["notifications"][0]
        self.assertEqual(set(notice), {"id", "kind", "item_id", "fields", "due_at", "created_at", "read_at"})
        self.assertEqual(notice["kind"], "scheduled_job")
        self.assertEqual(notice["item_id"], job["id"])
        self.assertEqual(notice["fields"]["status"], "started")
        self.assertTrue(reminders.execute(7, "mark_read", {"id": notice["id"]})["ok"])
        self.assertEqual(reminders.execute(8, "notifications", {})["total"], 0)

    def test_stopping_failure_unknown_and_running_precondition(self):
        cases = [({"operation": "stop_application", "channel": "liepin"},
                  {"ok": True, "status": "stopping"}, "stopping"),
                 ({"operation": "stop_application"}, {"ok": False, "error": "no"}, "failed"),
                 ({}, {"ok": False, "status": "limit_unconfirmed"}, "unknown")]
        for params, response, expected in cases:
            job = self.schedule(**params)["job"]
            self.make_due(job)
            self.execute_tool.side_effect = lambda u, name, args: (
                {"isRunning": False} if name == "get_status" else response)
            scheduled_jobs._process_due()
            self.assertEqual(scheduled_jobs.execute(1, "list", {})["jobs"][0]["status"], expected)
        job = self.schedule()["job"]
        self.make_due(job)
        self.execute_tool.reset_mock()
        self.execute_tool.side_effect = lambda u, name, args: {"isRunning": True}
        scheduled_jobs._process_due()
        self.execute_tool.assert_called_once_with(1, "get_status", {"channel": "boss"})
        self.assertEqual(scheduled_jobs.execute(1, "list", {})["jobs"][0]["status"], "failed")

    def test_claim_survives_restart_without_retry(self):
        job = self.schedule()["job"]
        self.make_due(job)
        self.assertIsNotNone(scheduled_jobs._claim())
        with db.get_conn() as conn:
            scheduled_jobs.init_db(conn)
        scheduled_jobs._process_due()
        self.ensure.assert_not_called()
        self.assertEqual(scheduled_jobs.execute(1, "list", {})["jobs"][0]["status"], "unknown")
        self.assertEqual(reminders.execute(1, "notifications", {})["notifications"][0]["fields"]["status"], "unknown")

    def test_competing_workers_claim_once(self):
        job = self.schedule()["job"]
        self.make_due(job)
        barrier = threading.Barrier(3)
        errors = []
        def worker():
            barrier.wait()
            try:
                scheduled_jobs._process_due()
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.ensure.assert_called_once_with(1)
        self.assertEqual(self.execute_tool.call_count, 2)
        self.assertEqual(reminders.execute(1, "notifications", {})["total"], 1)

    def test_uncertain_tool_failure_never_retries(self):
        job = self.schedule()["job"]
        self.make_due(job)
        def tool(user, name, args):
            if name == "get_status":
                return {"isRunning": False}
            raise TimeoutError("response lost")
        self.execute_tool.side_effect = tool
        scheduled_jobs._process_due()
        scheduled_jobs._process_due()
        self.assertEqual(self.execute_tool.call_count, 2)
        self.assertEqual(scheduled_jobs.execute(1, "list", {})["jobs"][0]["status"], "unknown")
