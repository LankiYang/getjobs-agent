"""Runner admission protects existing processes and bounds new starts."""
import contextlib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import runners


class RunnerCapacityTest(unittest.TestCase):
    @contextlib.contextmanager
    def rows(self, rows):
        class Connection:
            def execute(self, sql):
                class Result:
                    def fetchall(self):
                        return rows
                return Result()
        yield Connection()

    def check(self, rows, alive=lambda pid: False, healthy=lambda row: False, available=2048):
        with patch.object(runners, 'get_conn', lambda: self.rows(rows)), \
             patch.object(runners, '_alive', side_effect=alive), \
             patch.object(runners, 'healthy', side_effect=healthy), \
             patch.object(runners, '_available_memory_mib', return_value=available), \
             patch.object(runners, 'MAX_RUNNERS', 1), \
             patch.object(runners, 'MIN_AVAILABLE_MIB', 1024):
            runners._check_capacity()

    def test_stale_rows_do_not_block_and_live_error_rows_do(self):
        self.check([{'pid': 10, 'status': 'running'}, {'pid': None, 'status': 'starting'}])
        with self.assertRaises(runners.RunnerError) as error:
            self.check([{'pid': 10, 'status': 'stopped'}], alive=lambda pid: True)
        self.assertEqual(error.exception.status_code, 503)
        with self.assertRaises(runners.RunnerError):
            self.check([{'pid': None, 'status': 'error'}], healthy=lambda row: True)

    def test_stopped_without_pid_checks_health_only_when_port_is_occupied(self):
        row = {'pid': None, 'status': 'stopped', 'port': 12345}
        with patch.object(runners, '_port_free', return_value=True):
            self.check([row], healthy=lambda row: self.fail('unnecessary health request'))
        with patch.object(runners, '_port_free', return_value=False):
            with self.assertRaises(runners.RunnerError):
                self.check([row], healthy=lambda row: True)

    def test_zombie_is_not_alive_even_when_kill_probe_succeeds(self):
        with patch.object(runners, 'WINDOWS', False), \
             patch.object(runners.os, 'kill'), \
             patch.object(Path, 'read_text', return_value='123 (name with ) chars) Z 1'):
            self.assertFalse(runners._alive(123))
        with patch.object(runners, 'WINDOWS', False), \
             patch.object(runners.os, 'kill'), \
             patch.object(Path, 'read_text', side_effect=PermissionError):
            self.assertTrue(runners._alive(123))

    def test_low_or_unknown_memory_fails_closed(self):
        for available in (1023, None):
            with self.subTest(available=available), self.assertRaises(runners.RunnerError) as error:
                self.check([], available=available)
            self.assertEqual(error.exception.status_code, 503)
        self.check([], available=1024)

    def test_existing_healthy_runner_bypasses_admission(self):
        with patch.object(runners, '_row', return_value={'pid': 1}), \
             patch.object(runners, 'healthy', return_value=True), \
             patch.object(runners, 'status', return_value={'running': True}), \
             patch.object(runners, '_admission_lock', side_effect=AssertionError('admission called')):
            self.assertTrue(runners.start(42)['running'])

    def test_live_unhealthy_runner_is_not_replaced(self):
        with patch.object(runners, '_row', return_value={'pid': 1, 'status': 'stopped'}), \
             patch.object(runners, 'healthy', return_value=False), \
             patch.object(runners, '_alive', return_value=True), \
             patch.object(runners.config, 'START_TIMEOUT', 0), \
             patch.object(runners, '_spawn', side_effect=AssertionError('spawn called')):
            with self.assertRaises(runners.RunnerError) as error:
                runners._start(42)
            self.assertEqual(error.exception.status_code, 409)

    def test_concurrent_admissions_are_serialized(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(runners.config, 'CONTROL_DB', Path(directory) / 'control.db'):
            barrier = threading.Barrier(2)
            admitted = []
            rejected = []

            def start():
                barrier.wait()
                with runners._admission_lock():
                    try:
                        self.check(admitted, alive=lambda pid: True)
                    except runners.RunnerError as exc:
                        rejected.append(exc.status_code)
                    else:
                        time.sleep(0.05)
                        admitted.append({'pid': 10, 'status': 'starting'})

            threads = [threading.Thread(target=start) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(len(admitted), 1)
            self.assertEqual(rejected, [503])


if __name__ == '__main__':
    unittest.main()
