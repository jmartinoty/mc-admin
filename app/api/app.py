"""Fabrique FastAPI (composition root HTTP).

Assemble : middleware de session (cookies signés), état applicatif (users,
credentials, service, hasher), fichiers statiques et routes.
L'instance uvicorn vit dans `api.main` (ce module reste importable par les
tests sans effet de bord). `service`/`hasher` sont injectables pour tester la
couche HTTP avec des fakes.
"""
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from api.auth import Argon2Hasher
from api.login_security import ClientIpResolver, LoginAttemptLimiter
from api.routes import router
from config import (
    Settings,
    build_app_update_checker,
    build_archive_verifier,
    build_display_names,
    build_health_watcher,
    build_mod_update_checker,
    build_perf_watcher,
    build_notifications,
    build_password_store,
    build_player_history,
    build_service,
    build_temp_bans,
    load_rbac,
    migrate_yaml_users,
)
from player_watcher import PlayerLogWatcher
from maintenance_scheduler import MaintenanceScheduler
from restart_scheduler import RestartWarningScheduler
from restore_coordinator import RestoreCoordinator
from scheduler import BackupScheduler
from tempban_scheduler import TempBanScheduler

logger = logging.getLogger("mc_admin")


@contextmanager
def _running_background_tasks(tasks):
    """Démarre les tâches dans l'ordre et les arrête toutes en ordre inverse.

    Une tâche est enregistrée avant son démarrage : si ``start`` échoue après
    avoir créé une ressource, son ``stop`` est quand même tenté. Une erreur de
    nettoyage est journalisée sans masquer l'erreur initiale ni empêcher les
    autres tâches de s'arrêter.
    """
    started = []
    try:
        for name, task in tasks:
            if task is None:
                continue
            started.append((name, task))
            try:
                task.start()
            except Exception:  # noqa: BLE001 - rollback de composition
                logger.exception("échec du démarrage de la tâche %s", name)
                raise
        yield
    finally:
        for name, task in reversed(started):
            try:
                task.stop()
            except Exception:  # noqa: BLE001 - le nettoyage doit continuer
                logger.exception("échec de l'arrêt de la tâche %s", name)


