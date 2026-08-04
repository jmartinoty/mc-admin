"""Ports : interfaces que le domaine attend, implémentées par les adapters.

On utilise `typing.Protocol` (typage structurel) : les adapters n'ont pas besoin
d'hériter, et les tests fournissent de simples fakes. Le domaine ne dépend que
de ces signatures, jamais d'une techno concrète (RCON, docker, fichier…).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from .model import (
    AlertThresholds,
    AppRelease,
    AppUpdateSnooze,
    ArchiveCheck,
    AuditEntry,
    BackupProfile,
    DetectedServer,
    BackupArchive,
    BackupResult,
    BanEntry,
    ContainerState,
    Incident,
    MetricReading,
    PerformanceSnapshot,
    ModInfo,
    ModUpdate,
    OpEntry,
    PendingMaintenance,
    ScheduledMaintenance,
    PendingOpLevel,
    PendingRestore,
    PendingUnban,
    PlayerStats,
    RecurringRestart,
    Player,
    PlayerSession,
    PlayerSummary,
    ScheduledRestart,
    ServerEntry,
    StorageSnapshot,
    UpdateStatus,
    WorkerCheck,
)


class GamePort(Protocol):
    """Commandes in-game via RCON. Peut lever ServerUnavailable si injoignable."""

    def list_players(self) -> list[Player]: ...
    def save_all(self) -> None: ...
    def say(self, message: str) -> None: ...
    def raw(self, command: str) -> str: ...
    def stop(self) -> None: ...
    def whitelist_list(self) -> list[str]: ...
    def whitelist_add(self, player_name: str) -> str: ...
    def whitelist_remove(self, player_name: str) -> str: ...
    def op(self, player_name: str) -> str: ...
    def deop(self, player_name: str) -> str: ...
    def kick(self, player_name: str, reason: str = "") -> str: ...
    def ban(self, player_name: str, reason: str = "") -> str: ...
    def pardon(self, player_name: str) -> str: ...
    # Réglages de jeu (V5) — les VALEURS sont validées par le domaine (listes
    # blanches fermées) avant tout envoi : jamais de texte libre ici.
    def get_difficulty(self) -> str | None: ...
    def set_difficulty(self, level: str) -> str: ...
    def get_gamerule(self, name: str) -> bool | None: ...
    def set_gamerule(self, name: str, value: bool | int | str) -> str: ...
    def list_gamerule_names(self) -> list[str]: ...
    def get_gamerules(self, names: list[str]) -> dict[str, str]: ...
    def set_weather(self, kind: str) -> str: ...
    def set_time(self, moment: str) -> str: ...


class ServersPort(Protocol):
    """Serveurs administrés (V6.3) : persistés, mutations via AdminService
    (barrière SERVER_MANAGE). Seule la première entrée est pilotée pour
    l'instant (adapters construits au démarrage) — le reste attend la V7."""

    def list_servers(self) -> list[ServerEntry]: ...
    def add(self, entry: ServerEntry) -> None: ...
    def set_address(self, server_id: str, address: str) -> bool: ...
    def set_map_url(self, server_id: str, map_url: str) -> bool: ...


class MapProbePort(Protocol):
    """Test EN DIRECT d'une adresse de carte web (assistant page Carte).
    Contrat : ne lève jamais — verdict (ok, détail en langage utilisateur)."""

    def probe(self, url: str) -> tuple[bool, str]: ...


class AppReleaseCatalogPort(Protocol):
    """Dernière release publiée de mc-admin (bouton MAJ).
    Contrat : None sur toute erreur — le checker garde le dernier verdict."""

    def latest(self) -> AppRelease | None: ...


class AppUpdateStatePort(Protocol):
    """Verdict persisté du checker de mise à jour (/data/app_update.json)."""

    def get(self) -> tuple[AppRelease, datetime] | None: ...
    def set(self, release: AppRelease, checked_at: datetime) -> None: ...


