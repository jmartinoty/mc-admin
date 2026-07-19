"""AdminService : orchestrateur métier.

Responsabilités (toutes CÔTÉ SERVEUR, indépendantes du transport) :
  1. appliquer la RBAC (deny-by-default) sur CHAQUE action ;
  2. journaliser l'audit (qui/quoi/quand/résultat) ;
  3. orchestrer les ports, sans connaître leur implémentation.

Politique d'audit (cf. CLAUDE.md §7.4) :
  - tout REFUS RBAC est journalisé (y compris sur les lectures) ;
  - toute action MUTANTE journalise succès ET erreur ;
  - les LECTURES réussies (status/logs) ne sont pas journalisées, pour ne pas
    inonder le journal sous le polling de l'UI.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime

from domain.errors import (
    BackupUnavailable,
    PermissionDenied,
)
from domain.model import (
    AuditEntry,
    BackupOutcome,
    BackupResult,
    Permission,
    Role,
    RestoreOperation,
    User,
)
from domain.ports import (
    AuditPort,
    BackupArchivesPort,
    BackupPort,
    BackupProfilesPort,
    ArchiveChecksPort,
    ArchiveValidatorPort,
    WorkerIntegrityPort,
    BansPort,
    Clock,
    ContainerPort,
    GamePort,
    LogPort,
    MetricsPort,
    ModChecksPort,
    ModsPort,
    NotificationPort,
    OpLevelsApplyPort,
    OpsPort,
    PendingOpLevelsPort,
    PendingRestorePort,
    ProfileBackupPort,
    PlayerHistoryPort,
    PlayerStatsPort,
    RecurringRestartPort,
    RestartSchedulerPort,
    RestorePort,
    ServerDiscoveryPort,
    WatchedContainersPort,
    ServersPort,
    TempBanPort,
    UpdatePort,
)


# Format officiel des pseudos Minecraft. Validé avant TOUT envoi RCON.
_PLAYER_NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


def _format_seconds(seconds: int) -> str:
    if seconds >= 60:
        return f"{seconds // 60} minute(s)"
    return f"{seconds} seconde(s)"


# Identité système des ISSUES de sauvegardes : l'issue est constatée par
# l'observation de fond (pas une action utilisateur), l'audit doit le montrer.
_BACKUP_WATCH_USER = User(
    username="backup-watch",
    # BACKUP_RETENTION en plus du TRIGGER : l'issue d'une sauvegarde applique
    # automatiquement la politique de rétention du profil (V6).
    role=Role(name="automation",
              permissions=frozenset({Permission.BACKUP_TRIGGER, Permission.BACKUP_RETENTION}),
              grants_all=False),
)


# Types de canaux de notification (liste FERMÉE — politique du domaine ;
# l'UI ne propose que ceux-ci, l'adapter les construit) : type -> champs requis.
NOTIFICATION_CHANNEL_TYPES: dict[str, tuple[str, ...]] = {
    "discord": ("webhook_url",),
    "telegram": ("bot_token", "chat_id"),
}
# Événements débrayables par canal (les échecs d'actions ne le sont jamais).
EVENT_KEYS: tuple[str, ...] = (
    "player", "new_player", "moderation", "backup", "restart", "update",
    "health", "performance", "disk", "restore",
)
# Défauts pour une clé ABSENTE d'un canal existant (migrations douces) :
# « update » était toujours envoyé avant d'être débrayable -> reste actif ;
# les nouveaux filtres sont opt-in (pas de spam surprise).
EVENT_LEGACY_DEFAULTS: dict[str, bool] = {"update": True}


class _NoBackup:
    """Sauvegarde de sécurité non configurée : ne tourne jamais, refuse de
    déclencher — les appelants n'ont pas à connaître l'absence du port."""

    def trigger(self) -> BackupResult:
        raise BackupUnavailable("aucun conteneur de sauvegarde de sécurité configuré")

    def is_running(self) -> bool:
        return False

    def exit_code(self) -> int | None:
        return None




class ServiceCore:
    def __init__(
        self,
        *,
        game: GamePort,
        container: ContainerPort,
        logs: LogPort,
        audit: AuditPort,
        clock: Clock,
        container_name: str,
        metrics: MetricsPort,
        updater: UpdatePort,
        ops: OpsPort,
        notifications: NotificationPort,
        player_history: PlayerHistoryPort,
        backup_archives: BackupArchivesPort,
        bans: BansPort,
        temp_bans: TempBanPort,
        restart_schedule: RestartSchedulerPort,
        pending_op_levels: PendingOpLevelsPort | None = None,
        op_levels_apply: OpLevelsApplyPort | None = None,
        restore_safety_backup: BackupPort | None = None,
        restore: RestorePort | None = None,
        recurring_restart: RecurringRestartPort | None = None,
        pending_restore: PendingRestorePort | None = None,
        player_stats: PlayerStatsPort | None = None,
        archive_checks: ArchiveChecksPort | None = None,
        archive_validator: ArchiveValidatorPort | None = None,
        worker_integrity: WorkerIntegrityPort | None = None,
        backup_profiles: BackupProfilesPort | None = None,
        profile_backup: ProfileBackupPort | None = None,
        world_dir: str = "world",
        mods: ModsPort | None = None,
        servers: ServersPort | None = None,
        server_discovery: ServerDiscoveryPort | None = None,
        watched: WatchedContainersPort | None = None,
        companion_port_factory=None,
        notification_config=None,
        mod_checks: ModChecksPort | None = None,
        spark=None,
    ) -> None:
        self._game = game
        self._container = container
        self._restore_safety_backup = (
            restore_safety_backup if restore_safety_backup is not None else _NoBackup()
        )
        self._restore = restore
        self._pending_restore = pending_restore
        self._player_stats = player_stats
        self._archive_checks = archive_checks
        self._archive_validator = archive_validator
        self._worker_integrity = worker_integrity
        self._backup_profiles = backup_profiles
        self._profile_backup = profile_backup
        self._world_dir = world_dir
        self._mods = mods
        self._servers = servers
        self._server_discovery = server_discovery
        self._watched = watched
        self._companion_port_factory = companion_port_factory
        self._notification_config = notification_config
        self._mod_checks = mod_checks
        self._spark = spark
        self._recurring_restart = recurring_restart
        self._backup_archives = backup_archives
        self._bans = bans
        self._temp_bans = temp_bans
        self._restart_schedule = restart_schedule
        self._pending_op_levels = pending_op_levels
        self._op_levels_apply = op_levels_apply
        self._op_levels_operation: tuple[User, Permission, str, str] | None = None
        self._op_levels_lock = threading.Lock()
        self._logs = logs
        self._audit = audit
        self._clock = clock
        self._container_name = container_name
        self._metrics = metrics
        self._updater = updater
        self._ops = ops
        self._notify = notifications
        self._player_history = player_history
        self._restore_operation: RestoreOperation | None = None
        self._backup_started_at: dict[str, datetime] = {}
        self._backup_progress_max: dict[str, int] = {}
        self._backup_result: BackupOutcome | None = None
        self._backup_operation_ids: dict[str, str] = {}
        self._safety_validation_filename: str | None = None
        self._active_profile_id: str | None = None
        self._backup_restore_lock = threading.Lock()

    # ---- helpers internes : audit + RBAC ----

    def _record(self, user: User, permission: Permission, outcome: str, detail: str = "") -> None:
        self._audit.record(
            AuditEntry(
                timestamp=self._clock.now(),
                username=user.username,
                role=user.role.name,
                action=permission.value,
                target=self._container_name,
                outcome=outcome,
                detail=detail,
            )
        )

    def _authorize(self, user: User, permission: Permission) -> None:
        """Barrière RBAC. Journalise le refus AVANT de lever (audit #4)."""
        if not user.can(permission):
            self._record(user, permission, "denied")
            raise PermissionDenied(user.username, permission.value)

    def _restore_event(self, user: User, phase: str, outcome: str = "allowed", **fields) -> None:
        parts = [f"phase={phase}"]
        for key, value in fields.items():
            if value is None:
                continue
            text = str(value).replace("\n", " ").strip()
            if " " in text:
                text = '"' + text.replace('"', "'") + '"'
            parts.append(f"{key}={text}")
        self._record(user, Permission.BACKUP_RESTORE, outcome, " ".join(parts))

