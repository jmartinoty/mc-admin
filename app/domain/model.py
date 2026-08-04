"""Modèle du domaine : permissions, rôles, utilisateurs et objets métier.

Aucun I/O ici. Les rôles ne sont PAS énumérés dans le code : seule la liste des
`Permission` l'est (c'est le contrat de sécurité). Les rôles sont des
regroupements de permissions construits depuis la config YAML (voir rbac.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Permission(str, Enum):
    """Actions atomiques soumises au contrôle d'accès (RBAC).

    L'énumération EST le contrat de sécurité : chaque action sensible du service
    correspond à exactement une permission. Ajouter une capacité = ajouter une
    valeur ici ET l'autoriser dans un rôle du YAML.
    """

    STATUS = "STATUS"                    # voir l'état du serveur + joueurs
    LOGS_VIEW = "LOGS_VIEW"              # lire les logs récents
    RESTART = "RESTART"                  # redémarrer le conteneur
    START = "START"                      # démarrer le conteneur arrêté
    BACKUP_TRIGGER = "BACKUP_TRIGGER"    # déclencher une sauvegarde
    BACKUP_DOWNLOAD = "BACKUP_DOWNLOAD"  # télécharger une archive de sauvegarde
    BACKUP_RESTORE = "BACKUP_RESTORE"    # restaurer une archive (écrase le monde — owner)
    BACKUP_RETENTION = "BACKUP_RETENTION"  # voir/appliquer la politique de rétention
    RCON_RAW = "RCON_RAW"                # commande RCON arbitraire (owner)
    OP_MANAGE = "OP_MANAGE"             # gérer les op (owner)
    STOP = "STOP"                        # arrêt définitif (owner)
    UPDATE = "UPDATE"                    # update/recreate (owner, V2)
    AUDIT_VIEW = "AUDIT_VIEW"            # consulter le journal d'audit (owner)
    WHITELIST_MANAGE = "WHITELIST_MANAGE"  # gérer la whitelist (owner, V2)
    KICK = "KICK"                        # exclure un joueur connecté (owner+admin, V4.4)
    BAN_MANAGE = "BAN_MANAGE"            # bannir/lever un ban, permanent ou temporisé (owner, V4.4)
    GAME_SETTINGS = "GAME_SETTINGS"      # difficulté, gamerules, météo/heure (V5)
    BACKUP_MANAGE = "BACKUP_MANAGE"      # créer/modifier les profils de sauvegarde (V6, owner)
    SERVER_MANAGE = "SERVER_MANAGE"      # gérer les serveurs administrés + branchements (V6.3, owner)
    USER_MANAGE = "USER_MANAGE"          # créer/gérer les comptes mc-admin (V6.4, owner)
    MAINTENANCE = "MAINTENANCE"          # fermer/rouvrir le serveur en maintenance (owner)


# Dans le YAML, ce jeton accorde toutes les permissions à un rôle.
WILDCARD = "*"


@dataclass(frozen=True)
class Role:
    """Regroupement de permissions défini en configuration (pas en dur)."""

    name: str
    permissions: frozenset[Permission]
    grants_all: bool = False  # True si le rôle a reçu le wildcard "*"

    def allows(self, permission: Permission) -> bool:
        return self.grants_all or permission in self.permissions


@dataclass(frozen=True)
class User:
    """Identité + rôle. Le domaine ne connaît pas le mot de passe (auth = adapter)."""

    username: str
    role: Role

    def can(self, permission: Permission) -> bool:
        return self.role.allows(permission)


# --- Objets métier renvoyés par les ports / le service ---

@dataclass(frozen=True)
class Player:
    name: str


@dataclass(frozen=True)
class ContainerState:
    """État du conteneur Minecraft tel que rapporté par le ContainerPort."""

    name: str
    running: bool
    status: str                 # ex. "running", "exited", "restarting"
    health: str | None = None   # ex. "healthy" si un healthcheck existe
    started_at: datetime | None = None  # dernier démarrage (State.StartedAt)


@dataclass(frozen=True)
class ServerStatus:
    """Vue agrégée pour la page status.

    `players_available` distingue « 0 joueur » d'« information indisponible »
    (RCON injoignable alors que le conteneur tourne).
    """

    container: ContainerState
    players_online: tuple[Player, ...] = ()
    players_available: bool = False


@dataclass(frozen=True)
class BackupResult:
    started: bool
    detail: str = ""


@dataclass(frozen=True)
class BackupArchive:
    """Une archive de sauvegarde existante (page /backups, V4.2)."""

    filename: str
    size_bytes: int
    created_at: datetime


@dataclass(frozen=True)
class StorageSnapshot:
    """Un point d'historique de stockage (page /backups) : occupation par
    famille (manual/scheduled/profiles/restore-safety/legacy — premier
    segment du chemin de l'archive, PAS un dossier par profil individuel :
    stable même si des profils sont ajoutés/supprimés) + espace libre du
    volume, UN point par jour calendaire (le dernier relevé du jour gagne)."""

    date: str  # "AAAA-MM-JJ", jour calendaire local
    free_bytes: int | None
    families: dict[str, int]


@dataclass(frozen=True)
class BackupProgress:
    """Suivi best-effort d'une sauvegarde en cours."""

    kind: str                 # id de profil (V6) | safety | manual/scheduled (hérité)
    running: bool
    progress_percent: int | None
    archive: BackupArchive | None = None
    label: str = ""           # nom du profil, pour l'affichage


