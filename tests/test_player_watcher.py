"""Tests de PlayerLogWatcher.

`_initial_position`/`_consume_new_lines` sont testés directement (synchrone,
déterministe). Un seul test utilise un VRAI thread (`start`/`stop`) pour
vérifier l'intégration, avec un poll_interval minuscule.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from player_watcher import PlayerLogWatcher

from tests.fakes import FakePlayerHistory

FIXED_NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


class TestPlayerLogWatcherSynchronous(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        self.addCleanup(os.remove, self.path)
        self.history = FakePlayerHistory(summary=[])

    def _write(self, text: str) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(text)

    def _watcher(self) -> PlayerLogWatcher:
        return PlayerLogWatcher(self.path, self.history, clock=lambda: FIXED_NOW)

    def test_ignores_content_written_before_start(self):
        self._write("[10:00:00] [Server thread/INFO]: alice joined the game\n")
        watcher = self._watcher()
        position = watcher._initial_position()  # seek en fin -> ignore l'existant
        watcher._consume_new_lines(position)
        self.assertEqual(self.history.joins, [])

    def test_detects_join_then_leave_appended_after_start(self):
        watcher = self._watcher()
        position = watcher._initial_position()
        self._write("[10:05:00] [Server thread/INFO]: alice joined the game\n")
        self._write("[10:20:00] [Server thread/INFO]: alice left the game\n")
        watcher._consume_new_lines(position)
        self.assertEqual(self.history.joins, [("alice", FIXED_NOW)])
        self.assertEqual(self.history.leaves, [("alice", FIXED_NOW)])

    def test_notifier_receives_join_and_leave_when_configured(self):
        from tests.fakes import FakeNotifier
        notifier = FakeNotifier()
        moment = {"now": FIXED_NOW}
        watcher = PlayerLogWatcher(self.path, self.history, clock=lambda: moment["now"], notifier=notifier)
        position = watcher._initial_position()
        self._write("[10:05:00] [Server thread/INFO]: alice joined the game\n")
        position = watcher._consume_new_lines(position)
        moment["now"] = FIXED_NOW + timedelta(minutes=20)  # au-delà du cooldown anti-spam
        self._write("[10:25:00] [Server thread/INFO]: alice left the game\n")
        watcher._consume_new_lines(position)
        self.assertEqual(len(notifier.sent), 2)
        self.assertIn("alice a rejoint le serveur", notifier.sent[0][1])
        self.assertIn("alice a quitté le serveur", notifier.sent[1][1])

    def test_unrelated_lines_are_ignored(self):
        watcher = self._watcher()
        position = watcher._initial_position()
        self._write("[10:05:00] [RCON Listener #2/INFO]: Thread RCON Client /x started\n")
        watcher._consume_new_lines(position)
        self.assertEqual(self.history.joins, [])
        self.assertEqual(self.history.leaves, [])

    def test_missing_file_does_not_raise(self):
        watcher = PlayerLogWatcher("/nonexistent/latest.log", self.history)
        self.assertEqual(watcher._initial_position(), 0)
        self.assertEqual(watcher._consume_new_lines(0), 0)

    def test_incremental_reads_do_not_reprocess_old_lines(self):
        watcher = self._watcher()
        position = watcher._initial_position()
        self._write("[10:05:00] [Server thread/INFO]: alice joined the game\n")
        position = watcher._consume_new_lines(position)
        self._write("[10:06:00] [Server thread/INFO]: bob joined the game\n")
        watcher._consume_new_lines(position)
        self.assertEqual([p for p, _ in self.history.joins], ["alice", "bob"])


class TestNotificationCooldown(unittest.TestCase):
    """Anti-spam : un même joueur = une notification max par fenêtre de
    cooldown ; l'historique, lui, enregistre TOUT."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        self.addCleanup(os.remove, self.path)
        self.history = FakePlayerHistory(summary=[])
        from tests.fakes import FakeNotifier
        self.notifier = FakeNotifier()
        self.now = FIXED_NOW
        self.watcher = PlayerLogWatcher(
            self.path, self.history, clock=lambda: self.now, notifier=self.notifier,
            notify_cooldown_seconds=15 * 60,
        )
        self.position = self.watcher._initial_position()

    def _emit(self, line: str) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self.position = self.watcher._consume_new_lines(self.position)

    def test_same_player_is_throttled_but_history_records_everything(self):
        self._emit("[10:05:00] [Server thread/INFO]: alice joined the game")
        self.now = FIXED_NOW + timedelta(minutes=1)
        self._emit("[10:06:00] [Server thread/INFO]: alice left the game")   # < 15 min : supprimée
        self.now = FIXED_NOW + timedelta(minutes=20)
        self._emit("[10:25:00] [Server thread/INFO]: alice joined the game")  # fenêtre passée : notifiée
        self.assertEqual(len(self.notifier.sent), 2)
        self.assertEqual(len(self.history.joins) + len(self.history.leaves), 3)

    def test_distinct_players_are_not_throttled_together(self):
        self._emit("[10:05:00] [Server thread/INFO]: alice joined the game")
        self._emit("[10:05:01] [Server thread/INFO]: bob joined the game")
        titles = [t for t, *_ in self.notifier.sent]
        self.assertEqual(titles.count("Joueur connecté"), 2)          # pas de throttle croisé
        # bob n'a jamais été vu -> notification « nouveau joueur » EN PLUS,
        # taguée pour l'interrupteur dédié ; alice est connue -> rien.
        self.assertEqual(titles.count("Nouveau joueur 🎉"), 1)
        new_evt = next(evt for t, _, _, evt in self.notifier.sent if t.startswith("Nouveau"))
        self.assertEqual(new_evt, "new_player")


class TestPlayerLogWatcherThreaded(unittest.TestCase):
    def test_real_thread_detects_join_within_short_poll_interval(self):
        fd, path = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        self.addCleanup(os.remove, path)
        history = FakePlayerHistory(summary=[])
        watcher = PlayerLogWatcher(path, history, poll_interval=0.05, clock=lambda: FIXED_NOW)
        watcher.start()
        self.addCleanup(watcher.stop)
        # Écrire AVANT que le thread ait pris sa position initiale (seek fin de
        # fichier) ferait sauter la ligne — cause réelle d'un flaky sous charge.
        self.assertTrue(watcher.ready.wait(2.0))

        with open(path, "a", encoding="utf-8") as fh:
            fh.write("[10:05:00] [Server thread/INFO]: alice joined the game\n")

        deadline = time.monotonic() + 3.0
        while not history.joins and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(history.joins, [("alice", FIXED_NOW)])
        watcher.stop()
        self.assertFalse(watcher._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