class AppUpdateSnoozePort(Protocol):
    """Report du bandeau MAJ (« me le rappeler plus tard »)."""

    def get(self) -> AppUpdateSnooze | None: ...
    def set(self, snooze: AppUpdateSnooze) -> None: ...


class AppUpdaterPort(Protocol):
    """Déclenche le one-shot privilégié `mc-admin-updater` (pull + recréation
    de mc-admin ET des one-shots). Lève UpdateUnavailable si non démarrable."""

    def start(self) -> None: ...


class SelfImagePort(Protocol):
    """Image du conteneur mc-admin COURANT (détection du mode d'installation :
    image ghcr = bouton MAJ applicable, build git local = géré par le dépôt).
    Contrat : chaîne vide si indéterminable."""

    def image(self) -> str: ...


class ServerDiscoveryPort(Protocol):
    """Détection des serveurs Minecraft existants (V6.3) — en mode Docker :
    conteneurs dont l'image/le port évoquent Minecraft, via le listage déjà
    autorisé par le proxy. Best-effort : jamais d'erreur, liste vide."""

    def discover(self, configured: set[str]) -> list[DetectedServer]: ...

    def list_all(self) -> list[DetectedServer]: ...


class WatchedContainersPort(Protocol):
    """Liste des conteneurs COMPAGNONS surveillés (A2.1) — source de vérité
    unique (watched_containers.json), relue par le HealthWatcher à chaque
    sonde et modifiée depuis l'UI (page Surveillance)."""

    def all(self) -> list[str]: ...

    def add(self, name: str) -> bool: ...

    def remove(self, name: str) -> bool: ...


class ContainerPort(Protocol):
    """Cycle de vie du conteneur via le socket-proxy.

    L'implémentation est liée à UN conteneur (MINECRAFT_CONTAINER) et doit
    refuser toute autre cible (UnauthorizedContainer) — cf. CLAUDE.md §7.1.
    """

    def status(self) -> ContainerState: ...
    def restart(self) -> None: ...
    def start(self) -> None: ...
    # `timeout` = secondes laissées au serveur pour s'arrêter proprement avant
    # le SIGKILL. Absent = valeur par défaut de l'implémentation. Mesuré au
    # spike du 19/07/2026 : Minecraft demande bien plus que les 10 s par défaut
    # de Docker pour sauvegarder ses mondes (arrêt de maintenance).
    def stop(self, timeout: int | None = None) -> None: ...


class BackupPort(Protocol):
    """Déclenchement de sauvegarde, indépendant de Docker et SANS docker exec.

    `is_running` sert au séquencement d'une restauration (V5.x) : mc-restore
    ne doit démarrer qu'une fois la sauvegarde de sécurité terminée.
    """

    def trigger(self) -> BackupResult: ...
    def is_running(self) -> bool: ...
    def exit_code(self) -> int | None: ...


class BackupProfilesPort(Protocol):
    """Profils de sauvegarde (V6) : quoi/où/quand/rétention, persistés.
    Mutations UNIQUEMENT via AdminService (barrière BACKUP_MANAGE) ; le
    planificateur de fond passe par le service, jamais par ce port."""

    def list_profiles(self) -> list[BackupProfile]: ...
    def get(self, profile_id: str) -> BackupProfile | None: ...
    def upsert(self, profile: BackupProfile) -> None: ...
    def delete(self, profile_id: str) -> bool: ...
    def mark_run(self, profile_id: str, at: datetime) -> None: ...


class ProfileBackupPort(Protocol):
    """Déclenchement du conteneur de sauvegarde GÉNÉRIQUE (V6) : la consigne
    (destination, contenu) est écrite dans un fichier lu par le conteneur à
    son démarrage — même pattern que RestorePort, aucun droit Docker
    supplémentaire côté mc-admin. Les motifs viennent de la liste FERMÉE de
    catégories (adapters.backup_profiles.includes_for), jamais de l'UI."""

    def trigger_profile(self, dest: str, name: str, includes: str, excludes: str) -> BackupResult: ...
    def is_running(self) -> bool: ...
    def exit_code(self) -> int | None: ...