@dataclass(frozen=True)
class RestoreProgress:
    """Suivi best-effort d'une restauration en attente.

    `progress_percent` est une estimation pendant la sauvegarde de sécurité :
    mc-backup ne publie pas de progression native, donc on compare la taille de
    l'archive en cours à la dernière archive manuelle connue quand c'est
    possible.
    """

    pending: PendingRestore | None
    phase: str                # idle | backup | restore
    backup_running: bool
    progress_percent: int | None
    security_archive: BackupArchive | None = None
    operation_id: str | None = None
    filename: str | None = None
    message: str = ""
    level: str = "info"       # info | success | error


@dataclass(frozen=True)
class RestoreOperation:
    """Restauration post-lancement suivie jusqu'au retour healthy de Minecraft."""

    filename: str
    requested_by: str
    phase: str                # restore_running | waiting_minecraft | success | failed
    started_at: datetime
    deadline: datetime
    message: str = ""
    completed_at: datetime | None = None
    operation_id: str = ""


@dataclass(frozen=True)
class BackupRetentionItem:
    archive: BackupArchive
    reason: str


@dataclass(frozen=True)
class BackupRetentionPlan:
    """Dry-run de rétention : aucun fichier n'est supprimé à ce stade."""

    keep: tuple[BackupRetentionItem, ...]
    delete: tuple[BackupRetentionItem, ...]


@dataclass(frozen=True)
class OpEntry:
    """Un opérateur, avec son niveau actuellement chargé depuis `ops.json`."""

    name: str
    level: int


@dataclass(frozen=True)
class PendingOpLevel:
    """Niveau individuel à appliquer au prochain démarrage de Minecraft.

    ``applying`` signifie que le conteneur transactionnel a déjà réclamé la
    consigne et pilote actuellement le redémarrage.
    """

    name: str
    level: int
    applying: bool = False


@dataclass(frozen=True)
class BanEntry:
    """Un bannissement actif, lu depuis `banned-players.json` (V4.4).

    `expires` reflète le champ brut du fichier ("forever" pour tout ban posé
    par la commande vanilla `/ban` — elle ne supporte pas de durée). Un ban
    temporisé posé depuis mc-admin apparaît donc aussi en "forever" ici tant
    qu'il n'a pas expiré ; sa date d'expiration réelle est suivie séparément
    par le planificateur (cf. `PendingUnban`), pas dans ce fichier.
    """

    player: str
    reason: str
    created_at: datetime | None
    expires: str


@dataclass(frozen=True)
class PendingUnban:
    """Un ban temporisé posé depuis mc-admin, en attente de levée automatique."""

    player: str
    unban_at: datetime


@dataclass(frozen=True)
class ScheduledRestart:
    """Un redémarrage programmé en attente, avec avertissements dégressifs
    in-game avant l'arrêt effectif (cf. RestartWarningScheduler)."""

    restart_at: datetime
    scheduled_by: str
    # Garde-fou : si vrai, ne redémarre PAS tant que des joueurs sont
    # connectés — l'échéance est reportée à chaque tick jusqu'au serveur vide
    # (cf. tick_scheduled_restart). En mémoire comme le reste de l'op.
    defer_if_players: bool = False


