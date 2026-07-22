"""Tests de HealthWatcher (alerte serveur down, désactivée par défaut).

`_tick()` testé de façon synchrone (même approche que les autres watchers) :
le seuil anti-bruit, l'alerte unique et le rétablissement.
"""
from __future__ import annotations

import time
import unittest

from health_watcher import HealthWatcher

from tests.fakes import FakeContainer, FakeIncidents, FakeNotifier


class TestHealthWatcherTick(unittest.TestCase):
    def setUp(self):
        self.container = FakeContainer(running=True)
        self.notifier = FakeNotifier()
        self.watcher = HealthWatcher(self.container, self.notifier, poll_seconds=30, down_polls_before_alert=3)

    def test_no_alert_while_running(self):
        for _ in range(5):
            self.watcher._tick()
        self.assertEqual(self.notifier.sent, [])

    def test_transient_down_below_threshold_never_alerts(self):
        # Un `docker restart` manuel passe par 1-2 sondes down : pas d'alerte.
        self.container._running = False
        self.watcher._tick()
        self.watcher._tick()
        self.container._running = True
        self.watcher._tick()
        self.assertEqual(self.notifier.sent, [])

    def test_alerts_once_after_threshold_then_recovery_notice(self):
        self.container._running = False
        for _ in range(6):  # bien au-delà du seuil : une SEULE alerte
            self.watcher._tick()
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertEqual(self.notifier.sent[0][0], "Serveur down")
        self.assertEqual(self.notifier.sent[0][2], "error")
        self.container._running = True
        self.watcher._tick()
        self.watcher._tick()  # rétablissement notifié une seule fois
        self.assertEqual(len(self.notifier.sent), 2)
        self.assertEqual(self.notifier.sent[1][0], "Serveur rétabli")

    def test_incident_opened_on_alert_and_closed_on_recovery(self):
        incidents = FakeIncidents()
        watcher = HealthWatcher(self.container, self.notifier, poll_seconds=30,
                                down_polls_before_alert=2, incidents=incidents)
        self.container._running = False
        watcher._tick()
        watcher._tick()  # seuil atteint : incident ouvert
        self.assertEqual(incidents.opens, [("server", "availability", "Serveur", "Serveur arrêté")])
        self.assertEqual(incidents.closes, [])
        self.container._running = True
        watcher._tick()  # rétablissement : incident fermé
        self.assertEqual(incidents.closes, ["server"])

    def test_no_incident_below_threshold(self):
        incidents = FakeIncidents()
        watcher = HealthWatcher(self.container, self.notifier, poll_seconds=30,
                                down_polls_before_alert=3, incidents=incidents)
        self.container._running = False
        watcher._tick()  # 1 sonde down, sous le seuil
        self.container._running = True
        watcher._tick()
        self.assertEqual(incidents.opens, [])
        self.assertEqual(incidents.closes, [])

    def test_proxy_error_is_inconclusive(self):
        # Proxy injoignable != serveur down : on ne conclut rien, pas d'alerte.
        self.container._running = False
        self.container._fail = True

        # FakeContainer.status() ne lève pas -> forcer l'erreur via un stub.
        class Boom:
            def status(self):
                raise RuntimeError("proxy 500")

        watcher = HealthWatcher(Boom(), self.notifier, down_polls_before_alert=1)
        watcher._tick()
        self.assertEqual(self.notifier.sent, [])


class TestHealthWatcherThread(unittest.TestCase):
    def test_real_thread_alerts_and_stops_cleanly(self):
        container = FakeContainer(running=False)
        notifier = FakeNotifier()
        watcher = HealthWatcher(container, notifier, poll_seconds=0.001, down_polls_before_alert=2)
        watcher.start()
        self.addCleanup(watcher.stop)
        deadline = time.monotonic() + 5.0
        while not notifier.sent and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(notifier.sent)
        self.assertEqual(notifier.sent[0][0], "Serveur down")
        watcher.stop()
        self.assertFalse(watcher._thread.is_alive())


if __name__ == "__main__":
    unittest.main()


class TestExtraContainers(unittest.TestCase):
    """Conteneurs compagnons (V6.7.x — tunnel playit resté mort 2 jours)."""

    def setUp(self):
        self.server = FakeContainer(running=True)
        self.tunnel = FakeContainer(running=True, name="playit")
        self.notifier = FakeNotifier()
        from tests.fakes import FakeWatched
        self.store = FakeWatched(["playit"])
        self.ports = {"playit": self.tunnel}
        self.watcher = HealthWatcher(self.server, self.notifier, poll_seconds=30,
                                     down_polls_before_alert=3,
                                     watched_store=self.store,
                                     port_factory=lambda name: self.ports[name])

    def test_extra_down_alerts_with_its_name_server_untouched(self):
        self.tunnel._running = False
        for _ in range(4):
            self.watcher._tick()
        self.assertEqual(len(self.notifier.sent), 1)
        title, body, kind, event = self.notifier.sent[0]
        self.assertEqual(title, "playit down")
        self.assertIn("« playit »", body)
        self.assertEqual(kind, "error")
        self.assertEqual(event, "health")                # filtrable dans l'UI
        self.tunnel._running = True
        self.watcher._tick()
        self.assertEqual(self.notifier.sent[-1][0], "playit rétabli")

    def test_store_changes_are_picked_up_without_restart(self):
        added = FakeContainer(running=False, name="frp")
        self.ports["frp"] = added
        self.store.names.append("frp")            # ajout via l'UI
        for _ in range(3):
            self.watcher._tick()
        self.assertIn("frp down", [t for t, *_ in self.notifier.sent])
        self.store.names.remove("frp")            # retrait via l'UI
        sent_before = len(self.notifier.sent)
        self.watcher._tick()
        self.assertEqual(len(self.notifier.sent), sent_before)  # plus observé

    def test_each_container_has_its_own_streak(self):
        self.server._running = False
        self.watcher._tick()          # serveur : streak 1 (sous le seuil)
        self.tunnel._running = False
        for _ in range(2):
            self.watcher._tick()      # serveur atteint 3, tunnel seulement 2
        titles = [t for t, *_ in self.notifier.sent]
        self.assertEqual(titles, ["Serveur down"])