class BackupArchivesPort(Protocol):
    """Lecture seule des archives déjà produites (page /backups, V4.2).

    Port séparé de BackupPort (même logique que OpsPort/GamePort) : lister des
    fichiers est un concern différent de déclencher une sauvegarde via Docker.
    """

    def list_archives(self) -> list[BackupArchive]: ...
    def archive_path(self, filename: str) -> Path | None: ...
    def delete_archive(self, filename: str) -> bool: ...  # scheduled/ uniquement
    def free_bytes(self) -> int | None: ...               # espace libre du volume (None = illisible)


class StorageHistoryPort(Protocol):
    """Historique quotidien de l'occupation des sauvegardes (backlog
    fiabilité n° 6). Écrit par StorageHistoryWatcher (observation passive,
    comme ArchiveVerifier — pas de mutation via AdminService), lu par la
    page /backups. Un point PAR JOUR : `record_today` remplace le point du
    jour courant s'il existe déjà (plusieurs passes/jour = le dernier relevé
    du jour gagne), l'historique des jours passés n'est jamais modifié."""

    def record_today(self, snapshot: StorageSnapshot) -> None: ...
    def history(self, days: int = 30) -> list[StorageSnapshot]: ...  # tri croissant par date


class AlertThresholdsPort(Protocol):
    """Seuils d'alerte de PerfWatcher (MSPT, espace disque, durée d'incident
    avant alerte) — réglables dans l'UI (backlog fiabilité n° 4)."""

    def get(self) -> AlertThresholds: ...
    def set(self, thresholds: AlertThresholds) -> None: ...


class ArchiveChecksPort(Protocol):
    """Résultats de vérification d'intégrité des archives (V5.x).

    `record` est appelé par le vérificateur de fond (`app/archive_verifier.py`)
    — PAS via AdminService : observation passive, comme PlayerLogWatcher.
    Seule la LECTURE (`statuses`) passe par le service."""

    def statuses(self) -> dict[str, ArchiveCheck]: ...
    def record(self, check: ArchiveCheck) -> None: ...
    def checking(self) -> str | None: ...  # archive en cours de vérification (affichage)


class ArchiveValidatorPort(Protocol):
    """Relit immédiatement une archive avant une opération destructive."""

    def validate(self, filename: str) -> ArchiveCheck: ...


class WorkerIntegrityPort(Protocol):
    """Alignement des one-shots pré-créés (image + réseaux) avec l'app.

    `issues(kind)` renvoie les problèmes en langage utilisateur pour une
    famille d'opérations ("restore", "backup") ; `include_unknown` ajoute
    les vérifications impossibles (fail-closed, réservé au préflight de
    restauration — la seule opération destructive). `statuses()` alimente
    le diagnostic de la page Serveurs."""

    def issues(self, kind: str, *, include_unknown: bool = False) -> list[str]: ...
    def statuses(self) -> list[WorkerCheck]: ...


class RestorePort(Protocol):
    """Restauration d'une archive (V5.7) — écrase le monde en cours.

    Comme BackupPort/UpdatePort : le port ignore Docker. L'adapter déclenche un
    conteneur `mc-restore` one-shot PRIVILÉGIÉ (il monte le socket Docker pour
    arrêter/redémarrer minecraft, jamais mc-admin) en lui passant l'archive
    cible (chemin relatif dans /backups). mc-admin ne touche jamais au monde.
    """

    def restore(self, archive_relpath: str) -> None: ...
    def is_running(self) -> bool: ...
    def exit_code(self) -> int | None: ...


