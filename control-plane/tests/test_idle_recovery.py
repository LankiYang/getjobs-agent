"""Regression coverage for task-aware runner reclamation."""
import contextlib
import sqlite3
import unittest
from unittest.mock import patch

from app import activity, runners


class TaskAwareIdleTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute('CREATE TABLE task_start_reservations (id INTEGER PRIMARY KEY, user_id INTEGER, channel TEXT, observed_running INTEGER NOT NULL DEFAULT 0)')
        self.states = {'boss': False, 'liepin': False, '51job': False, 'zhilian': False}
        self.row = {'port': 12345, 'secret': 'isolated'}
        self.patches = [patch.object(runners, 'get_conn', self.connection), patch.object(runners.httpx, 'get', self.get)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.conn.close()

    @contextlib.contextmanager
    def connection(self):
        yield self.conn
        self.conn.commit()

    def get(self, url, **kwargs):
        channel = url.split('/api/', 1)[1].split('/', 1)[0]
        class Response:
            status_code = 200
            def json(inner):
                return {'isRunning': self.states[channel]}
        return Response()

    def test_unknown_or_running_task_prevents_reclaim(self):
        self.states['boss'] = True
        self.assertFalse(runners._tasks_idle(7, self.row))
        self.states['boss'] = None
        self.assertFalse(runners._tasks_idle(7, self.row))
        self.states['boss'] = False
        self.assertTrue(runners._tasks_idle(7, self.row))

    def test_unobserved_start_survives_idle_report_until_running_then_finished(self):
        self.conn.execute("INSERT INTO task_start_reservations(user_id, channel) VALUES (7, 'boss')")
        self.assertFalse(runners._tasks_idle(7, self.row))
        self.states['boss'] = True
        self.assertFalse(runners._tasks_idle(7, self.row))
        self.states['boss'] = False
        self.assertTrue(runners._tasks_idle(7, self.row))

    def test_recent_user_activity_and_busy_task_prevent_idle(self):
        with patch.object(activity, 'IDLE_SECONDS', 60):
            activity.touch(901)
            self.assertFalse(activity.is_idle(901))
            with activity._lock:
                activity._last[901] -= 120
            activity.begin_busy(901)
            with activity._lock:
                activity._last[901] -= 120
            self.assertFalse(activity.is_idle(901))
            activity.end_busy(901)
            with activity._lock:
                activity._last.pop(901, None)


if __name__ == '__main__':
    unittest.main()
