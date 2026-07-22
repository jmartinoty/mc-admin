"""Tests PerfWatcher : MSPT soutenu + disque bas (anti-bruit, hystérésis)."""
from __future__ import annotations

import unittest

from perf_watcher import PerfWatcher
from tests.fakes import FakeIncidents, FakeNotifier


class MetricsStub:
    def __init__(self, mspt=1.0):
        self.mspt = mspt
        self.fail = False

    def performance(self):
        if self.fail:
            raise RuntimeError("prometheus down")
        from domain.model import PerformanceSnapshot
        return PerformanceSnapshot(mspt_mean=self.mspt)


class ArchivesStub:
    def __init__(self, free=100 * 1024**3):
        self.free = free

    def free_bytes(self):
        return self.free


class FakeThresholds:
    def __init__(self, thresholds):
        self._thresholds = thresholds

    def get(self):
        return self._thresholds

    def set(self, thresholds):
        self._thresholds = thresholds


class TestPerfWatcherConfigurableThresholds(unittest.TestCase):
    """Seuils réglables dans l'UI (backlog fiabilité n° 4) — relus à chaque
    passe, sans redémarrer mc-admin."""

    def test_custom_mspt_threshold_is_honoured(self):
        from domain.model import AlertThresholds
        metrics = MetricsStub(mspt=120.0)
        archives = ArchivesStub()
        notifier = FakeNotifier()
        thresholds = FakeThresholds(AlertThresholds(
            mspt_threshold_ms=150.0, disk_min_free_gib=10.0, mspt_sustained_minutes=1.0))
        watcher = PerfWatcher(metrics, archives, notifier, poll_seconds=60,
                              mspt_polls_before_alert=1, thresholds=thresholds)
        watcher._tick()
        self.assertEqual(notifier.sent, [])  # 120 ms < seuil réglé à 150 ms

    def test_custom_disk_threshold_is_honoured(self):
        from domain.model import AlertThresholds
        metrics = MetricsStub()
        archives = ArchivesStub(free=15 * 1024**3)
        notifier = FakeNotifier()
        thresholds = FakeThresholds(AlertThresholds(
            mspt_threshold_ms=50.0, disk_min_free_gib=20.0, mspt_sustained_minutes=3.0))
        watcher = PerfWatcher(metrics, archives, notifier, poll_seconds=60,
                              thresholds=thresholds)
        watcher._tick()
        self.assertEqual(notifier.sent[0][0], "Espace disque bas")
        self.assertIn("seuil : 20 Gio", notifier.sent[0][1])

    def test_sustained_minutes_converted_to_poll_count(self):
        from domain.model import AlertThresholds
        metrics = MetricsStub(mspt=75.0)
        archives = ArchivesStub()
        notifier = FakeNotifier()
        # 2 minutes / 60 s de poll = 2 sondes avant alerte (pas 3, le défaut).
        thresholds = FakeThresholds(AlertThresholds(
            mspt_threshold_ms=50.0, disk_min_free_gib=10.0, mspt_sustained_minutes=2.0))
        watcher = PerfWatcher(metrics, archives, notifier, poll_seconds=60,
                              thresholds=thresholds)
        watcher._tick()
        self.assertEqual(notifier.sent, [])
        watcher._tick()
        self.assertEqual([t for t, *_ in notifier.sent], ["Lag serveur"])

    def test_unreadable_config_falls_back_to_defaults(self):
        class BrokenThresholds:
            def get(self):
                raise RuntimeError("fichier illisible")

        metrics = MetricsStub(mspt=75.0)
        archives = ArchivesStub()
        notifier = FakeNotifier()
        watcher = PerfWatcher(metrics, archives, notifier, poll_seconds=60,
                              mspt_polls_before_alert=3, thresholds=BrokenThresholds())
        for _ in range(3):
            watcher._tick()
        self.assertEqual([t for t, *_ in notifier.sent], ["Lag serveur"])  # défauts, pas de crash


class TestPerfWatcher(unittest.TestCase):
    def setUp(self):
        self.metrics = MetricsStub()
        self.archives = ArchivesStub()
        self.notifier = FakeNotifier()
        self.watcher = PerfWatcher(self.metrics, self.archives, self.notifier,
                                   poll_seconds=60, mspt_polls_before_alert=3)

    def test_sustained_mspt_alerts_once_then_recovery(self):
        self.metrics.mspt = 75.0
        for _ in range(5):
            self.watcher._tick()
        alerts = [t for t, *_ in self.notifier.sent]
        self.assertEqual(alerts, ["Lag serveur"])               # une seule alerte
        self.assertEqual(self.notifier.sent[0][3], "performance")   # taguée
        self.metrics.mspt = 3.0
        self.watcher._tick()
        self.assertEqual(self.notifier.sent[-1][0], "Lag serveur terminé")

    def test_transient_spike_below_threshold_stays_silent(self):
        self.metrics.mspt = 90.0
        self.watcher._tick()
        self.watcher._tick()                                        # 2 sondes < seuil de 3
        self.metrics.mspt = 2.0
        self.watcher._tick()
        self.assertEqual(self.notifier.sent, [])

    def test_prometheus_down_is_inconclusive(self):
        self.metrics.mspt = 90.0
        self.watcher._tick()
        self.watcher._tick()
        self.metrics.fail = True
        self.watcher._tick()                                        # ne conclut rien
        self.metrics.fail = False
        self.watcher._tick()                                        # 3e sonde concluante
        self.assertEqual([t for t, *_ in self.notifier.sent], ["Lag serveur"])

    def test_disk_low_alert_with_hysteresis(self):
        self.archives.free = 5 * 1024**3
        self.watcher._tick()
        self.watcher._tick()                                        # pas de répétition
        titles = [t for t, *_ in self.notifier.sent]
        self.assertEqual(titles, ["Espace disque bas"])
        self.assertEqual(self.notifier.sent[0][3], "disk")
        self.archives.free = 11 * 1024**3                           # au-dessus du seuil mais < +20 %
        self.watcher._tick()
        self.assertEqual(len(self.notifier.sent), 1)                # hystérésis : pas encore rétabli
        self.archives.free = 13 * 1024**3
        self.watcher._tick()
        self.assertEqual(self.notifier.sent[-1][0], "Espace disque rétabli")

    def test_mspt_incident_opened_and_closed(self):
        incidents = FakeIncidents()
        watcher = PerfWatcher(self.metrics, self.archives, self.notifier,
                              poll_seconds=60, mspt_polls_before_alert=2, incidents=incidents)
        self.metrics.mspt = 80.0
        watcher._tick()
        watcher._tick()  # seuil atteint : incident ouvert
        self.assertEqual([o[:3] for o in incidents.opens], [("mspt", "performance", "Lag serveur")])
        self.metrics.mspt = 2.0
        watcher._tick()  # rétablissement : fermé
        self.assertEqual(incidents.closes, ["mspt"])

    def test_disk_incident_opened_and_closed(self):
        incidents = FakeIncidents()
        watcher = PerfWatcher(self.metrics, self.archives, self.notifier,
                              poll_seconds=60, mspt_polls_before_alert=3, incidents=incidents)
        self.archives.free = 5 * 1024**3
        watcher._tick()
        self.assertEqual([o[0] for o in incidents.opens], ["disk"])
        self.archives.free = 13 * 1024**3
        watcher._tick()
        self.assertEqual(incidents.closes, ["disk"])


if __name__ == "__main__":
    unittest.main()
