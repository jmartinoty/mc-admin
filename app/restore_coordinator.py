"""Coordinateur de restauration — thread de fond (V5.x).

`restore_backup` ne démarre plus mc-restore immédiatement : la sauvegarde de
sécurité du monde courant doit finir D'ABORD (constaté au test réel du
12/07/2026 : lancées en parallèle, mc-backup lisait le monde pendant que
mc-restore l'écrasait — archive de sécurité potentiellement corrompue,
précisément celle dont on aurait besoin si la restauration tourne mal).

Même famille que TempBanScheduler/BackupScheduler : thread stdlib,
`threading.Event.wait` interruptible, le thread ne connaît QUE `AdminService`
(`tick_pending_restore` porte la logique : attente, démarrage, abandon après
deadline), sous un utilisateur système dédié pour que l'audit distingue le
démarrage différé (`auto-restore`) de la demande initiale (l'owner).
"""
from __future__ import annotations

import threading

from domain.model import Permission, Role, User

AUTO_RESTORE_USER = User(
    username="auto-restore",
    role=Role(name="automation", permissions=frozenset({Permission.BACKUP_RESTORE}), grants_all=False),
)


class RestoreCoordinator:
    """Vérifie toutes les `poll_seconds` si une restauration en attente peut
    démarrer (sauvegarde de sécurité terminée) via `tick_pending_restore`."""

    def __init__(self, service, poll_seconds: float = 10.0, sleep=None) -> None:
        self._service = service
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wait = sleep or self._stop.wait

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="restore-coordinator")
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
                self._service.tick_pending_restore(AUTO_RESTORE_USER)
            except Exception:  # noqa: BLE001 — déjà audité par le service ; le thread ne meurt jamais
                pass
