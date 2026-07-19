"""Avertissements dégressifs avant un redémarrage programmé — thread de fond.

Généralise le say+save-all déjà écrit pour la mise à jour (V2.3) : au lieu
d'un avertissement unique juste avant l'arrêt, une série d'avertissements
décroissants jusqu'au redémarrage effectif. Même squelette que
BackupScheduler/TempBanScheduler (thread stdlib, `threading.Event.wait`
interruptible, `sleep` injectable pour les tests) — et même discipline
qu'eux : ce thread ne connaît QUE `AdminService`, jamais directement les
ports (RBAC + audit vivent uniquement dans le service, cf.
`AdminService.tick_scheduled_restart`).
"""
from __future__ import annotations

import threading

from domain.model import Permission, Role, User

SCHEDULED_RESTART_USER = User(
    username="scheduled-restart",
    role=Role(name="automation", permissions=frozenset({Permission.RESTART}), grants_all=False),
)


class RestartWarningScheduler:
    def __init__(self, service, poll_seconds: float = 2.0, sleep=None) -> None:
        self._service = service
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wait = sleep or self._stop.wait

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="restart-warning-scheduler")
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
                self._service.tick_scheduled_restart(SCHEDULED_RESTART_USER)
            except Exception:  # noqa: BLE001 — un souci ne doit jamais tuer le thread
                pass
