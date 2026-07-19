"""Adapter PendingRestorePort : état en mémoire d'une restauration en attente.

Volontairement NON persistant (même compromis que InMemoryRestartSchedule) :
si mc-admin redémarre pendant l'attente, la restauration est simplement
oubliée — le monde n'a PAS été touché (mc-restore n'a jamais démarré), la
sauvegarde de sécurité se termine seule, l'owner reclique. C'est le mode
d'échec le plus sûr possible.
"""
from __future__ import annotations

import threading

from domain.model import PendingRestore


class InMemoryPendingRestore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: PendingRestore | None = None

    def set(self, pending: PendingRestore) -> None:
        with self._lock:
            self._state = pending

    def get(self) -> PendingRestore | None:
        with self._lock:
            return self._state

    def clear(self) -> bool:
        with self._lock:
            had = self._state is not None
            self._state = None
            return had
