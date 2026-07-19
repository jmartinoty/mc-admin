"""Planificateur de sauvegardes internes — thread de fond, stdlib uniquement.

Réutilise le MÊME chemin métier que le bouton « Lancer maintenant » de l'UI
(`AdminService.trigger_profile_backup`), sous un utilisateur système dédié : aucune
duplication de RBAC/audit/notifications, aucun nouveau concept dans le
domaine — une sauvegarde planifiée est auditée et notifiée exactement comme
une sauvegarde manuelle, avec `username="scheduler"` dans le journal.

Ce module vit dans `app/` (composition), pas dans `app/domain/` : c'est un
ADAPTER PILOTANT (driving) de plus, au même titre que les routes HTTP — il
appelle le service, il n'est jamais appelé par lui.
"""
from __future__ import annotations

import threading

from domain.model import Permission, Role, User

# Rôle construit en code, pas en YAML : ce n'est pas un compte connectable
# (pas de password_hash, n'apparaît jamais dans config/roles.yml), seulement
# un identifiant pour le passage dans AdminService/RBAC/audit.
SYSTEM_USER = User(
    username="scheduler",
    role=Role(name="automation", permissions=frozenset({Permission.BACKUP_TRIGGER}), grants_all=False),
)


class BackupScheduler:
    """Poll le service (~60 s) : `tick_backup_profiles` décide quels profils
    sont arrivés à échéance — planification et dernier déclenchement sont
    PERSISTÉS par profil (BackupProfilesPort) et réglables depuis l'UI. Ce
    thread ne connaît que AdminService, même discipline que
    RestartWarningScheduler.
    """

    def __init__(self, service, poll_seconds: float = 60.0, sleep=None) -> None:
        self._service = service
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # `sleep` injectable : en prod, `Event.wait` (interruptible) ; en test,
        # un fake qui avance une horloge virtuelle sans vraiment attendre.
        self._wait = sleep or self._stop.wait

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="backup-scheduler")
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
                self._service.tick_backup_profiles(SYSTEM_USER)
            except Exception:  # noqa: BLE001 — déjà audité/notifié par le service ; le thread ne meurt jamais
                pass
