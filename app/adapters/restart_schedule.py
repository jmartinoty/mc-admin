"""Adapter RestartSchedulerPort : état en mémoire d'un redémarrage programmé.

Volontairement NON persistant (contrairement à JsonTempBans) : un
redémarrage programmé est une action ponctuelle de courte portée — le
perdre si mc-admin lui-même redémarre est un compromis acceptable, pas un
bug (contrairement à un ban temporisé qui peut courir sur plusieurs jours).

Garde aussi le suivi des seuils d'avertissement déjà annoncés pour la
programmation EN COURS (`take_due_warning`) : les seuils déjà dépassés AU
MOMENT DE LA PROGRAMMATION elle-même (ex. "10 min" pour un délai de 4 min)
sont pré-marqués comme annoncés dans `schedule()`, pour ne jamais être
diffusés rétroactivement une fois le délai écoulé.
"""
from __future__ import annotations

import threading
from datetime import datetime

from domain.model import ScheduledRestart

# Secondes avant l'échéance auxquelles un avertissement est dû, du plus loin
# au plus proche.
_DEFAULT_THRESHOLDS = (600, 300, 60, 30, 10)


class InMemoryRestartSchedule:
    def __init__(self, warning_thresholds: tuple[int, ...] = _DEFAULT_THRESHOLDS) -> None:
        self._lock = threading.Lock()
        self._state: ScheduledRestart | None = None
        self._thresholds = tuple(sorted(warning_thresholds, reverse=True))
        self._announced: set[int] = set()

    def schedule(self, restart_at: datetime, username: str, total_seconds: float) -> None:
        with self._lock:
            self._state = ScheduledRestart(restart_at=restart_at, scheduled_by=username)
            # Seuils déjà dans le passé pour CE délai (ex. programmer 4 min à
            # l'avance ne doit jamais annoncer "10 min") : pré-marqués ici,
            # une fois pour toutes, plutôt que déduits plus tard d'un "now"
            # de sondage qui dépend du timing du thread de fond.
            self._announced = {t for t in self._thresholds if t > total_seconds}

    def cancel(self) -> bool:
        with self._lock:
            had = self._state is not None
            self._state = None
            self._announced = set()
            return had

    def status(self) -> ScheduledRestart | None:
        with self._lock:
            return self._state

    def take_due_warning(self, now: datetime) -> int | None:
        with self._lock:
            if self._state is None:
                return None
            remaining = (self._state.restart_at - now).total_seconds()
            for threshold in self._thresholds:
                if remaining <= threshold and threshold not in self._announced:
                    self._announced.add(threshold)
                    return threshold
            return None
