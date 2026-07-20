"""Historique de stockage — thread de fond (backlog fiabilité n° 6).

Même discipline que les autres watchers (ArchiveVerifier, PlayerLogWatcher) :
observation passive, écrit directement dans son adapter — pas d'AdminService,
pas de RBAC (seule la LECTURE de l'historique passe par le service). Relevé
peu coûteux (réutilise list_archives()/free_bytes(), déjà lus ailleurs par
l'app) — poll par défaut toutes les heures, mais un seul point PAR JOUR est
conservé (le dernier relevé du jour écrase les précédents dans l'adapter).
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from domain.model import StorageSnapshot


def _family(filename: str) -> str:
    """Premier segment du chemin de l'archive (manual/scheduled/profiles/
    restore-safety/legacy) — jamais un dossier par PROFIL individuel :
    stable même si des profils sont ajoutés ou supprimés entre deux passes."""
    return filename.split("/", 1)[0] if "/" in filename else filename


def compute_snapshot(archives, free_bytes: int | None, today: str) -> StorageSnapshot:
    """Pure : agrège la taille par famille — testable sans thread ni fichier."""
    families: dict[str, int] = {}
    for archive in archives:
        family = _family(archive.filename)
        families[family] = families.get(family, 0) + archive.size_bytes
    return StorageSnapshot(date=today, free_bytes=free_bytes, families=families)


class StorageHistoryWatcher:
    def __init__(self, archives, history, poll_seconds: float = 3600.0,
                sleep=None, clock=None) -> None:
        self._archives = archives      # BackupArchivesPort
        self._history = history        # StorageHistoryPort
        self._poll_seconds = poll_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wait = sleep or self._stop.wait

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="storage-history")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            interrupted = self._wait(self._poll_seconds)
            if interrupted or self._stop.is_set():
                break
            try:
                self.tick()
            except Exception:  # noqa: BLE001 — le thread ne meurt jamais
                pass

    def tick(self) -> None:
        archives = self._archives.list_archives()
        free_bytes = self._archives.free_bytes()
        today = self._clock().date().isoformat()
        self._history.record_today(compute_snapshot(archives, free_bytes, today))