class DoormanPort(Protocol):
    """Portier de maintenance (mc-doorman) : occupe l'endpoint réseau du
    serveur PENDANT qu'il est volontairement arrêté et répond aux joueurs
    (MOTD maintenance, refus de connexion expliqué).

    Comme RestorePort : le port ignore Docker et le placement réseau. Notre
    adapter démarre un one-shot qui reprend l'IP statique ciblée par playit
    (spike 19/07/2026) ; un mode hôte (V7.2) ferait un simple bind du port —
    même contrat, placement différent.
    """

    def engage(self, motd: str, kick: str) -> None: ...
    def release(self) -> None: ...
    def is_running(self) -> bool: ...
    def prime(self, motd: str, kick: str) -> None:
        """Écrit la consigne SANS prendre le poste : utilisé quand c'est un
        AUTRE processus (le worker mc-restore) qui démarrera le portier au bon
        moment. Écrire la consigne reste le rôle de mc-admin."""
        ...


class PendingMaintenancePort(Protocol):
    """Fermeture pour maintenance ANNONCÉE mais pas encore engagée (délai de
    grâce laissé aux joueurs connectés).

    En mémoire, non persistant — même compromis assumé que
    RestartSchedulerPort : perdre l'annonce si mc-admin redémarre pendant le
    délai est acceptable (aucun serveur n'est fermé tant que l'échéance n'est
    pas atteinte), contrairement au portier lui-même dont l'état RÉEL est
    toujours relu depuis Docker.
    """

    def schedule(
        self,
        engage_at: datetime,
        username: str,
        motd: str,
        kick: str,
        total_seconds: float,
        defer_if_players: bool = False,
    ) -> None: ...
    def cancel(self) -> bool: ...
    def status(self) -> PendingMaintenance | None: ...
    def take_due_warning(self, now: datetime) -> int | None: ...


class PendingRestorePort(Protocol):
    """État (en mémoire, non persistant — même compromis assumé que
    RestartSchedulerPort) d'une restauration en attente de sa sauvegarde de
    sécurité (V5.x). `set`/`get`/`clear` UNIQUEMENT depuis AdminService ; le
    thread de fond (RestoreCoordinator) ne connaît que le service."""

    def set(self, pending: PendingRestore) -> None: ...
    def get(self) -> PendingRestore | None: ...
    def clear(self) -> bool: ...


class LogPort(Protocol):
    """Lecture des logs récents (fichier latest.log monté en lecture seule)."""

    def recent(self, lines: int = 100) -> list[str]: ...


class UpdatePort(Protocol):
    """Mise à jour contrôlée du serveur (V2.3).

    `check()` compare la version qui tourne à la dernière release (peut lever
    ServerUnavailable si les sources de version sont injoignables).
    `apply()` déclenche le conteneur `mc-updater` one-shot — c'est LUI qui porte
    les droits Docker étendus (pull/recreate), jamais mc-admin (CLAUDE.md §8).

    `is_running()`/`exit_code()` permettent de constater l'ISSUE de la mise à
    jour (le one-shot travaille en asynchrone : sans ça, on ne saurait dire que
    « lancée »). Même contrat que BackupPort : `is_running()` LÈVE si l'état est
    illisible — jamais un faux « terminé » — et `exit_code()` renvoie None tant
    que rien d'exploitable n'est disponible.
    """

    def check(self) -> UpdateStatus: ...
    def apply(self) -> None: ...
    def is_running(self) -> bool: ...
    def exit_code(self) -> int | None: ...


class MetricsPort(Protocol):
    """Instantané des métriques configurées (lecture seule).

    Les requêtes (PromQL…) vivent dans la CONFIG de l'adapter, jamais dans le
    domaine ni dans l'UI — pas de requête arbitraire. Lève ServerUnavailable si
    la source de métriques est injoignable dans son ensemble.

    `performance()` (V6.7) : vue santé du serveur (TPS/MSPT, top des entités
    chargées, chunks par monde) — requêtes FIGÉES dans l'adapter, mêmes
    politiques d'erreur que `snapshot()`.
    """

    def snapshot(self) -> list[MetricReading]: ...

    def performance(self, history_hours: int = 24) -> PerformanceSnapshot: ...


