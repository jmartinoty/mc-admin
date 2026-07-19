"""Tests de TempBanScheduler.

Même approche que test_scheduler.py : `_run()` testé de façon SYNCHRONE avec
un `wait` factice (déterministe, sans thread ni sleep réel), plus un test
d'intégration avec un VRAI thread à intervalle minuscule.
"""
from __future__ import annotations

import time
import unittest
from datetime import datetime, timedelta, timezone

from adapters.restart_schedule import InMemoryRestartSchedule
from domain.model import Permission
from domain.services import AdminService
from tempban_scheduler import AUTO_UNBAN_USER, TempBanScheduler

from tests.fakes import (
    FakeBackupArchives,
    FakeBans,
    FakeContainer,
    FakeGame,
    FakeLogs,
    FakeMetrics,
    FakeNotifier,
    FakeOps,
    FakePlayerHistory,
    FakeTempBans,
    FakeUpdater,
    FixedClock,
    RecordingAudit,
)

T0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _service(game, temp_bans, audit=None):
    return AdminService(
        game=game,
        container=FakeContainer(),
        logs=FakeLogs(),
        audit=audit if audit is not None else RecordingAudit(),
        clock=FixedClock(),
        container_name="minecraft",
        metrics=FakeMetrics(),
        player_history=FakePlayerHistory(),
        backup_archives=FakeBackupArchives(),
        updater=FakeUpdater(),
        ops=FakeOps(),
        notifications=FakeNotifier(),
        bans=FakeBans(),
        temp_bans=temp_bans,
        restart_schedule=InMemoryRestartSchedule(),
    )


class TestAutoUnbanUser(unittest.TestCase):
    def test_can_only_manage_bans(self):
        self.assertTrue(AUTO_UNBAN_USER.can(Permission.BAN_MANAGE))
        self.assertFalse(AUTO_UNBAN_USER.can(Permission.BACKUP_TRIGGER))
        self.assertFalse(AUTO_UNBAN_USER.can(Permission.STOP))


class FakeWait:
    """Un seul tick puis arrêt — suffisant pour vérifier le comportement."""

    def __init__(self, stop_event):
        self._stop_event = stop_event
        self.calls = 0

    def __call__(self, timeout):
        self.calls += 1
        if self.calls >= 2:
            self._stop_event.set()
        return False


class TestRunSynchronous(unittest.TestCase):
    def test_pardons_due_entries(self):
        game = FakeGame()
        temp_bans = FakeTempBans()
        temp_bans.schedule("toto", T0 - timedelta(hours=1))  # déjà expiré
        sched = TempBanScheduler(_service(game, temp_bans), temp_bans, clock=lambda: T0)
        sched._wait = FakeWait(sched._stop)
        sched._run()
        self.assertEqual(game.ban_calls, [("pardon", "toto", "")])
        self.assertEqual(temp_bans.pending(), [])  # nettoyé par pardon() lui-même

    def test_future_entries_are_not_pardoned_yet(self):
        game = FakeGame()
        temp_bans = FakeTempBans()
        temp_bans.schedule("toto", T0 + timedelta(hours=1))  # pas encore expiré
        sched = TempBanScheduler(_service(game, temp_bans), temp_bans, clock=lambda: T0)
        sched._wait = FakeWait(sched._stop)
        sched._run()
        self.assertEqual(game.ban_calls, [])
        self.assertEqual(len(temp_bans.pending()), 1)

    def test_pardon_is_audited_as_auto_unban_user(self):
        game = FakeGame()
        temp_bans = FakeTempBans()
        temp_bans.schedule("toto", T0 - timedelta(hours=1))
        audit = RecordingAudit()
        sched = TempBanScheduler(_service(game, temp_bans, audit=audit), temp_bans, clock=lambda: T0)
        sched._wait = FakeWait(sched._stop)
        sched._run()
        entry = audit.entries[-1]
        self.assertEqual(entry.username, "auto-unban")
        self.assertEqual(entry.outcome, "allowed")

    def test_pardon_failure_does_not_kill_the_loop(self):
        class FailingGame(FakeGame):
            def pardon(self, player_name):
                raise RuntimeError("rcon down")

        game = FailingGame()
        temp_bans = FakeTempBans()
        temp_bans.schedule("toto", T0 - timedelta(hours=1))
        sched = TempBanScheduler(_service(game, temp_bans), temp_bans, clock=lambda: T0)
        sched._wait = FakeWait(sched._stop)
        sched._run()  # ne doit PAS lever
        self.assertTrue(sched._stop.is_set())
        # échec du pardon -> l'entrée reste (sera retentée au prochain tick)
        self.assertEqual(len(temp_bans.pending()), 1)


class TestStartStopThreaded(unittest.TestCase):
    def test_real_thread_pardons_and_stops_cleanly(self):
        game = FakeGame()
        temp_bans = FakeTempBans()
        temp_bans.schedule("toto", datetime.now(timezone.utc) - timedelta(hours=1))
        sched = TempBanScheduler(_service(game, temp_bans), temp_bans, poll_seconds=0.0001)
        sched.start()
        self.addCleanup(sched.stop)
        deadline = time.monotonic() + 5.0
        while not game.ban_calls and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(game.ban_calls, [("pardon", "toto", "")])
        sched.stop()
        self.assertFalse(sched._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