@dataclass(frozen=True)
class PendingRestore:
    """Une restauration en attente : elle ne démarre qu'une fois la sauvegarde
    de sécurité du monde courant TERMINÉE (cf. RestoreCoordinator). Passé
    `deadline`, elle est abandonnée — le monde n'est alors pas touché."""

    filename: str
    requested_by: str
    deadline: datetime
    operation_id: str = ""
    safety_archives_before: tuple[str, ...] = ()


@dataclass(frozen=True)
class PendingMaintenance:
    """Une fermeture pour maintenance annoncée : le serveur ferme à
    `engage_at` (fin du délai de grâce), le portier prend alors le relais.
    Même compromis en mémoire que ScheduledRestart : perdre la programmation
    si mc-admin redémarre est acceptable pour une action de courte portée."""

    engage_at: datetime
    requested_by: str
    motd: str
    kick: str
    operation_id: str = ""
    # Garde-fou : si vrai, la fermeture est REPORTÉE tant que des joueurs sont
    # connectés (le tick attend le serveur vide au lieu de les déconnecter).
    defer_if_players: bool = False


@dataclass(frozen=True)
class MaintenanceStatus:
    """Vue du mode maintenance : portier en place et/ou fermeture annoncée.
    `motd` n'est connu que du vivant du process qui a engagé la maintenance
    (contexte d'affichage, jamais une source de décision)."""

    active: bool
    pending: PendingMaintenance | None = None
    motd: str = ""


@dataclass(frozen=True)
class AlertThresholds:
    """Seuils d'alerte de PerfWatcher, réglables dans l'UI (backlog
    fiabilité n° 4) — mêmes défauts que le comportement historique câblé en
    dur. `get()` du port retourne TOUJOURS un objet concret (ces défauts si
    jamais réglé) : contrairement à un redémarrage récurrent, il n'y a pas
    d'état « désactivé »."""

    mspt_threshold_ms: float = 50.0
    disk_min_free_gib: float = 10.0
    mspt_sustained_minutes: float = 3.0


@dataclass(frozen=True)
class BackupProfile:
    """Une configuration de sauvegarde (V6) : quoi, où, quand, combien de
    temps. Plusieurs profils peuvent coexister (et à terme, plusieurs
    serveurs). `dest` est un sous-dossier RELATIF du volume de sauvegardes —
    jamais un chemin libre. `include` est une liste de CATÉGORIES fermées
    (world|mods|mods_jars|config|admin|all), traduites en motifs tar par
    l'adapter — jamais de motif libre venu de l'UI."""

    id: str
    name: str
    include: tuple[str, ...]
    dest: str
    hours: float = 0.0
    daily_at: str | None = None
    retention_daily_days: int = 0    # 0 = conserver sans limite
    retention_monthly_months: int = 0
    enabled: bool = True
    last_run: datetime | None = None


@dataclass(frozen=True)
class BackupOutcome:
    """Résultat d'une sauvegarde terminée (V5.x) : affiché en bandeau
    persistant jusqu'à fermeture, comme le résultat d'une restauration.
    ok=False = le conteneur a fini sans produire d'archive."""

    kind: str                  # id de profil (V6) | manual/scheduled (hérité)
    ok: bool
    finished_at: datetime
    archive: str | None = None
    label: str = ""            # nom du profil, pour l'affichage


@dataclass(frozen=True)
class ServerEntry:
    """Un serveur Minecraft administré (V6.3). Fondation du multi-serveur :
    pour l'instant, seule la PREMIÈRE entrée est pilotée (les adapters sont
    construits au démarrage) — les suivantes sont enregistrées pour la V7.
    Le mot de passe RCON reste dans l'environnement, jamais dans ce fichier."""

    id: str
    name: str
    mode: str = "docker"          # docker | host (V7)
    container: str = ""
    rcon_host: str = ""
    rcon_port: int = 25575
    log_file: str = ""
    world_dir: str = "world"
    # Adresse PUBLIQUE à partager aux joueurs (tunnel playit, IP LAN…) —
    # saisie à la main : l'app ne peut pas la deviner (UI-1).
    address: str = ""
    # URL de la carte web du monde (BlueMap, Dynmap…) — servie par le serveur
    # de jeu lui-même, mc-admin ne fait que pointer dessus (page Carte).
    map_url: str = ""