class OpsPort(Protocol):
    """Lecture seule des opérateurs depuis le fichier `ops.json`.

    Pas de commande RCON « op list » côté vanilla/Fabric : la seule source est
    le fichier `ops.json` du serveur. L'ajout/retrait (le fait d'être op ou
    non) passe par RCON (`GamePort.op/deop`), qui reste l'unique écrivain
    pendant que Minecraft tourne.
    """

    def list_ops(self) -> list[OpEntry]: ...


class PendingOpLevelsPort(Protocol):
    """Niveaux OP individuels persistés jusqu'au prochain redémarrage."""

    def list_pending(self) -> list[PendingOpLevel]: ...
    def set_level(self, player_name: str, level: int) -> None: ...
    def remove(self, player_name: str) -> None: ...


class OpLevelsApplyPort(Protocol):
    """Déclenche le worker qui applique les niveaux puis redémarre Minecraft."""

    def apply(self) -> None: ...
    def is_running(self) -> bool: ...
    def exit_code(self) -> int | None: ...


class ModsPort(Protocol):
    """Liste des mods installés (V6) : dossier mods/ monté en LECTURE SEULE,
    métadonnées extraites du fabric.mod.json des .jar (best-effort — un jar
    illisible s'affiche avec son seul nom de fichier, jamais d'erreur)."""

    def list_mods(self) -> list[ModInfo]: ...


class ModChecksPort(Protocol):
    """Verdicts Modrinth persistés (mods enrichis) : écrits par le checker de
    fond, lus par la page Mods. Clé = empreinte SHA-1 du .jar."""

    def statuses(self) -> dict[str, ModUpdate]: ...

    def checked_at(self) -> datetime | None: ...


class NotificationPort(Protocol):
    """Canal de notification best-effort (Discord, Telegram…).

    Contrat : `notify` NE LÈVE JAMAIS — une notification est un aparté, jamais
    un chemin critique (un webhook down ne doit jamais faire échouer un
    restart). C'est à l'implémentation (ex. `CompositeNotifier`) de garantir
    ce contrat, pas à ses appelants dans le domaine.
    """

    def notify(self, title: str, message: str, level: str = "info") -> None: ...


class PlayerHistoryPort(Protocol):
    """Historique des sessions de jeu (V3.4).

    `record_join`/`record_leave` sont appelés par le tailer de logs en tâche
    de fond (`app/player_watcher.py`) — PAS via AdminService : ce n'est pas une
    action utilisateur soumise à RBAC, c'est de l'observation passive (comme
    un collector). Seule la LECTURE (`summary`) passe par le service, gatée
    par la même permission que le status.
    """

    def record_join(self, player: str, at: datetime) -> None: ...
    def record_leave(self, player: str, at: datetime) -> None: ...
    def record_command(self, player: str, command: str, at: datetime) -> None: ...
    def summary(self) -> list[PlayerSummary]: ...
    def recent_sessions(self, limit: int = 100, since: datetime | None = None) -> list[PlayerSession]: ...
    def recent_commands(self, limit: int = 100, since: datetime | None = None) -> list[tuple[str, datetime, str]]: ...


class PlayerStatsPort(Protocol):
    """Statistiques vanilla des joueurs (V5.x), source : fichiers
    `stats/<uuid>.json` du monde + `usercache.json` (uuid -> pseudo), montés
    en LECTURE SEULE — même politique que ops.json/banned-players.json.
    Fichiers absents ou illisibles = liste vide/joueur ignoré, jamais d'erreur
    (la page Joueurs reste fonctionnelle sans les stats)."""

    def all_stats(self) -> list[PlayerStats]: ...


class BansPort(Protocol):
    """Lecture de la liste des bannissements.

    Comme pour les opérateurs : pas de commande RCON fiable pour lister
    (`/banlist` existe mais son parsing est fragile selon le nombre de
    joueurs) — la source de vérité est `banned-players.json`, monté en
    LECTURE SEULE. Les mutations (ban/pardon) passent par RCON (`GamePort`).
    """

    def list_bans(self) -> list[BanEntry]: ...


