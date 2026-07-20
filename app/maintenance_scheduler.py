"""Compte à rebours d'une fermeture pour maintenance — thread de fond.

Même squelette que `RestartWarningScheduler` (thread stdlib, `Event.wait`
interruptible, `sleep` injectable) et même discipline : ce thread ne connaît
QUE `AdminService.tick_maintenance` — jamais les ports. La RBAC et l'audit
restent dans le service, sous une identité système dédiée pour que le journal
distingue une fermeture annoncée (compte à rebours écoulé) d'un clic humain.
"""
from __future__ import annotations

import threading

from domain.model import Permission, Role, User

MAINTENANCE_USER = User(
    username="maintenance",
    role=Role(
        name="automation",
        permissions=frozenset({Permission.MAINTENANCE}),
        grants_all=False,
    ),
)


class MaintenanceScheduler:
    def __init__(self, service, poll_seconds: float = 2.0, sleep=None) -> None:
        self._service = service
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wait = sleep or self._stop.wait

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="maintenance-scheduler"
        )
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
                self._service.tick_maintenance(MAINTENANCE_USER)
            except Exception:  # noqa: BLE001 — un souci ne doit jamais tuer le thread
                pass
