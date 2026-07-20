"""Vérification des mises à jour de mc-admin — thread de fond (bouton MAJ).

Même discipline que ModUpdateChecker : observation passive (catalog + store
directs, pas d'AdminService — aucune action utilisateur ici), le thread ne
meurt jamais, et AU PLUS une interrogation GitHub par 24 h. Erreur réseau =
le dernier verdict persisté reste affiché, jamais de fausse alerte.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

_RECHECK_AFTER = timedelta(hours=24)


class AppUpdateChecker:
    def __init__(self, catalog, state, poll_seconds: float = 3600.0,
                 sleep=None, clock=None) -> None:
        self._catalog = catalog          # AppReleaseCatalogPort (GithubReleases)
        self._state = state              # AppUpdateStatePort (JsonAppUpdate)
        self._poll_seconds = poll_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wait = sleep or self._stop.wait

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="app-update-checker")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        # Première passe 60 s après le démarrage, puis cadence horaire
        # bornée par les 24 h — même rythme que le checker de mods.
        if self._wait(60.0) or self._stop.is_set():
            return
        self._safe_tick()
        while not self._stop.is_set():
            if self._wait(self._poll_seconds) or self._stop.is_set():
                break
            self._safe_tick()

    def _safe_tick(self) -> None:
        try:
            self.tick()
        except Exception:  # noqa: BLE001 — le thread ne meurt jamais
            pass

    def tick(self) -> bool:
        """Interroge GitHub si le dernier verdict date de plus de 24 h.
        Renvoie True si une passe a eu lieu."""
        known = self._state.get()
        if known is not None and self._clock() - known[1] < _RECHECK_AFTER:
            return False
        release = self._catalog.latest()
        if release is None:
            return False                  # erreur réseau/API : verdict conservé
        self._state.set(release, self._clock())
        return True
