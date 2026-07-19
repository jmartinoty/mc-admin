"""Tests PerfWatcher : MSPT soutenu + disque bas (anti-bruit, hystérésis)."""
from __future__ import annotations

import unittest

from perf_watcher import PerfWatcher
from tests.fakes import FakeNotifier


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


if __name__ == "__main__":
    unittest.main()