@dataclass(frozen=True)
class DetectedServer:
    """Un serveur Minecraft repéré par la détection (conteneur Docker dont
    l'image ou les ports évoquent Minecraft), pas encore configuré."""

    name: str
    image: str
    state: str
    configured: bool = False


@dataclass(frozen=True)
class ConnectionCheck:
    """Le verdict d'un branchement (page Serveurs) : testé EN DIRECT."""

    key: str
    label: str
    ok: bool
    detail: str = ""     # ce qui marche, ou quoi corriger (langage utilisateur)


@dataclass(frozen=True)
class WorkerCheck:
    """Alignement d'un conteneur one-shot pré-créé avec l'app qui tourne.

    Un one-shot créé à l'avance fige son image ET ses réseaux : après un
    rebuild ou une recréation de réseau, il exécuterait du code périmé ou
    échouerait au démarrage (constaté 3× le 18/07/2026). `ok=None` =
    invérifiable (proxy muet, ou conteneur mc-admin inconnu de lui-même)."""

    name: str
    label: str
    ok: bool | None
    detail: str = ""     # explication + geste de correction (langage utilisateur)


@dataclass(frozen=True)
class ModInfo:
    """Un mod installé sur le serveur (V6, lecture seule).

    `name`/`version`/`description` viennent du fabric.mod.json embarqué dans
    le .jar quand il est lisible ; sinon on retombe sur le nom de fichier."""

    filename: str
    size_bytes: int
    modified_at: datetime
    name: str = ""
    version: str = ""
    description: str = ""
    enabled: bool = True   # False = fichier .jar.disabled
    sha1: str = ""         # empreinte du .jar (identification Modrinth exacte)


@dataclass(frozen=True)
class ModUpdate:
    """Verdict Modrinth pour UN mod installé (identifié par l'empreinte SHA-1
    de son .jar — jamais par son nom). `known=False` = jar inconnu de
    Modrinth (mod perso, CurseForge-only…) : affiché honnêtement, pas une
    erreur. Lecture seule : mc-admin ne met JAMAIS un mod à jour lui-même
    (changer un jar = redémarrage + risque de compatibilité, geste humain)."""

    sha1: str
    known: bool = False
    project_url: str = ""
    installed_version: str = ""   # numéro de version Modrinth du jar installé
    latest_version: str = ""      # dernière version compatible (loader + MC)
    update_available: bool = False
    checked_at: datetime | None = None


@dataclass(frozen=True)
class ArchiveCheck:
    """Résultat de la vérification d'intégrité d'une archive (V5.x).

    Produite en tâche de fond (ArchiveVerifier) : l'archive est relue en
    entier (tar+gzip). `ok` signifie que le conteneur est lisible et sûr à
    extraire ; `restorable` confirme en plus la présence de server.properties
    et du level.dat du monde déclaré. None désigne un ancien verdict qui doit
    être recalculé. Taille et date de modification empêchent de réutiliser le
    verdict d'un fichier remplacé depuis sa vérification."""

    filename: str
    ok: bool
    checked_at: datetime
    size_bytes: int
    detail: str = ""
    restorable: bool | None = None
    world_name: str = ""
    archive_created_at: datetime | None = None


@dataclass(frozen=True)
class GameSettings:
    """Réglages de jeu lisibles/modifiables à chaud via RCON (V5).
    None = valeur illisible (réponse RCON inattendue)."""

    difficulty: str | None            # peaceful | easy | normal | hard
    keep_inventory: bool | None       # gamerule keepInventory
    mob_griefing: bool | None         # gamerule mobGriefing
    daylight_cycle: bool | None       # gamerule doDaylightCycle


@dataclass(frozen=True)
class BackupSchedule:
    """Planification des sauvegardes automatiques (V5), persistée avec le
    dernier déclenchement pour survivre aux redéploiements de mc-admin.

    Deux modes exclusifs : toutes les `hours` heures (0 = désactivé), OU tous
    les jours à `daily_at` (HH:MM, heure LOCALE du conteneur). En mode
    quotidien, une échéance manquée (mc-admin éteint à l'heure dite) est
    rattrapée le jour même — une sauvegarde en retard vaut mieux que rien."""

    hours: float
    daily_at: str | None = None
    last_run: datetime | None = None

    @property
    def enabled(self) -> bool:
        return self.hours > 0 or bool(self.daily_at)