def create_app(
    settings: Settings | None = None,
    service=None,
    hasher=None,
    scheduler=None,
    player_watcher=None,
    tempban_scheduler=None,
    restart_scheduler=None,
    display_names=None,
    password_overrides=None,
    health_watcher=None,
    perf_watcher=None,
    mod_update_checker=None,
    restore_coordinator=None,
    archive_verifier=None,
    login_limiter=None,
    client_ip_resolver=None,
    map_open=None,
    app_update_checker=None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if not settings.session_secret:
        raise RuntimeError("SESSION_SECRET manquant : refuse de démarrer sans clé de signature de session.")

    from adapters.users_store import JsonUsers
    users_store = JsonUsers(settings.users_file)
    password_store = (
        password_overrides if password_overrides is not None else build_password_store(settings)
    )
    # Migration one-shot V6.5 : les comptes d'un roles.yml historique passent
    # dans users.json/passwords.json AVANT le chargement — cf. config.
    migrate_yaml_users(settings.rbac_config, users_store, password_store)
    users, roles = load_rbac(settings.rbac_config, settings.users_file)
    built_service = service if service is not None else build_service(settings)
    backup_scheduler = (
        scheduler if scheduler is not None else BackupScheduler(built_service)
    )
    # Instance SÉPARÉE de celle utilisée par AdminService (même fichier SQLite,
    # aucune connexion persistante gardée par l'objet -> sans risque, cf.
    # config.build_player_history) : le tailer ÉCRIT, le service LIT, chacun
    # via sa propre instance.
    watcher = (
        player_watcher
        if player_watcher is not None
        else PlayerLogWatcher(
            settings.mc_log_file,
            build_player_history(settings),
            # Joins/leaves -> canaux configurés ; l'interrupteur « player »
            # (notifications.json, réglé dans l'UI) filtre dans le notifier.
            notifier=build_notifications(settings),
        )
    )
    # Alerte serveur/compagnons down (filtrée par l'interrupteur « health »).
    health = health_watcher if health_watcher is not None else build_health_watcher(settings)
    perf = perf_watcher if perf_watcher is not None else build_perf_watcher(settings)
    mod_checker = (mod_update_checker if mod_update_checker is not None
                   else build_mod_update_checker(settings))
    app_checker = (app_update_checker if app_update_checker is not None
                   else build_app_update_checker(settings))
    # Même logique d'instance séparée que ci-dessus (cf. build_temp_bans).
    tempban_sched = (
        tempban_scheduler
        if tempban_scheduler is not None
        else TempBanScheduler(built_service, build_temp_bans(settings), settings.temp_ban_poll_seconds)
    )
    # Contrairement aux deux ci-dessus, PAS d'instance séparée du port : l'état
    # d'un redémarrage programmé est en mémoire (InMemoryRestartSchedule, sans
    # fichier), donc UNE SEULE instance existe (celle tenue par AdminService) —
    # ce thread ne la touche jamais directement, il ne connaît que le service.
    restart_sched = (
        restart_scheduler
        if restart_scheduler is not None
        else RestartWarningScheduler(built_service, settings.restart_poll_seconds)
    )
    # Même famille et mêmes raisons que le restart scheduler : l'annonce de
    # maintenance vit en mémoire dans l'unique instance tenue par AdminService.
    maintenance_sched = MaintenanceScheduler(built_service, settings.restart_poll_seconds)
    # Comme le restart scheduler : l'état (restauration en attente) vit en
    # mémoire dans l'unique instance tenue par AdminService — le thread ne
    # connaît que le service.
    restore_coord = (
        restore_coordinator
        if restore_coordinator is not None
        else RestoreCoordinator(built_service)
    )
    verifier = archive_verifier if archive_verifier is not None else build_archive_verifier(settings)
    background_tasks = (
        ("backup-scheduler", backup_scheduler),
        ("player-watcher", watcher),
        ("tempban-scheduler", tempban_sched),
        ("restart-scheduler", restart_sched),
        ("maintenance-scheduler", maintenance_sched),
        ("restore-coordinator", restore_coord),
        ("archive-verifier", verifier),
        ("health-watcher", health),
        ("perf-watcher", perf),
        ("mod-update-checker", mod_checker),
        ("app-update-checker", app_checker),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with _running_background_tasks(background_tasks):
            yield

    app = FastAPI(title="mc-admin", docs_url=None, redoc_url=None, lifespan=lifespan)
    # Cookies de session : signés + HttpOnly (défaut Starlette) + Secure + SameSite=Lax.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        https_only=settings.cookie_secure,  # -> attribut Secure
        same_site="lax",
    )

    app.state.settings = settings
    app.state.users = users
    app.state.roles = roles
    app.state.users_store = users_store
    app.state.service = built_service
    # Relais borné de la carte web (page Carte) — injectable pour les tests.
    if map_open is None:
        from adapters.map_fetch import open_map_path as map_open
    app.state.map_open = map_open
    password_hasher = hasher if hasher is not None else Argon2Hasher()
    app.state.hasher = password_hasher
    # Même coût de vérification pour un pseudo absent : évite de révéler
    # l'existence d'un compte par le temps de réponse.
    app.state.login_dummy_hash = password_hasher.hash(secrets.token_urlsafe(32))
    app.state.scheduler = backup_scheduler
    app.state.player_watcher = watcher
    app.state.tempban_scheduler = tempban_sched
    app.state.restart_scheduler = restart_sched
    app.state.display_names = display_names if display_names is not None else build_display_names(settings)
    app.state.password_overrides = password_store
    app.state.health_watcher = health
    app.state.perf_watcher = perf
    app.state.mod_update_checker = mod_checker
    app.state.restore_coordinator = restore_coord
    app.state.archive_verifier = verifier
    app.state.login_limiter = (
        login_limiter if login_limiter is not None else LoginAttemptLimiter()
    )
    app.state.client_ip_resolver = (
        client_ip_resolver
        if client_ip_resolver is not None
        else ClientIpResolver.from_config(settings.login_trusted_proxy_cidrs)
    )

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(router)
    return app
