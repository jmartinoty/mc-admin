"""Tests de l'adapter JsonIncidents (historique des incidents persisté)."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from adapters.incidents import JsonIncidents


class _Clock:
    def __init__(self):
        self.now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


class TestJsonIncidents(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))
        self.clock = _Clock()

    def _log(self):
        return JsonIncidents(self.path, clock=self.clock)

    def test_open_then_close_records_a_bounded_incident(self):
        log = self._log()
        log.open("server", "availability", "Serveur", "arrêté")
        self.clock.advance(minutes=5)
        log.close("server")

        incidents = log.recent()
        self.assertEqual(len(incidents), 1)
        inc = incidents[0]
        self.assertEqual(inc.subject, "server")
        self.assertEqual(inc.label, "Serveur")
        self.assertIsNotNone(inc.ended_at)
        self.assertEqual((inc.ended_at - inc.started_at).total_seconds(), 300)

    def test_open_is_idempotent_per_subject(self):
        log = self._log()
        log.open("server", "availability", "Serveur")
        self.clock.advance(minutes=1)
        log.open("server", "availability", "Serveur")  # déjà ouvert : ignoré
        incidents = log.recent()
        self.assertEqual(len(incidents), 1)
        self.assertIsNone(incidents[0].ended_at)  # toujours en cours

    def test_close_without_open_is_noop(self):
        log = self._log()
        log.close("server")  # rien d'ouvert
        self.assertEqual(log.recent(), [])

    def test_ongoing_incident_is_listed_with_no_end(self):
        log = self._log()
        log.open("disk", "disk", "Espace disque")
        incidents = log.recent()
        self.assertEqual(len(incidents), 1)
        self.assertIsNone(incidents[0].ended_at)

    def test_recent_sorted_newest_first(self):
        log = self._log()
        log.open("server", "availability", "Serveur")
        log.close("server")
        self.clock.advance(hours=1)
        log.open("disk", "disk", "Espace disque")
        subjects = [i.subject for i in log.recent()]
        self.assertEqual(subjects, ["disk", "server"])

    def test_persistence_across_instances(self):
        log = self._log()
        log.open("server", "availability", "Serveur")
        log.close("server")
        reloaded = self._log()
        self.assertEqual(len(reloaded.recent()), 1)

    def test_tolerates_corrupt_file(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("not json")
        self.assertEqual(self._log().recent(), [])

    def test_reopen_after_close_creates_second_incident(self):
        log = self._log()
        log.open("server", "availability", "Serveur")
        log.close("server")
        self.clock.advance(hours=2)
        log.open("server", "availability", "Serveur")  # nouvelle chute
        self.assertEqual(len(log.recent()), 2)


if __name__ == "__main__":
    unittest.main()
