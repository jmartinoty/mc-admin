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
    MaintenanceUnavailable,
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
    IncidentLogPort,
    BackupArchivesPort,
    BackupPort,
    BackupProfilesPort,
    ArchiveChecksPort,
    ArchiveValidatorPort,
    WorkerIntegrityPort,
    BansPort,
    Clock,
    ContainerPort,
    DoormanPort,
    GamePort,
    LogPort,
    MetricsPort,
    ModChecksPort,
    ModsPort,
    NotificationPort,
    OpLevelsApplyPort,
    OpsPort,
    PendingMaintenancePort,
    PendingOpLevelsPort,
    PendingRestorePort,
    ProfileBackupPort,
    PlayerHistoryPort,
    PlayerStatsPort,
    RecurringRestartPort,
    ScheduledMaintenancePort,
    RestartSchedulerPort,
    RestorePort,
    AlertThresholdsPort,
    ServerDiscoveryPort,
    WatchedContainersPort,
    ServersPort,
    StorageHistoryPort,
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
_UPDATE_WATCH_USER = User(
    username="update-watch",
    role=Role(name="automation",
              permissions=frozenset({Permission.UPDATE}),
              grants_all=False),
)

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
    "app_update", "health", "performance", "disk", "restore",
)
# Défauts pour une clé ABSENTE d'un canal existant (migrations douces) :
# « update » était toujours envoyé avant d'être débrayable -> reste actif ;
# les nouveaux filtres sont opt-in (pas de spam surprise). « app_update »
# (mise à jour de mc-admin LUI-MÊME) est volontairement opt-in AUSSI : c'est
# la notification de sa propre action, Jeremy la recevait en double à chaque
# release (27/07/2026) — qui la veut la coche.
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
        doorman: DoormanPort | None = None,
        pending_maintenance: PendingMaintenancePort | None = None,
        recurring_restart: RecurringRestartPort | None = None,
        scheduled_maintenance: ScheduledMaintenancePort | None = None,
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
        map_probe=None,
        app_update_state=None,
        app_update_snooze=None,
        app_updater=None,
        self_image=None,
        storage_history: StorageHistoryPort | None = None,
        alert_thresholds: AlertThresholdsPort | None = None,
        incidents: IncidentLogPort | None = None,
    ) -> None:
        self._game = game
        self._map_probe = map_probe
        self._app_update_state = app_update_state
        self._app_update_snooze = app_update_snooze
        self._app_updater = app_updater
        self._self_image = self_image
        self._container = container
        self._restore_safety_backup = (
            restore_safety_backup if restore_safety_backup is not None else _NoBackup()
        )
        self._restore = restore
        self._incidents = incidents
        self._doorman = doorman
        self._pending_maintenance = pending_maintenance
        self._pending_restore = pending_restore
        self._player_stats = player_stats
        self._archive_checks = archive_checks
        self._archive_validator = archive_validator
        self._worker_integrity = worker_integrity
        self._storage_history = storage_history
        self._alert_thresholds = alert_thresholds
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
        self._scheduled_maintenance = scheduled_maintenance
        self._backup_archives = backup_archives
        self._bans = bans
        self._temp_bans = temp_bans
        self._restart_schedule = restart_schedule
        self._pending_op_levels = pending_op_levels
        self._op_levels_apply = op_levels_apply
        self._op_levels_operation: tuple[User, Permission, str, str] | None = None
        self._op_levels_lock = threading.Lock()
        # Ops programmées mises « en attente » car des joueurs sont connectés
        # (garde-fou « ne pas exécuter tant que joueurs connectés ») : on
        # mémorise les operation_id déjà annoncés pour ne prévenir qu'UNE fois
        # par report, pas à chaque tick. Transitoire (même portée que l'op).
        self._deferred_ops_announced: set[str] = set()
        # Mise à jour du serveur EN COURS (one-shot mc-updater lancé) : sert à
        # constater son ISSUE une fois le conteneur terminé — sans ça on ne
        # saurait dire que « lancée ». (operation_id, version d'avant).
        self._update_operation: tuple[str, str | None] | None = None
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

    def _refuse_if_maintenance(self, user: User, permission: Permission, hint: str) -> None:
        """Refuse une action qui DÉMARRERAIT le serveur pendant une maintenance.

        Le portier occupe l'adresse statique du serveur : Docker refuserait le
        démarrage (« Address already in use ») et l'échec remontait jusqu'à une
        erreur 500 (constaté par Jeremy le 31/07 en cliquant « Démarrer »
        pendant une maintenance). On refuse AVANT d'agir, avec le geste correct
        à faire. Un état de portier illisible ne bloque pas : l'action
        échouerait alors proprement d'elle-même.
        """
        doorman = getattr(self, "_doorman", None)
        if doorman is None:
            return
        try:
            if not doorman.is_running():
                return
        except Exception:  # noqa: BLE001 — état inconnu : ne pas bloquer à tort
            return
        self._record(user, permission, "denied", "bloqué : serveur en maintenance")
        raise MaintenanceUnavailable(
            f"Le serveur est en maintenance : {hint} (le portier occupe son adresse réseau)."
        )

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

