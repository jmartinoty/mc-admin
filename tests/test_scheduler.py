"""Tests de BackupScheduler (poller du tick persistant, V5).

Le thread est volontairement mince : toute la décision (intervalle écoulé ?
compteur persisté) vit dans AdminService.tick_backup_profiles, couverte par
tests/test_services.py::TestBackupSchedule. Ici : le polling lui-même, la
tolérance aux erreurs et start/stop — même approche que les autres schedulers.
"""
from __future__ import annotations

import time
import unittest

from domain.model import Permission
from scheduler import SYSTEM_USER, BackupScheduler


class TestSystemUser(unittest.TestCase):
    def test_can_only_trigger_backup(self):
        self.assertTrue(SYSTEM_USER.can(Permission.BACKUP_TRIGGER))
        self.assertFalse(SYSTEM_USER.can(Permission.STOP))
        self.assertFalse(SYSTEM_USER.can(Permission.RCON_RAW))


class FakeService:
    def __init__(self, fail=False):
        self.calls: list = []
        self._fail = fail

    def tick_backup_profiles(self, user):
        self.calls.append(user)
        if self._fail:
            raise RuntimeError("boom")


class FakeWait:
    def __init__(self, stop_event):
        self._stop_event = stop_event
        self.calls = 0

    def __call__(self, timeout):
        self.calls += 1
        if self.calls >= 2:
            self._stop_event.set()
        return False


class TestRunSynchronous(unittest.TestCase):
    def test_calls_tick_with_system_user(self):
        service = FakeService()
        sched = BackupScheduler(service)
        sched._wait = FakeWait(sched._stop)
        sched._run()
        self.assertEqual(service.calls, [SYSTEM_USER])

    def test_tick_failure_does_not_kill_the_loop(self):
        service = FakeService(fail=True)
        sched = BackupScheduler(service)
        sched._wait = FakeWait(sched._stop)
        sched._run()  # ne doit pas lever
        self.assertTrue(sched._stop.is_set())


class TestStartStopThreaded(unittest.TestCase):
    def test_real_thread_polls_and_stops_cleanly(self):
        service = FakeService()
        sched = BackupScheduler(service, poll_seconds=0.001)
        sched.start()
        self.addCleanup(sched.stop)
        deadline = time.monotonic() + 5.0
        while not service.calls and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertGreaterEqual(len(service.calls), 1)
        self.assertEqual(service.calls[0], SYSTEM_USER)
        sched.stop()
        self.assertFalse(sched._thread.is_alive())

    def test_double_start_is_a_noop(self):
        sched = BackupScheduler(FakeService(), poll_seconds=10)
        sched.start()
        first_thread = sched._thread
        sched.start()
        self.assertIs(sched._thread, first_thread)
        sched.stop()


if __name__ == "__main__":
    unittest.main()