@dataclass(frozen=True)
class RecurringRestart:
    """Redémarrage quotidien récurrent : chaque jour à HH:MM (heure LOCALE du
    conteneur), planifié `lead_seconds` avant l'échéance pour que les
    avertissements in-game habituels s'appliquent."""

    time_hhmm: str          # "06:00"
    lead_seconds: int = 300
    # Garde-fou reporté au one-shot armé chaque jour : si vrai, le redémarrage
    # quotidien attend que le serveur soit vide plutôt que de couper des
    # joueurs connectés (persisté avec la config).
    defer_if_players: bool = False


@dataclass(frozen=True)
class ScheduledMaintenance:
    """Une maintenance programmée dans la liste (page d'accueil). Deux formes :

    - `kind="once"`   : une date calendaire précise (`date` = "YYYY-MM-DD") + heure ;
    - `kind="weekly"` : un ou plusieurs jours de semaine (`weekdays`, 0=lundi …
      6=dimanche) + heure, récurrent chaque semaine.

    Champs partagés : `lead_seconds` (préavis in-game), `message`/`until` du
    portier, `defer_if_players` (reporter tant que des joueurs sont connectés).
    L'échéance réelle est armée `lead_seconds` avant l'heure par le tick de fond
    (mêmes avertissements dégressifs qu'une fermeture annoncée manuelle)."""

    id: str
    kind: str                        # "once" | "weekly"
    time_hhmm: str                   # "04:00"
    date: str = ""                   # "YYYY-MM-DD" (kind=once)
    weekdays: tuple[int, ...] = ()   # 0=lundi … 6=dimanche (kind=weekly)
    lead_seconds: int = 300
    message: str = ""
    until: str = ""
    defer_if_players: bool = False


@dataclass(frozen=True)
class UpdateStatus:
    """État de mise à jour du serveur (garde-fous V2.3 : versions + changelog).

    `current_version` = version Minecraft du serveur qui TOURNE ;
    `latest_version` = dernière release officielle. None = information
    indisponible (affichée « ? », ne bloque pas la page).
    """

    current_version: str | None
    latest_version: str | None
    update_available: bool
    changelog_url: str | None


@dataclass(frozen=True)
class AppRelease:
    """Une release publiée de mc-admin (bouton MAJ) : tag GitHub + notes."""

    version: str          # « 0.9.1 » (sans le v)
    notes: str = ""       # corps de la release, borné par l'adapter
    url: str = ""         # page de la release


@dataclass(frozen=True)
class AppUpdateStatus:
    """État de mise à jour de mc-admin LUI-MÊME (carte accueil, owner).

    `can_apply` = le bouton « Appliquer » a un sens sur cette installation ;
    sinon `reason` dit pourquoi en langage utilisateur (ex. installation
    gérée par git : la mise à jour passe par le dépôt, pas par l'image)."""

    current_version: str
    latest: AppRelease | None
    update_available: bool
    checked_at: datetime | None
    can_apply: bool
    reason: str = ""
    snoozed: bool = False  # « me le rappeler plus tard » actif pour CETTE version


@dataclass(frozen=True)
class AppUpdateSnooze:
    """Report du bandeau MAJ (« me le rappeler plus tard ») — propre à une
    version précise : si une version PLUS RÉCENTE sort entre-temps, le
    report ne s'applique plus (jamais silencer une vraie nouveauté)."""

    version: str
    until: datetime


@dataclass(frozen=True)
class EntityTop:
    """Une ligne du top des entités chargées (V6.7 — page Performances)."""

    world: str
    type: str
    count: int


@dataclass(frozen=True)
class WorldChunks:
    """Chunks chargés d'un monde : valeur instantanée + série 24 h."""

    world: str
    count: int | None
    history: tuple[float, ...] | None = None


@dataclass(frozen=True)
class PerformanceSnapshot:
    """Vue agrégée de la page Performances (V6.7).

    Tout est best-effort : None/vide = indisponible pour CETTE donnée, la
    page affiche le reste. Un top vide avec un serveur qui tourne signifie
    « aucune entité chargée » (personne en ligne), pas une erreur."""

    tps: float | None = None
    mspt_mean: float | None = None
    mspt_max: float | None = None
    mspt_history: tuple[float, ...] | None = None
    entities_total: int | None = None
    entity_top: tuple[EntityTop, ...] = ()
    chunks: tuple[WorldChunks, ...] = ()


