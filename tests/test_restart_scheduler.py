"""Tests de RestartWarningScheduler.

Le thread est volontairement "mince" : toute la logique (seuils, avertissement,
redémarrage effectif) vit dans AdminService.tick_scheduled_restart, déjà
couverte par tests/test_services.py::TestScheduledRestart. Ce fichier ne teste
donc que le POLLING lui-même (appel répété, tolérance aux erreurs, start/stop),
même approche que test_tempban_scheduler.py.
"""
from __future__ import annotations

import time
import unittest

from domain.model import Permission
from restart_scheduler import SCHEDULED_RESTART_USER, RestartWarningScheduler


class TestScheduledRestartUser(unittest.TestCase):
    def test_can_only_restart(self):
        self.assertTrue(SCHEDULED_RESTART_USER.can(Permission.RESTART))
        self.assertFalse(SCHEDULED_RESTART_USER.can(Permission.STOP))
        self.assertFalse(SCHEDULED_RESTART_USER.can(Permission.BACKUP_TRIGGER))


class FakeService:
    def __init__(self, fail=False):
        self.calls: list = []
        self._fail = fail

    def tick_scheduled_restart(self, user):
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
    def test_calls_tick_with_scheduled_restart_user(self):
        service = FakeService()
        sched = RestartWarningScheduler(service)
        sched._wait = FakeWait(sched._stop)
        sched._run()
        self.assertEqual(service.calls, [SCHEDULED_RESTART_USER])

    def test_tick_failure_does_not_kill_the_loop(self):
        service = FakeService(fail=True)
        sched = RestartWarningScheduler(service)
        sched._wait = FakeWait(sched._stop)
        sched._run()  # ne doit pas lever malgré l'échec de tick_scheduled_restart
        self.assertTrue(sched._stop.is_set())


class TestStartStopThreaded(unittest.TestCase):
    def test_real_thread_polls_and_stops_cleanly(self):
        service = FakeService()
        sched = RestartWarningScheduler(service, poll_seconds=0.0001)
        sched.start()
        self.addCleanup(sched.stop)
        deadline = time.monotonic() + 5.0
        while not service.calls and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertGreaterEqual(len(service.calls), 1)
        self.assertEqual(service.calls[0], SCHEDULED_RESTART_USER)
        sched.stop()
        self.assertFalse(sched._thread.is_alive())

    def test_double_start_is_a_noop(self):
        sched = RestartWarningScheduler(FakeService(), poll_seconds=10)
        sched.start()
        first_thread = sched._thread
        sched.start()
        self.assertIs(sched._thread, first_thread)
        sched.stop()


if __name__ == "__main__":
    unittest.main()
