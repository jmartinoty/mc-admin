"""Vérification des mises à jour de mods — thread de fond (mods enrichis).

Discipline de la famille des watchers (ArchiveVerifier, PerfWatcher) :
observation passive, écrit directement dans son adapter, jamais d'exception
qui tue le thread. Bornes volontaires :

- au plus UNE interrogation Modrinth par 24 h (respect de l'API publique),
  sauf si un jar est apparu/changé entre-temps (nouvel ensemble d'empreintes) ;
- la VERSION DU SERVEUR est requise pour filtrer « dernière version
  compatible » — indisponible (Prometheus down, serveur éteint) = pas
  d'interrogation, on garde les derniers verdicts connus ;
- erreur réseau/API = pareil : les verdicts existants restent affichés.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

_RECHECK_AFTER = timedelta(hours=24)


class ModUpdateChecker:
    def __init__(self, mods, catalog, checks, version_provider,
                 poll_seconds: float = 3600.0, sleep=None, clock=None) -> None:
        self._mods = mods                    # ModsPort (liste + empreintes)
        self._catalog = catalog              # ModrinthCatalog
        self._checks = checks                # JsonModChecks
        self._version = version_provider     # () -> str | None (version MC courante)
        self._poll_seconds = poll_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wait = sleep or self._stop.wait

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="mod-update-checker")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        # Première passe rapide (60 s après le démarrage : laisser Prometheus
        # et le serveur se poser), puis cadence horaire bornée par les 24 h.
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
        """Interroge Modrinth si nécessaire. Renvoie True si une passe a eu lieu."""
        mods = [m for m in self._mods.list_mods() if m.sha1]
        if not mods:
            return False
        hashes = sorted({m.sha1 for m in mods})
        checked_at = self._checks.checked_at()
        known = set(self._checks.statuses())
        fresh = checked_at is not None and self._clock() - checked_at < _RECHECK_AFTER
        if fresh and set(hashes) <= known:
            return False                      # rien de neuf et verdicts récents
        game_version = self._version()
        if not game_version:
            return False                      # version serveur inconnue : on attend
        results = self._catalog.check(hashes, game_version)
        self._checks.replace_all(results, self._clock())
        return True