@dataclass(frozen=True)
class SparkProfile:
    """Un profil spark sauvé sur disque (perf palier 3) — à glisser sur le
    viewer spark.lucko.me pour l'analyse par chunk/mod/tick."""

    filename: str
    size_bytes: int
    created_at: datetime


@dataclass(frozen=True)
class MetricReading:
    """Valeur instantanée d'une métrique déclarée en configuration.

    Le domaine ignore d'où vient la valeur (PromQL, etc.) : il ne voit que le
    résultat. `value=None` = cette métrique précise n'a pas pu être évaluée
    (le reste du panneau reste affichable). `history` (V5.x) : série des
    dernières 24 h pour les métriques marquées `spark` en config — None si non
    demandée ou irrécupérable (la valeur instantanée reste affichée)."""

    key: str
    label: str
    unit: str
    value: float | None
    history: tuple[float, ...] | None = None


@dataclass(frozen=True)
class PlayerSummary:
    """Historique agrégé d'un joueur (V3.4) : temps de jeu cumulé + dernier vu.

    `online=True` -> `last_seen` vaut « maintenant » (session en cours, pas
    encore de `left_at`) ; `online=False` -> `last_seen` est l'horodatage de
    la dernière déconnexion connue.
    """

    player: str
    total_seconds: float
    last_seen: datetime
    online: bool


@dataclass(frozen=True)
class Incident:
    """Un épisode d'indisponibilité ou de dégradation, borné dans le temps
    (V6.x historique des incidents). Agrège les transitions déjà détectées par
    les watchers (chute/rétablissement) rendues PERSISTANTES.

    `ended_at=None` -> incident toujours en cours. `kind` distingue la nature
    (`availability` conteneur down, `performance` lag MSPT, `disk` espace bas)
    pour un affichage/filtre cohérent."""

    id: str
    subject: str            # "server" | nom de conteneur | "mspt" | "disk"
    kind: str               # "availability" | "performance" | "disk"
    label: str              # libellé humain (« Serveur », « playit », « Lag serveur »)
    started_at: datetime
    ended_at: datetime | None = None
    detail: str = ""


@dataclass(frozen=True)
class PlayerStats:
    """Statistiques vanilla d'un joueur (V5.x), lues depuis les fichiers
    `stats/*.json` du monde. Comptées par le SERVEUR depuis la création du
    monde — elles couvrent donc aussi la période antérieure à mc-admin,
    contrairement à PlayerSummary (observation du tailer de logs)."""

    player: str
    play_time_seconds: float
    deaths: int
    mob_kills: int
    player_kills: int
    distance_cm: int      # toutes locomotions confondues (marche, sprint, nage, vol…)
    jumps: int
    blocks_mined: int
    items_crafted: int
    uuid: str = ""                        # nom du fichier de stats (avatars, alias)
    last_played: datetime | None = None   # mtime du fichier : dernière partie ESTIMÉE


@dataclass(frozen=True)
class PlayerSession:
    """Une session de jeu individuelle (V5, page Événements)."""

    player: str
    joined_at: datetime
    left_at: datetime | None  # None = session en cours


@dataclass(frozen=True)
class TimelineEvent:
    """Un événement de la timeline unifiée (V5) : action d'audit ou
    join/leave d'un joueur, fusionnés et triés par le service."""

    kind: str          # "audit" | "join" | "leave"
    timestamp: datetime
    title: str
    detail: str = ""
    outcome: str = ""  # rempli pour kind="audit" ("allowed"/"denied"/"error")
    actor: str = ""    # username mc-admin (kind="audit") — résolu en nom affiché par l'UI


@dataclass(frozen=True)
class AuditEntry:
    """Ligne du journal d'audit : qui / quoi / quand / résultat (RBAC #4)."""

    timestamp: datetime
    username: str
    role: str
    action: str                 # Permission.value
    target: str                 # conteneur visé (MINECRAFT_CONTAINER)
    outcome: str                # "allowed" | "denied" | "error"
    detail: str = ""
