"""Adapter PendingMaintenancePort : fermeture pour maintenance ANNONCÉE.

Même compromis (et presque le même corps) que `InMemoryRestartSchedule` :
volontairement NON persistant. Tant que l'échéance n'est pas atteinte, aucun
serveur n'est fermé — perdre l'annonce si mc-admin redémarre ne laisse donc
jamais le système dans un état incohérent. L'état RÉEL de la maintenance
(portier en poste ou non) n'est jamais déduit d'ici : il est relu depuis
Docker via `DoormanPort.is_running()`.

Les seuils déjà dépassés AU MOMENT DE L'ANNONCE sont pré-marqués (mêmes
raisons que pour le redémarrage programmé : annoncer « dans 10 minutes »
pour un délai de 4 minutes serait un mensonge).
"""
from __future__ import annotations

import threading
from datetime import datetime

from domain.model import PendingMaintenance

_DEFAULT_THRESHOLDS = (600, 300, 60, 30, 10)


class InMemoryPendingMaintenance:
    def __init__(self, warning_thresholds: tuple[int, ...] = _DEFAULT_THRESHOLDS) -> None:
        self._lock = threading.Lock()
        self._state: PendingMaintenance | None = None
        self._thresholds = tuple(sorted(warning_thresholds, reverse=True))
        self._announced: set[int] = set()

    def schedule(
        self,
        engage_at: datetime,
        username: str,
        motd: str,
        kick: str,
        total_seconds: float,
    ) -> None:
        with self._lock:
            self._state = PendingMaintenance(
                engage_at=engage_at,
                requested_by=username,
                motd=motd,
                kick=kick,
                operation_id=f"maintenance::{engage_at.isoformat()}",
            )
            self._announced = {t for t in self._thresholds if t > total_seconds}

    def cancel(self) -> bool:
        with self._lock:
            had = self._state is not None
            self._state = None
            self._announced = set()
            return had

    def status(self) -> PendingMaintenance | None:
        with self._lock:
            return self._state

    def take_due_warning(self, now: datetime) -> int | None:
        with self._lock:
            if self._state is None:
                return None
            remaining = (self._state.engage_at - now).total_seconds()
            for threshold in self._thresholds:
                if remaining <= threshold and threshold not in self._announced:
                    self._announced.add(threshold)
                    return threshold
            return None
