"""Planificateur de levée automatique des bans temporisés — thread de fond.

Réutilise le MÊME chemin que la levée manuelle (`AdminService.pardon`), sous
un utilisateur système DÉDIÉ (distinct de celui du `BackupScheduler` : un nom
distinct clarifie l'origine dans le journal d'audit) : aucune duplication de
RBAC/audit/notifications, et `pardon()` annule lui-même l'entrée programmée
(cohérence assurée par le domaine, pas par ce module).

Même famille que `app/scheduler.py` : thread stdlib, `threading.Event.wait`
interruptible, ne meurt jamais sur une erreur de pardon isolée.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from domain.model import Permission, Role, User

AUTO_UNBAN_USER = User(
    username="auto-unban",
    role=Role(name="automation", permissions=frozenset({Permission.BAN_MANAGE}), grants_all=False),
)


class TempBanScheduler:
    """Vérifie toutes les `poll_seconds` s'il existe des bans temporisés
    arrivés à expiration, et les lève via `AdminService.pardon`."""

    def __init__(self, service, temp_bans, poll_seconds: float = 60.0, sleep=None, clock=None) -> None:
        self._service = service
        self._temp_bans = temp_bans
        self._poll_seconds = poll_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wait = sleep or self._stop.wait

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="tempban-scheduler")
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
            for entry in self._temp_bans.due(self._clock()):
                try:
                    self._service.pardon(AUTO_UNBAN_USER, entry.player)
                except Exception:  # noqa: BLE001 — déjà audité par le service ; le thread ne meurt jamais
                    pass
