"""Configuration & câblage (composition root).

Lit l'environnement, charge la config RBAC (YAML) au DÉMARRAGE, et assemble
l'AdminService avec ses adapters concrets. C'est le seul endroit qui connaît à
la fois le domaine ET les adapters — le reste du code reste découplé.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

from domain.rbac import build_roles, build_users

# Version affichée dans l'UI et attendue par le tag git de release (vX.Y.Z).
APP_VERSION = "0.10.1"
# Dépôt public — source des releases pour le bouton MAJ.
APP_REPO = "jmartinoty/mc-admin"


@dataclass(frozen=True)
class Settings:
    rcon_host: str
    rcon_port: int
    rcon_password: str
    session_secret: str
    minecraft_container: str
    mc_log_file: str
    rbac_config: str
    audit_log: str
    cookie_secure: bool
    prometheus_url: str
    metrics_config: str
    mc_updater_container: str
    ops_file: str
    player_history_db: str = "/data/players.db"
    backup_archives_dir: str = "/backups"
    bans_file: str = "/banned-players.json"
    users_file: str = "/data/users.json"   # comptes créés depuis l'app (V6.2)
    archive_checks_file: str = "/data/archive_checks.json"  # verdicts d'intégrité des archives
    backup_profiles_file: str = "/data/backup_profiles.json"
    profile_backup_container: str = ""   # vide = profils V6 non disponibles
    profile_backup_env_file: str = "/data/backup-profile.env"
    world_dir: str = "world"             # dossier du monde (catégorie « Monde »)
    mods_dir: str = ""                   # dossier mods/ (RO) ; vide = page absente
    servers_file: str = "/data/servers.json"   # serveurs administrés (V6.3)
    stats_dir: str = ""            # stats vanilla (RO) ; vide = fonctionnalité absente
    usercache_file: str = ""       # uuid -> pseudo (RO)
    player_aliases_file: str = "/data/legacy-player-names.json"  # alias d'époque (optionnel)
    temp_bans_file: str = "/data/pending_unbans.json"
    temp_ban_poll_seconds: float = 60.0
    restart_poll_seconds: float = 2.0
    display_names_file: str = "/data/display_names.json"
    password_store_file: str = "/data/passwords.json"
    sessions_file: str = "/data/sessions.json"   # appareils connectés (révocables)
    totp_file: str = "/data/totp.json"           # secrets 2FA (0600)
    mc_restore_container: str = ""
    restore_target_file: str = "/data/restore-target"
    mc_doorman_container: str = ""
    doorman_config_file: str = "/data/doorman.json"
    recurring_restart_file: str = "/data/recurring_restart.json"
    backup_schedule_file: str = "/data/backup_schedule.json"
    # Vide = canal désactivé (comme MC_BACKUP_CONTAINER absent).
    discord_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # None = filtre par défaut de FileLog ; chaîne vide = filtre désactivé.
    log_exclude: str | None = None
    mc_restore_safety_backup_container: str = ""
    # Notifications d'événements de fond (défaut : DÉSACTIVÉ — en dev, les
    # redémarrages fréquents spammeraient Discord/Telegram).
    notify_player_events: bool = False
    notify_restore_events: bool = False
    health_alerts: bool = False
    health_poll_seconds: float = 30.0
    # Conteneurs COMPAGNONS surveillés en plus du serveur (ex. "playit") —
    # AMORÇAGE uniquement : importé une fois dans watched_containers.json,
    # la liste se gère ensuite dans l'UI (page Surveillance).
    health_extra_containers: str = ""
    watched_containers_file: str = "/data/watched_containers.json"
    notification_config_file: str = "/data/notifications.json"
    mod_checks_file: str = "/data/mod_checks.json"
    app_update_file: str = "/data/app_update.json"
    app_update_snooze_file: str = "/data/app_update_snooze.json"  # « me le rappeler plus tard »
    app_updater_container: str = "mc-admin-updater"
    spark_dir: str = ""  # profils spark (RO) ; vide = carte absente
    pending_op_levels_file: str = "/data/pending_op_levels.json"
    mc_op_levels_container: str = ""
    # Vide = les en-têtes X-Forwarded-For sont ignorés. N'ajouter ici que les
    # CIDR des reverse proxies directement connectés à mc-admin.
    login_trusted_proxy_cidrs: str = ""
    storage_history_file: str = "/data/storage_history.json"  # occupation quotidienne des sauvegardes
    storage_history_poll_seconds: float = 3600.0
    alert_thresholds_file: str = "/data/alert_thresholds.json"  # seuils PerfWatcher réglables (UI)

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        env = env if env is not None else os.environ

        def flag(name: str, default: str) -> bool:
            return str(env.get(name, default)).strip().lower() in ("1", "true", "yes", "on")

        return cls(
            rcon_host=env.get("RCON_HOST", "minecraft"),
            rcon_port=int(env.get("RCON_PORT", "25575")),
            rcon_password=env.get("RCON_PASSWORD", ""),
            session_secret=env.get("SESSION_SECRET", ""),
            # Pas de valeur par défaut : une install fraîche SANS cette
            # variable doit démarrer sans serveur (état vide + assistant),
            # pas avec un fantôme « minecraft » sorti de nulle part (constaté
            # sur la démo par Jeremy).
            minecraft_container=env.get("MINECRAFT_CONTAINER", ""),
            mc_log_file=env.get("MC_LOG_FILE", "/logs/latest.log"),
            rbac_config=env.get("RBAC_CONFIG", "/config/roles.yml"),
            audit_log=env.get("AUDIT_LOG", "/data/audit.jsonl"),
            cookie_secure=flag("SESSION_COOKIE_SECURE", "true"),
            prometheus_url=env.get("PROMETHEUS_URL", "http://prometheus:9090"),
            metrics_config=env.get("METRICS_CONFIG", "/config/metrics.yml"),
            mc_updater_container=env.get("MC_UPDATER_CONTAINER", "mc-updater"),
            ops_file=env.get("OPS_FILE", "/ops.json"),
            player_history_db=env.get("PLAYER_HISTORY_DB", "/data/players.db"),
            backup_archives_dir=env.get("BACKUP_ARCHIVES_DIR", "/backups"),
            bans_file=env.get("BANS_FILE", "/banned-players.json"),
            users_file=env.get("USERS_FILE", "/data/users.json"),
            archive_checks_file=env.get("ARCHIVE_CHECKS_FILE", "/data/archive_checks.json"),
            backup_profiles_file=env.get("BACKUP_PROFILES_FILE", "/data/backup_profiles.json"),
            profile_backup_container=env.get("MC_PROFILE_BACKUP_CONTAINER", ""),
            profile_backup_env_file=env.get("PROFILE_BACKUP_ENV_FILE", "/data/backup-profile.env"),
            world_dir=env.get("MC_WORLD_DIR", "world"),
            mods_dir=env.get("MC_MODS_DIR", ""),
            servers_file=env.get("SERVERS_FILE", "/data/servers.json"),
            stats_dir=env.get("MC_STATS_DIR", ""),
            usercache_file=env.get("MC_USERCACHE_FILE", ""),
            player_aliases_file=env.get("PLAYER_ALIASES_FILE", "/data/legacy-player-names.json"),
            temp_bans_file=env.get("TEMP_BANS_FILE", "/data/pending_unbans.json"),
            temp_ban_poll_seconds=float(env.get("TEMP_BAN_POLL_SECONDS", "60")),
            restart_poll_seconds=float(env.get("RESTART_POLL_SECONDS", "2")),
            display_names_file=env.get("DISPLAY_NAMES_FILE", "/data/display_names.json"),
            password_store_file=env.get("PASSWORD_STORE_FILE", "/data/passwords.json"),
            sessions_file=env.get("SESSIONS_FILE", "/data/sessions.json"),
            totp_file=env.get("TOTP_FILE", "/data/totp.json"),
            mc_restore_container=env.get("MC_RESTORE_CONTAINER", ""),
            restore_target_file=env.get("RESTORE_TARGET_FILE", "/data/restore-target"),
            mc_doorman_container=env.get("MC_DOORMAN_CONTAINER", ""),
            doorman_config_file=env.get("DOORMAN_CONFIG_FILE", "/data/doorman.json"),
            recurring_restart_file=env.get("RECURRING_RESTART_FILE", "/data/recurring_restart.json"),
            backup_schedule_file=env.get("BACKUP_SCHEDULE_FILE", "/data/backup_schedule.json"),
            discord_webhook_url=env.get("DISCORD_WEBHOOK_URL", ""),
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=env.get("TELEGRAM_CHAT_ID", ""),
            log_exclude=env.get("LOG_EXCLUDE"),
            mc_restore_safety_backup_container=env.get("MC_RESTORE_SAFETY_BACKUP_CONTAINER", ""),
            notify_player_events=flag("NOTIFY_PLAYER_EVENTS", "false"),
            notify_restore_events=flag("NOTIFY_RESTORE_EVENTS", "false"),
            health_alerts=flag("HEALTH_ALERTS", "false"),
            health_poll_seconds=float(env.get("HEALTH_POLL_SECONDS", "30")),
            health_extra_containers=env.get("HEALTH_EXTRA_CONTAINERS", ""),
            watched_containers_file=env.get("WATCHED_CONTAINERS_FILE", "/data/watched_containers.json"),
            notification_config_file=env.get("NOTIFICATION_CONFIG_FILE", "/data/notifications.json"),
            mod_checks_file=env.get("MOD_CHECKS_FILE", "/data/mod_checks.json"),
            app_update_file=env.get("APP_UPDATE_FILE", "/data/app_update.json"),
            app_update_snooze_file=env.get(
                "APP_UPDATE_SNOOZE_FILE", "/data/app_update_snooze.json"
            ),
            app_updater_container=env.get("APP_UPDATER_CONTAINER", "mc-admin-updater"),
            spark_dir=env.get("MC_SPARK_DIR", ""),
            pending_op_levels_file=env.get(
                "PENDING_OP_LEVELS_FILE",
                "/data/pending_op_levels.json",
            ),
            mc_op_levels_container=env.get("MC_OP_LEVELS_CONTAINER", ""),
            login_trusted_proxy_cidrs=env.get("LOGIN_TRUSTED_PROXY_CIDRS", ""),
            storage_history_file=env.get("STORAGE_HISTORY_FILE", "/data/storage_history.json"),
            storage_history_poll_seconds=float(env.get("STORAGE_HISTORY_POLL_SECONDS", "3600")),
            alert_thresholds_file=env.get("ALERT_THRESHOLDS_FILE", "/data/alert_thresholds.json"),
        )


def _read_rbac_yaml(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def load_rbac(path: str, users_file: str = ""):
    """Charge la config RBAC. Renvoie (users, roles).

    V6.5 — un seul cerveau par donnée : roles.yml ne définit que les RÔLES
    (absent = rôles par défaut owner/admin/viewer) ; les COMPTES vivent dans
    `users.json` (source de vérité unique, gérée dans l'UI) et les hash de
    mot de passe dans le store dédié (cf. build_password_store). Une section
    `users:` du YAML n'est plus lue ici — elle est importée une fois par
    migrate_yaml_users, puis ignorée. Zéro compte connu = mode « premier
    lancement » (l'UI propose de créer le compte administrateur)."""
    from domain.rbac import DEFAULT_ROLES_SPEC
    data = _read_rbac_yaml(path)
    roles = build_roles(data.get("roles") or DEFAULT_ROLES_SPEC)
    users = {}
    if users_file:
        from adapters.users_store import JsonUsers
        for username, role_name in JsonUsers(users_file).all().items():
            if role_name == "friend" and role_name not in roles and "viewer" in roles:
                role_name = "viewer"  # compat : l'ancien rôle par défaut a été renommé
            if role_name in roles:
                users[username] = build_users({username: {"role": role_name}}, roles)[username]
    return users, roles


def migrate_yaml_users(path: str, users_store, password_store) -> list[str]:
    """Migration one-shot V6.5 : importe la section `users:` de roles.yml dans
    users.json (rôle) et le store de mots de passe (hash Argon2 copié tel
    quel), puis pose le drapeau `yaml_import_done` — les modifications
    ultérieures du YAML sont ignorées, l'app est propriétaire des comptes.

    Idempotente et prudente : un compte déjà présent dans users.json ou
    supprimé via l'app (tombstones V6.4) n'est pas réimporté ; un hash déjà
    défini côté app n'est pas écrasé. Renvoie les comptes importés (log)."""
    if users_store.yaml_import_done():
        return []
    users_spec = _read_rbac_yaml(path).get("users") or {}
    existing = set(users_store.all())
    skip = existing | users_store.legacy_removed()
    imported: dict[str, str] = {}
    for username, cfg in users_spec.items():
        cfg = cfg or {}
        if username in skip or not cfg.get("role"):
            continue
        imported[username] = str(cfg["role"])
        if cfg.get("password_hash") and not password_store.get(username):
            password_store.set(username, cfg["password_hash"])
    users_store.import_yaml_users(imported)
    if imported:
        import logging
        logging.getLogger("mc_admin").info(
            "migration V6.5 : %d compte(s) importé(s) de roles.yml vers users.json : %s "
            "— la section users: du YAML est désormais ignorée et peut être retirée.",
            len(imported), ", ".join(sorted(imported)),
        )
    return sorted(imported)


# Métriques par défaut (V6.5.3) : le panneau fonctionne SANS metrics.yml.
# `{container}` est remplacé par le nom du conteneur du serveur piloté
# (servers.json) au chargement — jamais codé en dur. Un metrics.yml présent
# REMPLACE intégralement cette liste (pas de fusion : le fichier est la
# vérité s'il existe — cf. config/metrics.example.yml pour le gabarit).
DEFAULT_METRICS: list[dict] = [
    {"key": "players", "label": "Joueurs en ligne", "unit": "",
     "query": 'minecraft_status_players_online_count{server_host="{container}"}',
     "spark": True},
    {"key": "latency", "label": "Latence ping", "unit": "ms",
     "query": 'minecraft_status_response_time_seconds{server_host="{container}"} * 1000'},
    {"key": "cpu", "label": "CPU conteneur", "unit": "%",
     "query": 'rate(container_cpu_usage_seconds_total{name="{container}"}[2m]) * 100'},
    {"key": "ram", "label": "RAM conteneur", "unit": "Mio",
     "query": 'container_memory_working_set_bytes{name="{container}"} / 1048576',
     "spark": True},
    # TPS/MSPT : mod FabricExporter côté serveur (s'appuie sur spark), scrapé
    # par Prometheus — pas de label conteneur, l'exporteur est par serveur.
    {"key": "tps", "label": "TPS", "unit": "", "query": "minecraft_tps", "spark": True},
    {"key": "mspt", "label": "MSPT moyen", "unit": "ms", "query": 'minecraft_mspt{type="mean"}'},
]


def load_metrics(path: str, container: str = "") -> list[dict]:
    """Specs de métriques (PromQL prédéfini, jamais soumis par l'UI).

    Fichier absent = DÉFAUTS embarqués, `{container}` résolu ; présent = il
    remplace tout (y compris présent-mais-vide = panneau volontairement vide)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return [
            {**spec, "query": spec["query"].replace("{container}", container)}
            for spec in DEFAULT_METRICS
        ]
    specs = data.get("metrics") or []
    for spec in specs:
        missing = {"key", "label", "query"} - set(spec)
        if missing:
            raise ValueError(f"metrics.yml : champs manquants {missing} dans {spec}")
    return specs


def build_player_history(settings: Settings):
    """Adapter PlayerHistoryPort (SQLite). Ne garde aucune connexion ouverte
    entre les appels : construire plusieurs instances sur le MÊME fichier
    (service + tailer de logs, cf. app/api/app.py) est sans risque."""
    from adapters.player_history import SqlitePlayerHistory

    return SqlitePlayerHistory(settings.player_history_db)


def build_temp_bans(settings: Settings):
    """Adapter TempBanPort (JSON). Plusieurs instances peuvent viser le même
    fichier : leur verrou partagé par chemin protège les cycles lecture-écriture
    (service qui planifie, TempBanScheduler qui lève automatiquement)."""
    from adapters.temp_bans import JsonTempBans

    return JsonTempBans(settings.temp_bans_file)


def build_display_names(settings: Settings):
    """Store des noms affichés (préférence UI). Accédé par la couche API, pas
    par AdminService (ce n'est pas une action RBAC — cf. adapter)."""
    from adapters.display_names import JsonDisplayNames

    return JsonDisplayNames(settings.display_names_file)


def build_password_store(settings: Settings):
    """Overlay inscriptible des hash de mot de passe (roles.yml reste RO)."""
    from adapters.credentials_store import JsonCredentials

    return JsonCredentials(settings.password_store_file)


def build_notification_config(settings: Settings):
    """Store de config des notifications + amorçage one-shot depuis l'env
    (canaux DISCORD_WEBHOOK_URL/TELEGRAM_* et interrupteurs NOTIFY_*/
    HEALTH_ALERTS) — ensuite, l'UI est la source de vérité."""
    from adapters.notification_config import JsonNotificationConfig

    store = JsonNotificationConfig(settings.notification_config_file)
    store.import_from_env(
        channels={
            "discord_webhook_url": settings.discord_webhook_url,
            "telegram_bot_token": settings.telegram_bot_token,
            "telegram_chat_id": settings.telegram_chat_id,
        },
        events={
            "player": settings.notify_player_events,
            "health": settings.health_alerts,
            "restore": settings.notify_restore_events,
        },
    )
    return store


def build_notifications(settings: Settings):
    """Notifier adossé à notifications.json (relu à chaud) — un changement
    dans l'UI est effectif au prochain envoi, sans redémarrage."""
    from adapters.notifications import StoreBackedNotifier

    return StoreBackedNotifier(build_notification_config(settings))


def build_watched_store(settings: Settings):
    """Store des compagnons surveillés + amorçage one-shot depuis l'env."""
    from adapters.watched_store import JsonWatched

    store = JsonWatched(settings.watched_containers_file)
    store.import_from_env(
        [n.strip() for n in settings.health_extra_containers.split(",") if n.strip()])
    return store


def build_health_watcher(settings: Settings):
    """HealthWatcher (alertes serveur/compagnons down). Toujours actif depuis
    l'A2.3 : l'interrupteur « health » (notifications.json, réglé dans l'UI)
    filtre les envois — le thread, lui, coûte une sonde de 30 s. Le serveur
    surveillé est celui de servers.json (comme build_service)."""
    from adapters.docker_proxy import DockerProxyContainer
    from adapters.servers_store import JsonServers
    from health_watcher import HealthWatcher

    first = next(iter(JsonServers(settings.servers_file).list_servers()), None)
    server = (first.container if first else "") or settings.minecraft_container
    return HealthWatcher(
        DockerProxyContainer(server),
        build_notifications(settings),
        poll_seconds=settings.health_poll_seconds,
        watched_store=build_watched_store(settings),
        port_factory=DockerProxyContainer,
    )


def build_perf_watcher(settings: Settings):
    """PerfWatcher (MSPT soutenu + disque bas) — comme le HealthWatcher :
    toujours actif, ce sont les interrupteurs par canal qui filtrent. Seuils
    réglables (backlog fiabilité n° 4) : instance AlertThresholdsPort SÉPARÉE
    de celle du service (le watcher relit, le service écrit — même politique
    que StoreBackedNotifier avec notifications.json)."""
    from adapters.alert_thresholds import JsonAlertThresholds
    from adapters.backup_archives import FileBackupArchives
    from adapters.prometheus import PrometheusMetrics
    from perf_watcher import PerfWatcher

    return PerfWatcher(
        PrometheusMetrics(settings.prometheus_url, []),
        FileBackupArchives(settings.backup_archives_dir),
        build_notifications(settings),
        thresholds=JsonAlertThresholds(settings.alert_thresholds_file),
    )


def build_mod_update_checker(settings: Settings):
    """Checker Modrinth de fond — None si la page Mods est absente (pas de
    dossier mods configuré). La version MC courante vient de mc-monitor via
    Prometheus (même source que la carte mise à jour)."""
    if not settings.mods_dir:
        return None
    from adapters.mod_checks import JsonModChecks
    from adapters.mods import FileMods
    from adapters.modrinth import ModrinthCatalog
    from adapters.updater import current_server_version
    from mod_update_checker import ModUpdateChecker

    return ModUpdateChecker(
        FileMods(settings.mods_dir),
        ModrinthCatalog(),
        JsonModChecks(settings.mod_checks_file),
        version_provider=lambda: current_server_version(settings.prometheus_url),
    )


def build_app_update_checker(settings: Settings):
    """Checker de mise à jour de mc-admin (bouton MAJ) — releases GitHub."""
    from adapters.app_update import GithubReleases, JsonAppUpdate
    from app_update_checker import AppUpdateChecker

    return AppUpdateChecker(
        GithubReleases(APP_REPO),
        JsonAppUpdate(settings.app_update_file),
    )


def build_backup_schedule(settings: Settings):
    """Ancienne planification V5 (backup_schedule.json) : n'est plus lue que
    par la MIGRATION vers les profils (seed_from_legacy) — l'UI et le
    planificateur ne connaissent que les profils depuis la V6.5."""
    from adapters.backup_schedule import JsonBackupSchedule

    return JsonBackupSchedule(settings.backup_schedule_file)


def build_restart_schedule():
    """Adapter RestartSchedulerPort (en mémoire, non persistant — cf. adapter)."""
    from adapters.restart_schedule import InMemoryRestartSchedule

    return InMemoryRestartSchedule()


def build_archive_verifier(settings: Settings):
    """Vérificateur d'intégrité des archives (thread de fond). Instance
    d'adapters SÉPARÉE de celle du service (même politique que les watchers :
    le vérificateur écrit, le service lit, fichiers réouverts à chaque appel)."""
    from adapters.archive_checks import JsonArchiveChecks
    from adapters.backup_archives import FileBackupArchives
    from archive_verifier import ArchiveVerifier
    return ArchiveVerifier(
        FileBackupArchives(settings.backup_archives_dir),
        JsonArchiveChecks(settings.archive_checks_file),
    )


def build_storage_history_watcher(settings: Settings):
    """Historique de stockage (thread de fond, backlog fiabilité n° 6).
    Instance d'adapters SÉPARÉE de celle du service (même politique que
    build_archive_verifier) : le watcher écrit, le service lit."""
    from adapters.backup_archives import FileBackupArchives
    from adapters.storage_history import JsonStorageHistory
    from storage_history_watcher import StorageHistoryWatcher
    return StorageHistoryWatcher(
        FileBackupArchives(settings.backup_archives_dir),
        JsonStorageHistory(settings.storage_history_file),
        poll_seconds=settings.storage_history_poll_seconds,
    )


def build_service(settings: Settings):
    """Assemble l'AdminService avec les adapters concrets (appelé au démarrage).

    V6.3 : le PREMIER serveur de servers.json (assistant « Ajouter un
    serveur ») prime sur les variables d'env — la config env existante est
    migrée en première entrée au premier démarrage (seed idempotent)."""
    from dataclasses import replace as _dc_replace

    from adapters.servers_store import JsonServers
    servers_store = JsonServers(settings.servers_file)
    servers_store.seed_from_env(settings)
    first = next(iter(servers_store.list_servers()), None)
    if first is not None:
        settings = _dc_replace(
            settings,
            minecraft_container=first.container or settings.minecraft_container,
            rcon_host=first.rcon_host or settings.rcon_host,
            rcon_port=first.rcon_port or settings.rcon_port,
            mc_log_file=first.log_file or settings.mc_log_file,
            world_dir=first.world_dir or settings.world_dir,
        )
    # Imports locaux : évite de charger les adapters (donc leurs libs) hors runtime.
    from adapters.audit_file import FileAuditLog
    from adapters.archive_validation import FileArchiveValidator
    from adapters.backup import DockerBackupTrigger
    from adapters.backup_archives import FileBackupArchives
    from adapters.bans_file import FileBans
    from adapters.clock import SystemClock
    from adapters.docker_proxy import DockerProxyContainer, DockerServerDiscovery
    from adapters.log_file import DEFAULT_EXCLUDE, FileLog
    from adapters.ops_file import OpsFile
    from adapters.prometheus import PrometheusMetrics
    from adapters.rcon import RconGame
    from adapters.updater import MinecraftUpdater
    from domain.services import AdminService

    restore_safety_backup = (
        DockerBackupTrigger(settings.mc_restore_safety_backup_container)
        if settings.mc_restore_safety_backup_container
        else None  # le service substitue son null object (_NoBackup)
    )
    backup_archives = FileBackupArchives(settings.backup_archives_dir)

    from adapters.archive_checks import JsonArchiveChecks
    from adapters.alert_thresholds import JsonAlertThresholds
    from adapters.storage_history import JsonStorageHistory
    from adapters.backup_profiles import JsonBackupProfiles
    from adapters.mod_checks import JsonModChecks
    from adapters.mods import FileMods
    from adapters.op_levels import DockerOpLevelsApply
    from adapters.pending_op_levels import JsonPendingOpLevels
    from adapters.pending_restore import InMemoryPendingRestore
    from adapters.player_stats import FilePlayerStats
    from adapters.recurring_restart import JsonRecurringRestart
    from adapters.restore import DockerRestoreTrigger, NotConfiguredRestore
    restore = (
        DockerRestoreTrigger(settings.mc_restore_container, settings.restore_target_file)
        if settings.mc_restore_container
        else NotConfiguredRestore()
    )

    from adapters.doorman import DockerDoorman, NotConfiguredDoorman
    from adapters.maintenance_state import InMemoryPendingMaintenance
    doorman = (
        DockerDoorman(settings.mc_doorman_container, settings.doorman_config_file)
        if settings.mc_doorman_container
        else NotConfiguredDoorman()
    )

    notifications = build_notifications(settings)

    from adapters.worker_integrity import DockerWorkerIntegrity, WorkerSpec
    worker_specs = []
    if settings.mc_restore_container:
        worker_specs.append(WorkerSpec(
            settings.mc_restore_container, "Outil de restauration",
            same_image=True, kinds=frozenset({"restore"})))
    if settings.mc_op_levels_container:
        worker_specs.append(WorkerSpec(
            settings.mc_op_levels_container, "Outil des niveaux OP",
            same_image=True))
    if settings.profile_backup_container:
        worker_specs.append(WorkerSpec(
            settings.profile_backup_container, "Outil de sauvegarde",
            kinds=frozenset({"backup"})))
    if settings.mc_doorman_container:
        # Même image que mc-admin (le portier EST app/doorman_server.py) et même
        # réseau de jeu : un portier périmé ou hors réseau ne tiendrait pas
        # l'adresse du serveur, et la maintenance fermerait le serveur pour rien.
        worker_specs.append(WorkerSpec(
            settings.mc_doorman_container, "Portier de maintenance",
            same_image=True, kinds=frozenset({"maintenance"})))
    if settings.mc_restore_safety_backup_container:
        worker_specs.append(WorkerSpec(
            settings.mc_restore_safety_backup_container,
            "Outil de sauvegarde de sécurité", kinds=frozenset({"restore"})))
    if settings.mc_updater_container:
        worker_specs.append(WorkerSpec(
            settings.mc_updater_container, "Outil de mise à jour"))
    worker_integrity = DockerWorkerIntegrity(worker_specs) if worker_specs else None

    backup_profiles = None
    profile_backup = None
    if settings.profile_backup_container:
        from adapters.backup import DockerProfileBackup
        backup_profiles = JsonBackupProfiles(settings.backup_profiles_file)
        # Migration V5 -> V6 : au premier démarrage, les sauvegardes
        # manuelles/automatiques existantes deviennent deux profils.
        backup_profiles.seed_from_legacy(build_backup_schedule(settings))
        profile_backup = DockerProfileBackup(
            settings.profile_backup_container, settings.profile_backup_env_file,
        )

    return AdminService(
        game=RconGame(settings.rcon_host, settings.rcon_port, settings.rcon_password),
        container=DockerProxyContainer(settings.minecraft_container),
        logs=FileLog(
            settings.mc_log_file,
            exclude=(settings.log_exclude if settings.log_exclude is not None else DEFAULT_EXCLUDE) or None,
        ),
        audit=FileAuditLog(settings.audit_log),
        clock=SystemClock(),
        container_name=settings.minecraft_container,
        metrics=PrometheusMetrics(
            settings.prometheus_url,
            load_metrics(settings.metrics_config, settings.minecraft_container),
        ),
        updater=MinecraftUpdater(settings.prometheus_url, settings.mc_updater_container),
        ops=OpsFile(settings.ops_file),
        notifications=notifications,
        player_history=build_player_history(settings),
        backup_archives=backup_archives,
        bans=FileBans(settings.bans_file),
        temp_bans=build_temp_bans(settings),
        restart_schedule=build_restart_schedule(),
        pending_op_levels=JsonPendingOpLevels(settings.pending_op_levels_file),
        op_levels_apply=(
            DockerOpLevelsApply(settings.mc_op_levels_container)
            if settings.mc_op_levels_container
            else None
        ),
        notification_config=build_notification_config(settings),
        restore_safety_backup=restore_safety_backup,
        restore=restore,
        doorman=doorman,
        pending_maintenance=InMemoryPendingMaintenance(),
        recurring_restart=JsonRecurringRestart(settings.recurring_restart_file),
        pending_restore=InMemoryPendingRestore(),
        player_stats=(FilePlayerStats(settings.stats_dir, settings.usercache_file,
                                      settings.player_aliases_file)
                      if settings.stats_dir else None),
        archive_checks=JsonArchiveChecks(settings.archive_checks_file),
        archive_validator=FileArchiveValidator(backup_archives),
        worker_integrity=worker_integrity,
        storage_history=JsonStorageHistory(settings.storage_history_file),
        alert_thresholds=JsonAlertThresholds(settings.alert_thresholds_file),
        backup_profiles=backup_profiles,
        profile_backup=profile_backup,
        world_dir=settings.world_dir,
        mods=FileMods(settings.mods_dir) if settings.mods_dir else None,
        spark=__import__("adapters.spark", fromlist=["FileSparkProfiles"]).FileSparkProfiles(settings.spark_dir) if settings.spark_dir else None,
        servers=servers_store,
        server_discovery=DockerServerDiscovery(),
        watched=build_watched_store(settings),
        mod_checks=JsonModChecks(settings.mod_checks_file) if settings.mods_dir else None,
        companion_port_factory=DockerProxyContainer,
        map_probe=__import__("adapters.map_fetch", fromlist=["MapProbe"]).MapProbe(),
        app_update_state=__import__("adapters.app_update", fromlist=["JsonAppUpdate"]).JsonAppUpdate(settings.app_update_file),
        app_update_snooze=__import__("adapters.app_update", fromlist=["JsonAppUpdateSnooze"]).JsonAppUpdateSnooze(settings.app_update_snooze_file),
        app_updater=__import__("adapters.app_update", fromlist=["DockerAppUpdater"]).DockerAppUpdater(settings.app_updater_container),
        self_image=__import__("adapters.app_update", fromlist=["DockerSelfImage"]).DockerSelfImage(),
    )
