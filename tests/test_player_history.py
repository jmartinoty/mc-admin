"""Tests de l'adapter SqlitePlayerHistory."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from adapters.player_history import SqlitePlayerHistory

T0 = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)


class TestSqlitePlayerHistory(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)  # le fichier doit être créé PAR l'adapter
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def test_creates_schema_on_first_use(self):
        SqlitePlayerHistory(self.path)
        self.assertTrue(os.path.exists(self.path))

    def test_closed_session_reports_duration_and_offline(self):
        clock = iter([T0 + timedelta(hours=5)])  # heure de calcul de summary()
        history = SqlitePlayerHistory(self.path, clock=lambda: next(clock))
        history.record_join("alice", T0)
        history.record_leave("alice", T0 + timedelta(minutes=90))
        summary = history.summary()
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0].player, "alice")
        self.assertEqual(summary[0].total_seconds, 90 * 60)
        self.assertFalse(summary[0].online)
        self.assertEqual(summary[0].last_seen, T0 + timedelta(minutes=90))

    def test_open_session_counts_up_to_now_and_reports_online(self):
        now = T0 + timedelta(minutes=30)
        history = SqlitePlayerHistory(self.path, clock=lambda: now)
        history.record_join("alice", T0)  # jamais de leave -> session ouverte
        summary = history.summary()
        self.assertTrue(summary[0].online)
        self.assertEqual(summary[0].total_seconds, 30 * 60)
        self.assertEqual(summary[0].last_seen, now)

    def test_rejoin_without_prior_leave_closes_orphan_session(self):
        """mc-admin redémarré entre un join et son leave -> l'ancienne session
        ne doit jamais rester ouverte indéfiniment quand un nouveau join arrive."""
        now = T0 + timedelta(hours=3)
        history = SqlitePlayerHistory(self.path, clock=lambda: now)
        history.record_join("alice", T0)
        history.record_join("alice", T0 + timedelta(hours=1))  # pas de leave entre les deux
        summary = history.summary()
        self.assertEqual(len(summary), 1)  # un seul joueur, pas deux entrées
        self.assertTrue(summary[0].online)  # la session la plus récente reste ouverte

    def test_cumulative_across_multiple_sessions(self):
        history = SqlitePlayerHistory(self.path, clock=lambda: T0)
        history.record_join("alice", T0)
        history.record_leave("alice", T0 + timedelta(minutes=30))
        history.record_join("alice", T0 + timedelta(hours=2))
        history.record_leave("alice", T0 + timedelta(hours=2, minutes=45))
        summary = history.summary()
        self.assertEqual(summary[0].total_seconds, (30 + 45) * 60)

    def test_multiple_players_sorted_by_last_seen_desc(self):
        history = SqlitePlayerHistory(self.path, clock=lambda: T0)
        history.record_join("alice", T0)
        history.record_leave("alice", T0 + timedelta(minutes=10))
        history.record_join("bob", T0 + timedelta(hours=1))
        history.record_leave("bob", T0 + timedelta(hours=1, minutes=10))
        summary = history.summary()
        self.assertEqual([s.player for s in summary], ["bob", "alice"])

    def test_empty_database_returns_empty_summary(self):
        history = SqlitePlayerHistory(self.path)
        self.assertEqual(history.summary(), [])




class TestSinceFilter(unittest.TestCase):
    """`since` filtre en SQL — vital depuis l'import de l'historique ancien."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "players.db")
        self.addCleanup(self.dir.cleanup)

    def test_since_filters_sessions_and_commands_in_sql(self):
        history = SqlitePlayerHistory(self.path)
        old = datetime(2025, 9, 1, 12, 0, tzinfo=timezone.utc)
        recent = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
        history.record_join("Ancien", old)
        history.record_leave("Ancien", old + timedelta(hours=1))
        history.record_command("Ancien", "/give Ancien minecraft:dirt", old)
        history.record_join("Recent", recent)
        history.record_leave("Recent", recent + timedelta(hours=1))
        history.record_command("Recent", "/time set day", recent)

        cutoff = datetime(2026, 7, 1, tzinfo=timezone.utc)
        sessions = history.recent_sessions(100, since=cutoff)
        self.assertEqual([s.player for s in sessions], ["Recent"])
        commands = history.recent_commands(100, since=cutoff)
        self.assertEqual([c[0] for c in commands], ["Recent"])
        # Sans filtre : tout revient, l'ancien inclus.
        self.assertEqual(len(history.recent_sessions(100)), 2)
        self.assertEqual(len(history.recent_commands(100)), 2)

    def test_open_session_older_than_cutoff_still_counts(self):
        history = SqlitePlayerHistory(self.path)
        history.record_join("Marathon", datetime(2026, 6, 1, tzinfo=timezone.utc))
        cutoff = datetime(2026, 7, 1, tzinfo=timezone.utc)
        # Session jamais fermée : toujours en cours, donc dans la période.
        self.assertEqual([s.player for s in history.recent_sessions(100, since=cutoff)],
                         ["Marathon"])


if __name__ == "__main__":
    unittest.main()
