"""Tests de l'adapter InMemoryRestartSchedule."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from adapters.restart_schedule import InMemoryRestartSchedule

T0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


class TestInMemoryRestartSchedule(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryRestartSchedule()

    def test_status_on_fresh_store_is_none(self):
        self.assertIsNone(self.store.status())

    def test_schedule_then_status_roundtrip(self):
        self.store.schedule(T0 + timedelta(minutes=30), "jeremy", 1800)
        status = self.store.status()
        self.assertEqual(status.restart_at, T0 + timedelta(minutes=30))
        self.assertEqual(status.scheduled_by, "jeremy")

    def test_rescheduling_replaces_previous(self):
        self.store.schedule(T0 + timedelta(minutes=30), "jeremy", 1800)
        self.store.schedule(T0 + timedelta(minutes=10), "paul", 600)
        status = self.store.status()
        self.assertEqual(status.restart_at, T0 + timedelta(minutes=10))
        self.assertEqual(status.scheduled_by, "paul")

    def test_cancel_clears_state_and_reports_whether_something_was_pending(self):
        self.assertFalse(self.store.cancel())  # rien à annuler
        self.store.schedule(T0 + timedelta(minutes=30), "jeremy", 1800)
        self.assertTrue(self.store.cancel())
        self.assertIsNone(self.store.status())

    def test_take_due_warning_none_without_pending_schedule(self):
        self.assertIsNone(self.store.take_due_warning(T0))

    def test_take_due_warning_fires_each_threshold_once_in_order(self):
        self.store.schedule(T0 + timedelta(minutes=11), "jeremy", 660)
        # Bien avant tout seuil : rien.
        self.assertIsNone(self.store.take_due_warning(T0))
        # 590s restantes -> seuil 600 franchi.
        self.assertEqual(self.store.take_due_warning(T0 + timedelta(seconds=70)), 600)
        # Même instant, seuil déjà consommé : plus rien à annoncer.
        self.assertIsNone(self.store.take_due_warning(T0 + timedelta(seconds=70)))
        # 290s restantes -> seuil 300 franchi.
        self.assertEqual(self.store.take_due_warning(T0 + timedelta(seconds=370)), 300)

    def test_short_schedule_never_fires_thresholds_already_passed_at_schedule_time(self):
        # Délai de 4 min (240s) : les seuils 600/300 n'ont jamais été valides
        # pour CETTE programmation, même une fois le temps largement écoulé.
        self.store.schedule(T0 + timedelta(minutes=4), "jeremy", 240)
        self.assertEqual(self.store.take_due_warning(T0 + timedelta(seconds=181)), 60)
        self.assertEqual(self.store.take_due_warning(T0 + timedelta(seconds=400)), 30)
        # 600/300 ne sont jamais renvoyés, quel que soit "now".

    def test_rescheduling_resets_announced_thresholds(self):
        self.store.schedule(T0 + timedelta(minutes=11), "jeremy", 660)
        self.assertEqual(self.store.take_due_warning(T0 + timedelta(seconds=70)), 600)
        # Reprogrammation : le nouveau délai doit pouvoir ré-annoncer "600".
        self.store.schedule(T0 + timedelta(minutes=11), "jeremy", 660)
        self.assertEqual(self.store.take_due_warning(T0 + timedelta(seconds=70)), 600)

    def test_cancel_resets_announced_thresholds(self):
        self.store.schedule(T0 + timedelta(minutes=11), "jeremy", 660)
        self.store.take_due_warning(T0 + timedelta(seconds=70))
        self.store.cancel()
        self.store.schedule(T0 + timedelta(minutes=11), "jeremy", 660)
        self.assertEqual(self.store.take_due_warning(T0 + timedelta(seconds=70)), 600)


if __name__ == "__main__":
    unittest.main()