class TempBanPort(Protocol):
    """Suivi persistant des bans temporisés (V4.4b) — vanilla n'a pas de durée
    native sur `/ban` : mc-admin planifie lui-même la levée automatique.

    `schedule`/`cancel` sont appelés par le service (action utilisateur) ;
    `due()` et l'appel à `GamePort.pardon` correspondant sont utilisés par le
    planificateur de fond (`app/tempban_scheduler.py`), pas par le service.
    """

    def schedule(self, player: str, unban_at: datetime) -> None: ...
    def cancel(self, player: str) -> None: ...
    def pending(self) -> list[PendingUnban]: ...
    def due(self, now: datetime) -> list[PendingUnban]: ...


class RestartSchedulerPort(Protocol):
    """État (en mémoire, volontairement non persistant — cf. adapter) d'un
    redémarrage programmé unique (V4.5). Appelé UNIQUEMENT depuis
    `AdminService` (jamais directement par le planificateur de fond, qui ne
    connaît que le service — même discipline que TempBanScheduler/
    BackupScheduler) :
      - `schedule`/`cancel`/`status` par les actions utilisateur ;
      - `take_due_warning` par `tick_scheduled_restart`, à chaque poll, pour
        savoir si un nouveau seuil d'avertissement vient d'être franchi (un
        seuil n'est jamais renvoyé deux fois pour une même programmation).
    """

    def schedule(
        self,
        restart_at: datetime,
        username: str,
        total_seconds: float,
        defer_if_players: bool = False,
    ) -> None: ...
    def cancel(self) -> bool: ...
    def status(self) -> ScheduledRestart | None: ...
    def take_due_warning(self, now: datetime) -> int | None: ...


class RecurringRestartPort(Protocol):
    """Configuration persistante du redémarrage quotidien récurrent (V5).

    `get`/`set_config`/`clear` par les actions utilisateur ; `last_fired`/
    `mark_fired` par `tick_scheduled_restart` pour ne déclencher qu'une fois
    par jour (persisté : survit au redémarrage de mc-admin).
    """

    def get(self) -> RecurringRestart | None: ...
    def set_config(self, config: RecurringRestart) -> None: ...
    def clear(self) -> None: ...
    def last_fired(self) -> str | None: ...      # "YYYY-MM-DD" ou None
    def mark_fired(self, day: str) -> None: ...


class ScheduledMaintenancePort(Protocol):
    """Liste persistante des maintenances programmées (date précise OU jours de
    semaine). `list`/`add`/`remove` par les actions utilisateur ;
    `last_fired`/`mark_fired` par `tick_maintenance` pour n'armer qu'une fois
    par jour ET par entrée (persisté, survit au redémarrage de mc-admin).
    `add` attribue et renvoie l'identifiant de l'entrée.
    """

    def list(self) -> list[ScheduledMaintenance]: ...
    def add(self, entry: ScheduledMaintenance) -> str: ...
    def remove(self, entry_id: str) -> bool: ...
    def last_fired(self, entry_id: str) -> str | None: ...
    def mark_fired(self, entry_id: str, day: str) -> None: ...


class AuditPort(Protocol):
    """Journal d'audit append-only (qui/quoi/quand/résultat)."""

    def record(self, entry: AuditEntry) -> None: ...
    def recent(self, limit: int = 50) -> list[AuditEntry]: ...


class IncidentLogPort(Protocol):
    """Historique des incidents : les watchers ÉCRIVENT (open/close aux
    transitions détectées, observation passive comme PlayerHistory), le
    service LIT (recent, barrière STATUS). `open` est idempotent par sujet
    (une chute déjà ouverte n'en rouvre pas une seconde)."""

    def open(self, subject: str, kind: str, label: str, detail: str = "") -> None: ...
    def close(self, subject: str, detail: str = "") -> None: ...
    def recent(self, limit: int = 100) -> list[Incident]: ...


class Clock(Protocol):
    """Horloge injectable — rend l'audit testable de façon déterministe."""

    def now(self) -> datetime: ...
