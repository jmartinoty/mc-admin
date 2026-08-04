"""Tableau de bord : status, fragment pollé, cycle de vie du serveur,
console RCON, mise à jour contrôlée."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import urllib.parse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from adapters.map_fetch import MapUnreachable
from api.routes.common import _csrf, _current, _nav_context, _redirect_target, _require_csrf, flash_error, templates
from config import APP_VERSION
from domain.errors import DomainError, PermissionDenied
from domain.model import Permission

router = APIRouter()


def _humanize_uptime(started_at) -> str | None:
    """« 3 j 14 h », « 5 h 12 min », « 8 min » — deux unités max, lisible."""
    if started_at is None:
        return None
    seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
    if seconds < 0:
        return None
    minutes = int(seconds // 60)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days} j {hours} h" if hours else f"{days} j"
    if hours:
        return f"{hours} h {mins} min" if mins else f"{hours} h"
    return f"{mins} min" if mins else "moins d'une minute"



def _minutes_until(hhmm: str) -> float:
    """Minutes d'ici la prochaine occurrence de HH:MM (heure locale ; demain si
    l'heure est déjà passée). Lève ValueError si le format est invalide."""
    hour, minute = (int(part) for part in hhmm.split(":"))
    local_now = datetime.now().astimezone()
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local_now:
        target += timedelta(days=1)
    return (target - local_now).total_seconds() / 60.0


def _game_state(status) -> str | None:
    """Pastille honnête (retour Jeremy 18/07) : un conteneur allumé ne veut
    pas dire un jeu joignable. < 5 min d'uptime = démarrage en cours ;
    au-delà, le jeu est réellement injoignable (RCON muet)."""
    if status is None or not status.container.running or status.players_available:
        return None
    started = status.container.started_at
    if started is not None and (datetime.now(timezone.utc) - started).total_seconds() < 300:
        return "starting"
    return "unreachable"

def _status_context(request: Request, user) -> dict:
    svc = request.app.state.service
    try:
        status = svc.get_status(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError:
        # Conteneur/proxy injoignable (ex. installation pas terminée) : la
        # page doit VIVRE et le dire, pas renvoyer une erreur 500 brute.
        status = None
    # 300 = plafond : l'adapter coupe au dernier démarrage du serveur s'il est plus proche.
    logs = svc.recent_logs(user, 300) if user.can(Permission.LOGS_VIEW) else []
    try:
        metrics = svc.metrics_snapshot(user)
    except DomainError:
        metrics = None  # Prometheus injoignable -> panneau « indisponible »
    can_restart = user.can(Permission.RESTART)
    uptime = None
    if status is not None and status.container.running:
        uptime = _humanize_uptime(status.container.started_at)
    try:
        server_address = svc.server_address(user)
    except DomainError:
        server_address = ""
    try:
        maintenance = svc.maintenance_status(user)
    except DomainError:
        maintenance = None  # jamais de 500 : le bandeau disparaît, c'est tout
    return {
        "server_address": server_address,
        "maintenance": maintenance,
        "can_maintenance": user.can(Permission.MAINTENANCE),
        "metrics": metrics,
        "status": status,
        "game_state": _game_state(status),
        "uptime": uptime,
        "logs": logs,
        "csrf_token": _csrf(request),
        "can_restart": can_restart,
        "can_start": user.can(Permission.START),
        "can_stop": user.can(Permission.STOP),
        "can_kick": user.can(Permission.KICK),
        "can_logs": user.can(Permission.LOGS_VIEW),
        "can_console": user.can(Permission.RCON_RAW),
    }


def _rcon_context(request: Request, user) -> dict:
    return {
        "can_logs": user.can(Permission.LOGS_VIEW),
        "can_console": user.can(Permission.RCON_RAW),
        "last_command": request.session.pop("rcon_command", None),
        "last_output": request.session.pop("rcon_output", None),
        "last_error": request.session.pop("rcon_error", None),
        "csrf_token": _csrf(request),
    }


# --- status (page + fragment pour le polling) ---

@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    ctx = {
        **_nav_context(request, user, "status"),
        **_status_context(request, user),
        **_rcon_context(request, user),
    }
    # Carte mise à jour : page complète uniquement (le check interroge Mojang —
    # pas question de le mettre dans le fragment pollé toutes les 5 s).
    ctx["recurring_restart"] = (
        request.app.state.service.recurring_restart_status(user) if user.can(Permission.RESTART) else None
    )
    ctx["scheduled_maintenance"] = (
        request.app.state.service.list_scheduled_maintenance(user)
        if user.can(Permission.MAINTENANCE) else []
    )
    ctx["weekday_labels"] = ("Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim")
    ctx["show_infra"] = user.can(Permission.SERVER_MANAGE)
    ctx["show_runtime_health"] = ctx["show_infra"]
    ctx["dashboard_mode"] = (
        "operations"
        if any(
            user.can(permission)
            for permission in (
                Permission.RESTART,
                Permission.START,
                Permission.STOP,
                Permission.UPDATE,
                Permission.GAME_SETTINGS,
                Permission.RCON_RAW,
            )
        )
        else "overview"
    )
    ctx["infra"] = None
    if ctx["show_infra"]:
        try:
            infra = request.app.state.service.infra_status(user)
            ctx["infra"] = infra if len(infra) > 1 else None
        except DomainError:
            pass
    try:
        ctx["mod_count"] = len(request.app.state.service.mods_list(user))
    except DomainError:
        ctx["mod_count"] = None
    ctx["can_game"] = user.can(Permission.GAME_SETTINGS)
    from api.gamerule_help import GAMERULE_HELP
    ctx["rule_help"] = GAMERULE_HELP
    ctx["game"] = None
    if ctx["can_game"]:
        try:
            ctx["game"] = request.app.state.service.game_settings(user)
        except DomainError:
            ctx["game"] = None  # RCON injoignable -> carte « indisponible »
    ctx["can_update"] = user.can(Permission.UPDATE)
    ctx["update"] = None
    ctx["app_update"] = None
    if ctx["can_update"]:
        try:
            ctx["update"] = request.app.state.service.update_status(user)
        except DomainError:
            ctx["update"] = None
        try:
            status = request.app.state.service.app_update_status(user, APP_VERSION)
            ctx["app_update"] = status if (status.update_available and not status.snoozed) else None
        except DomainError:
            ctx["app_update"] = None
    return templates.TemplateResponse(request, "status.html", ctx)


@router.post("/actions/app-update")
def action_app_update(request: Request, csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.apply_app_update(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "La mise à jour de mc-admin n'a pas pu être lancée",
                    code="MAJ-01", details=str(exc))
        return RedirectResponse("/", status_code=303)
    return RedirectResponse("/updating", status_code=303)


@router.post("/actions/app-update/snooze")
def action_app_update_snooze(request: Request, csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.snooze_app_update(user)
        request.session["flash"] = "D'accord, je te le rappellerai demain."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Impossible de reporter le rappel", code="MAJ-02", details=str(exc))
    return RedirectResponse("/", status_code=303)


@router.get("/updating")
def updating_page(request: Request):
    """Page d'attente pendant que l'updater recrée mc-admin : sonde /healthz
    et revient à l'accueil quand l'application répond de nouveau."""
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "updating.html",
                                      {"csrf_token": _csrf(request)})


# --- console RCON live (owner : RCON_RAW, aucune restriction supplémentaire —
# c'est l'échappatoire volontairement complète, cf. domain/services.py) ---

@router.get("/console", response_class=HTMLResponse)
def console_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        request.app.state.service.can_access_console(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    ctx = _nav_context(request, user, "console")
    ctx["last_command"] = request.session.pop("rcon_command", None)
    ctx["last_output"] = request.session.pop("rcon_output", None)
    ctx["last_error"] = request.session.pop("rcon_error", None)
    return templates.TemplateResponse(request, "console.html", ctx)


@router.post("/actions/rcon")
def action_rcon(request: Request, command: str = Form(...), csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    request.session["rcon_command"] = command
    try:
        request.session["rcon_output"] = request.app.state.service.run_rcon(user, command)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        request.session["rcon_error"] = str(exc)
    # Ancre : ne pas renvoyer l'utilisateur en haut de page alors que le
    # terminal (et le résultat de sa commande) est en bas.
    return RedirectResponse("/#terminal", status_code=303)


@router.get("/fragment/status", response_class=HTMLResponse)
def fragment_status(request: Request):
    user = _current(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Non authentifié")
    return templates.TemplateResponse(request, "partials/status_body.html", _status_context(request, user))


# --- actions (POST, protégées CSRF ; RBAC appliquée par le service) ---

@router.post("/actions/restart")
def action_restart(request: Request, csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.restart(user)
        request.session["flash"] = "Redémarrage demandé."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Le redémarrage a échoué", code="SRV-01", details=str(exc))
    return RedirectResponse("/", status_code=303)


@router.post("/actions/schedule-restart")
def action_schedule_restart(
    request: Request,
    minutes: str = Form(""),
    seconds: str = Form(""),
    defer_if_players: str = Form(""),
    csrf_token: str = Form(...),
):
    """Délai en secondes (dialogue, défaut 30 s -> décompte in-game) ou en
    minutes (compat). <= 0 = redémarrage immédiat sans avertissement."""
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        minutes_value = float(minutes) if minutes.strip() else float(seconds or "0") / 60.0
        if minutes_value <= 0:
            request.app.state.service.restart(user)
            request.session["flash"] = "Redémarrage demandé."
        else:
            request.app.state.service.schedule_restart(
                user, minutes_value, defer_if_players=bool(defer_if_players)
            )
            delay_txt = (
                f"{minutes_value:g} minute(s)" if minutes_value >= 1
                else f"{int(minutes_value * 60)} seconde(s)"
            )
            request.session["flash"] = f"Redémarrage programmé dans {delay_txt}."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DomainError, ValueError) as exc:
        flash_error(request, "La programmation du redémarrage a échoué", code="SRV-02", details=str(exc))
    return RedirectResponse("/", status_code=303)


@router.post("/actions/recurring-restart")
def action_recurring_restart(
    request: Request,
    time_hhmm: str = Form(""),
    lead_seconds: str = Form("300"),
    defer_if_players: str = Form(""),
    disable: str = Form(""),
    csrf_token: str = Form(...),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        if disable:
            request.app.state.service.clear_recurring_restart(user)
            request.session["flash"] = "Redémarrage quotidien désactivé."
        else:
            request.app.state.service.set_recurring_restart(
                user, time_hhmm.strip(), int(lead_seconds),
                defer_if_players=bool(defer_if_players),
            )
            request.session["flash"] = f"Redémarrage quotidien activé : tous les jours à {time_hhmm.strip()}."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DomainError, ValueError) as exc:
        flash_error(request, "Le redémarrage quotidien n'a pas pu être réglé", code="SRV-03", details=str(exc))
    return RedirectResponse("/", status_code=303)


@router.post("/actions/cancel-restart")
def action_cancel_restart(request: Request, csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.cancel_scheduled_restart(user)
        request.session["flash"] = "Programmation de redémarrage annulée."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return RedirectResponse("/", status_code=303)


@router.post("/actions/start")
def action_start(request: Request, csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.start(user)
        request.session["flash"] = "Démarrage demandé."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Le démarrage a échoué", code="SRV-04", details=str(exc))
    return RedirectResponse("/", status_code=303)


@router.post("/actions/stop")
def action_stop(request: Request, csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.stop(user)
        request.session["flash"] = "Arrêt demandé."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "L'arrêt a échoué", code="SRV-05", details=str(exc))
    return RedirectResponse("/", status_code=303)


# --- mode maintenance (owner : MAINTENANCE, garde-fous dans le service) ---

@router.post("/actions/maintenance")
def action_enter_maintenance(
    request: Request,
    message: str = Form(""),
    until: str = Form(""),
    grace_minutes: str = Form(""),
    at_time: str = Form(""),
    defer_if_players: str = Form(""),
    csrf_token: str = Form(...),
):
    """Ferme le serveur derrière le portier. Trois modes de délai : vide/<=0 =
    tout de suite ; `grace_minutes` = dans X minutes ; `at_time` (HH:MM) = à
    heure fixe (aujourd'hui, ou demain si l'heure est passée)."""
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        if at_time.strip():
            grace = _minutes_until(at_time.strip())
        else:
            grace = float(grace_minutes) if grace_minutes.strip() else 0.0
        request.app.state.service.enter_maintenance(
            user, message=message, until=until, grace_minutes=grace,
            defer_if_players=bool(defer_if_players),
        )
        request.session["flash"] = (
            f"Fermeture pour maintenance dans {grace:g} minute(s)." if grace > 0
            else "Le serveur est en maintenance."
        )
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DomainError, ValueError) as exc:
        flash_error(request, "La mise en maintenance a échoué", code="SRV-06", details=str(exc))
    return RedirectResponse("/", status_code=303)


@router.post("/actions/scheduled-maintenance/add")
def action_scheduled_maintenance_add(
    request: Request,
    kind: str = Form("once"),
    time_hhmm: str = Form(""),
    date: str = Form(""),
    weekdays: list[str] = Form(default=[]),
    lead_seconds: str = Form("300"),
    message: str = Form(""),
    until: str = Form(""),
    defer_if_players: str = Form(""),
    csrf_token: str = Form(...),
):
    """Ajoute une maintenance programmée : date précise (kind=once) OU jours de
    semaine cochés (kind=weekly)."""
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        days = tuple(int(w) for w in weekdays if w.strip().lstrip("-").isdigit())
        request.app.state.service.add_scheduled_maintenance(
            user, kind=kind.strip(), time_hhmm=time_hhmm.strip(),
            date=date.strip(), weekdays=days, lead_seconds=int(lead_seconds),
            message=message, until=until, defer_if_players=bool(defer_if_players),
        )
        request.session["flash"] = "Maintenance programmée ajoutée."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DomainError, ValueError) as exc:
        flash_error(request, "La maintenance programmée n'a pas pu être ajoutée",
                    code="SRV-07", details=str(exc))
    return RedirectResponse("/", status_code=303)


@router.post("/actions/scheduled-maintenance/remove")
def action_scheduled_maintenance_remove(
    request: Request,
    entry_id: str = Form(...),
    csrf_token: str = Form(...),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.remove_scheduled_maintenance(user, entry_id.strip())
        request.session["flash"] = "Maintenance programmée retirée."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "La maintenance programmée n'a pas pu être retirée",
                    code="SRV-07", details=str(exc))
    return RedirectResponse("/", status_code=303)


@router.post("/actions/maintenance/cancel")
def action_cancel_maintenance(request: Request, csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.cancel_pending_maintenance(user)
        request.session["flash"] = "Fermeture annulée : le serveur reste ouvert."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "L'annulation a échoué", code="SRV-07", details=str(exc))
    return RedirectResponse("/", status_code=303)


@router.post("/actions/maintenance/end")
def action_exit_maintenance(request: Request, csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.exit_maintenance(user)
        request.session["flash"] = "Maintenance terminée : le serveur redémarre."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        # Cas le plus utile à expliquer : le portier tient encore l'adresse,
        # redémarrer maintenant échouerait sur un conflit d'IP.
        flash_error(request, "La réouverture a échoué", code="SRV-08", details=str(exc))
    return RedirectResponse("/", status_code=303)


# --- mise à jour contrôlée (owner : UPDATE, garde-fous dans le service) ---

@router.post("/actions/update")
def action_update(request: Request, csrf_token: str = Form(...), force: str = Form("")):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.apply_update(user, force=(force == "yes"))
        request.session["flash"] = "Mise à jour lancée : le serveur va être recréé dans quelques instants."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "La mise à jour a été refusée", code="SRV-06", details=str(exc))
    return RedirectResponse("/", status_code=303)



# --- réglages de jeu (barrière GAME_SETTINGS, appliquée par le service) ---

def _game_action(request: Request, csrf_token: str, apply, redirect_to: str = "/"):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        apply(request.app.state.service, user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Le réglage de jeu n'a pas pu être appliqué", code="JEU-01", details=str(exc))
        return RedirectResponse(redirect_to, status_code=303)
    request.session["flash"] = "Réglage appliqué."
    return RedirectResponse(redirect_to, status_code=303)


@router.post("/actions/game/difficulty")
def action_game_difficulty(request: Request, level: str = Form(...), csrf_token: str = Form(...)):
    return _game_action(request, csrf_token, lambda svc, u: svc.set_difficulty(u, level))


@router.post("/actions/game/gamerule")
def action_game_gamerule(
    request: Request,
    name: str = Form(...),
    value: str = Form(...),
    redirect_to: str = Form("/"),
    csrf_token: str = Form(...),
):
    target = _redirect_target(redirect_to, "/")
    return _game_action(request, csrf_token,
                        lambda svc, u: svc.set_gamerule(u, name, value), target)


@router.get("/servers", response_class=HTMLResponse)
def servers_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    svc = request.app.state.service
    try:
        ctx = _nav_context(request, user, "servers")
        ctx["servers"] = svc.servers_list(user)
        ctx["checks"] = svc.connection_checks(user)
        ctx["detected"] = svc.discover_servers(user)
        ctx["active_container"] = svc.active_server_container(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    ctx["checks_ok"] = sum(1 for c in ctx["checks"] if c.ok)
    return templates.TemplateResponse(request, "servers.html", ctx)


@router.post("/actions/servers/{server_id}/address")
def action_server_address(request: Request, server_id: str,
                          address: str = Form(""), csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.set_server_address(user, server_id, address)
        request.session["flash"] = ("Adresse de connexion enregistrée — visible sur le tableau de bord."
                                    if address.strip() else "Adresse de connexion effacée.")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "L'adresse n'a pas pu être enregistrée", code="SRV-07", details=str(exc))
    return RedirectResponse("/servers", status_code=303)


@router.get("/map")
def map_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    ctx = _nav_context(request, user, "map")
    if not ctx["map_url"] and not user.can(Permission.SERVER_MANAGE):
        # Pas de carte configurée : seul l'owner voit l'assistant.
        return RedirectResponse("/", status_code=303)
    svc = request.app.state.service
    if user.can(Permission.SERVER_MANAGE):
        servers = svc.servers_list(user)
        first = servers[0] if servers else None
        ctx["map_server_id"] = first.id if first else ""
        ctx["map_suggested"] = (f"http://{first.rcon_host}:8100"
                                if first and first.rcon_host else "http://minecraft:8100")
        ctx["map_draft"] = request.query_params.get("draft", "")
        try:
            mods = svc.mods_list(user)
            ctx["map_mod_detected"] = next(
                (m.name or m.filename for m in mods
                 if any(k in (m.name or m.filename).lower() for k in ("bluemap", "dynmap", "squaremap"))),
                "")
        except DomainError:
            ctx["map_mod_detected"] = ""
    return templates.TemplateResponse(request, "map.html", ctx)


@router.get("/map/embed")
@router.get("/map/embed/{path:path}")
def map_embed(request: Request, path: str = ""):
    """Relais borné de la carte : le navigateur ne voit que l'origine de
    mc-admin (HTTPS partout, adresse d'origine jamais exposée — anti-SSRF
    et normalisation du chemin dans adapters/map_fetch)."""
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        base = request.app.state.service.server_map_url(user)  # barrière STATUS
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not base:
        raise HTTPException(status_code=404, detail="aucune carte configurée")
    try:
        status, headers, chunks = request.app.state.map_open(
            base, path, request.url.query,
            if_none_match=request.headers.get("if-none-match", ""),
            if_modified_since=request.headers.get("if-modified-since", ""),
            accept_encoding=request.headers.get("accept-encoding", ""))
    except MapUnreachable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    media_type = headers.pop("Content-Type", "application/octet-stream")
    return StreamingResponse(chunks, status_code=status, media_type=media_type, headers=headers)


@router.post("/actions/map/test")
def action_map_test(request: Request, map_url: str = Form(""), csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        ok, detail = request.app.state.service.test_map_url(user, map_url)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    request.session["flash"] = (f"Test réussi : {detail} — tu peux activer la carte."
                                if ok else f"Test : {detail}")
    return RedirectResponse(f"/map?draft={urllib.parse.quote(map_url.strip())}", status_code=303)


@router.post("/actions/map")
def action_map_save(request: Request, server_id: str = Form(...),
                    map_url: str = Form(""), csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.set_server_map_url(user, server_id, map_url)
        request.session["flash"] = ("Carte activée — visible par tous les comptes."
                                    if map_url.strip() else "Carte désactivée.")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "La carte n'a pas pu être enregistrée", code="SRV-09", details=str(exc))
    return RedirectResponse("/map", status_code=303)


@router.post("/actions/servers/add")
def action_server_add(
    request: Request,
    name: str = Form(...),
    container: str = Form(...),
    rcon_host: str = Form(...),
    rcon_port: str = Form("25575"),
    world_dir: str = Form("world"),
    csrf_token: str = Form(...),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        was_empty = not request.app.state.service.servers_list(user)
        entry = request.app.state.service.add_server(
            user, name, container, rcon_host, int(rcon_port), world_dir)
        if was_empty:
            request.session["flash"] = (
                f"« {entry.name} » enregistré — redémarre mc-admin pour le prendre en main."
            )
        else:
            request.session["flash"] = (
                f"« {entry.name} » enregistré — le pilotage de plusieurs serveurs arrive bientôt."
            )
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DomainError, ValueError) as exc:
        flash_error(request, "Le serveur n'a pas pu être ajouté", code="SRV-08", details=str(exc))
    return RedirectResponse("/servers", status_code=303)


@router.get("/mods", response_class=HTMLResponse)
def mods_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    ctx = _nav_context(request, user, "mods")
    try:
        ctx["mods"] = request.app.state.service.mods_list(user)
        ctx["mod_updates"], ctx["mods_checked_at"] = request.app.state.service.mod_updates(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    ctx["mods_total_bytes"] = sum(m.size_bytes for m in ctx["mods"])
    return templates.TemplateResponse(request, "mods.html", ctx)


@router.get("/game", response_class=HTMLResponse)
def game_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    ctx = _nav_context(request, user, "game")
    try:
        difficulty, gamerules = request.app.state.service.full_game_settings(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError:
        difficulty, gamerules = None, None
    from api.gamerule_help import GAMERULE_HELP
    ctx["difficulty"] = difficulty
    ctx["gamerules"] = gamerules
    ctx["rule_help"] = GAMERULE_HELP
    return templates.TemplateResponse(request, "game.html", ctx)


@router.get("/fragments/vitals", response_class=HTMLResponse)
def vitals_fragment(request: Request):
    """Signes vitaux pollés par le tableau de bord (UI-3) : pastille + uptime
    + Infrastructure. Léger et sans état — mêmes sources que la page."""
    user = _current(request)
    if user is None:
        raise HTTPException(status_code=401)
    svc = request.app.state.service
    try:
        status = svc.get_status(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError:
        status = None
    uptime = None
    if status is not None and status.container.running:
        uptime = _humanize_uptime(status.container.started_at)
    show_infra = user.can(Permission.SERVER_MANAGE)
    infra = None
    if show_infra:
        try:
            infra = svc.infra_status(user)
            infra = infra if len(infra) > 1 else None
        except DomainError:
            pass
    # Version du jeu : elle DISPARAÎT pendant un redémarrage (mc-monitor n'a
    # plus de mesure) et ne revenait qu'au rechargement manuel de la page
    # (constaté par Jeremy le 31/07). Le check est mis en cache 60 s côté
    # adapter : le poller ne martèle donc ni Prometheus ni Mojang.
    update = None
    if user.can(Permission.UPDATE):
        try:
            update = svc.update_status(user)
        except DomainError:
            update = None
    return templates.TemplateResponse(
        request, "partials/vitals.html",
        {
            "status": status,
            "game_state": _game_state(status),
            "uptime": uptime,
            "infra": infra,
            "show_runtime_health": show_infra,
            "update": update,
            "can_update": user.can(Permission.UPDATE),
        },
    )


@router.get("/watch", response_class=HTMLResponse)
def watch_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    svc = request.app.state.service
    try:
        watched = svc.watched_containers(user)
        candidates = svc.watch_candidates(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    ctx = _nav_context(request, user, "watch")
    states = {st.name: st for st in svc.infra_status(user)}
    ctx["watched"] = [(name, states.get(name)) for name in watched]
    ctx["server_state"] = next(iter(states.values()), None)
    ctx["candidates"] = candidates
    return templates.TemplateResponse(request, "watch.html", ctx)


@router.post("/actions/watch/add")
def action_watch_add(request: Request, name: str = Form(...), csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.watch_container(user, name)
        request.session["flash"] = f"« {name} » est maintenant surveillé (effectif sous ~30 s)."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Le compagnon n'a pas pu être surveillé", code="SUR-01", details=str(exc))
    return RedirectResponse("/watch", status_code=303)


@router.post("/actions/watch/remove")
def action_watch_remove(request: Request, name: str = Form(...), csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.unwatch_container(user, name)
        request.session["flash"] = f"« {name} » n'est plus surveillé."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "La surveillance n'a pas pu être retirée", code="SUR-02", details=str(exc))
    return RedirectResponse("/watch", status_code=303)


@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        channels = request.app.state.service.notification_channels(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    ctx = _nav_context(request, user, "notifications")
    ctx["channels"] = channels
    from domain.services import NOTIFICATION_CHANNEL_TYPES
    ctx["channel_types"] = list(NOTIFICATION_CHANNEL_TYPES)
    ctx["event_labels"] = EVENT_LABELS
    return templates.TemplateResponse(request, "notifications.html", ctx)


# Libellés FR des filtres d'événements (ordre d'affichage sur les cartes).
EVENT_LABELS: list[tuple[str, str]] = [
    ("player", "Joueurs (arrivées/départs)"),
    ("new_player", "Nouveau joueur (première connexion)"),
    ("moderation", "Modération (kick, ban, op)"),
    ("backup", "Sauvegardes réussies"),
    ("restart", "Redémarrages effectués"),
    ("update", "Mises à jour du serveur de jeu"),
    ("app_update", "Mises à jour de mc-admin (l'application)"),
    ("health", "Pannes de conteneurs"),
    ("performance", "Lag serveur (MSPT soutenu au-dessus du seuil réglé)"),
    ("disk", "Espace disque bas (volume des sauvegardes)"),
    ("restore", "Restaurations"),
]


async def _events_from_request(request: Request) -> dict:
    """Les 8 interrupteurs depuis le formulaire (checkbox absente = False)."""
    from domain.services import EVENT_KEYS
    form = await request.form()
    return {key: bool(form.get(key)) for key in EVENT_KEYS}


def _send_channel_test(svc, user, channel: dict) -> str:
    """Envoie le message de test sur UN canal et renvoie le flash honnête."""
    from adapters.notifications import build_channel
    target = build_channel(channel["type"], channel["config"])
    if target is None:
        return f"Échec : canal « {channel['label']} » incomplet."
    try:
        target.notify("Test mc-admin", f"Canal « {channel['label']} » opérationnel 🎉", "info")
        svc.record_notification_test(user, channel["id"], ok=True)
        return f"Message de test envoyé sur « {channel['label']} » — vérifie qu'il est arrivé."
    except Exception as exc:  # noqa: BLE001 — l'échec du test EST l'information
        svc.record_notification_test(user, channel["id"], ok=False)
        return f"Échec du test « {channel['label']} » : {_explain_channel_error(exc)}"


def _explain_channel_error(exc: Exception) -> str:
    """Traduit les erreurs des API Discord/Telegram en correctif actionnable
    (vécu Jeremy 17/07 : « can't send messages to the bot » = chat_id qui
    pointe le bot lui-même)."""
    import urllib.error
    if isinstance(exc, urllib.error.HTTPError):
        body = ""
        try:
            body = exc.read()[:300].decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        if "can't send messages to the bot" in body:
            return ("le chat ID renseigné est celui du bot lui-même. Il faut TON identifiant "
                    "(un nombre) : envoie /start au bot, puis récupère ton ID via @userinfobot.")
        if "chat not found" in body:
            return ("chat ID introuvable : envoie d'abord /start au bot (obligatoire pour "
                    "qu'il puisse t'écrire), puis vérifie l'ID via @userinfobot.")
        if exc.code in (401, 404):
            return "jeton ou webhook invalide (l'API répond " + str(exc.code) + ")."
        return f"HTTP {exc.code} — {body[:120]}"
    return str(exc)


@router.post("/actions/notify-channels")
async def action_channel_add(
    request: Request,
    type: str = Form(...),
    label: str = Form(""),
    webhook_url: str = Form(""),
    bot_token: str = Form(""),
    chat_id: str = Form(""),
    csrf_token: str = Form(...),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    svc = request.app.state.service
    try:
        channel_id = svc.add_notification_channel(
            user, type, label,
            {"webhook_url": webhook_url, "bot_token": bot_token, "chat_id": chat_id},
            await _events_from_request(request))
        channel = next(c for c in svc.notification_channels(user) if c["id"] == channel_id)
        # « Créer et tester » : le canal reçoit immédiatement son test.
        request.session["flash"] = f"Canal créé. {_send_channel_test(svc, user, channel)}"
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Le canal de notification n'a pas pu être créé", code="NOT-01", details=str(exc))
    return RedirectResponse("/notifications", status_code=303)


@router.post("/actions/notify-channels/detect-chats")
def action_detect_chats(request: Request, bot_token: str = Form(""), csrf_token: str = Form(...)):
    """Assistant Telegram : liste les chats vus par le bot pour préremplir le
    chat ID. Le jeton vient du FORMULAIRE (pas encore stocké) et ne part
    jamais au journal."""
    user = _current(request)
    if user is None:
        raise HTTPException(status_code=401)
    _require_csrf(request, csrf_token)
    svc = request.app.state.service
    try:
        svc.notification_channels(user)  # barrière RBAC (SERVER_MANAGE)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    from fastapi.responses import JSONResponse
    bot_token = bot_token.strip()
    if not bot_token:
        return JSONResponse({"error": "Renseigne d'abord le jeton du bot."})
    import urllib.error
    from adapters.notifications import telegram_detect_chats
    try:
        chats = telegram_detect_chats(bot_token)
    except urllib.error.HTTPError as exc:
        msg = ("jeton invalide (l'API répond 401)." if exc.code in (401, 404)
               else f"l'API Telegram répond {exc.code}.")
        return JSONResponse({"error": f"Échec : {msg}"})
    except Exception:  # noqa: BLE001 — réseau : message honnête, pas de 500
        return JSONResponse({"error": "Échec : Telegram injoignable depuis le serveur."})
    if not chats:
        return JSONResponse({"error": "Aucun chat vu par ce bot — envoie /start au bot "
                                      "(ou ajoute-le au groupe) puis re-clique."})
    return JSONResponse({"chats": chats})


@router.post("/actions/notify-channels/{channel_id}/update")
async def action_channel_update(
    request: Request,
    channel_id: str,
    label: str = Form(""),
    webhook_url: str = Form(""),
    bot_token: str = Form(""),
    chat_id: str = Form(""),
    csrf_token: str = Form(...),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.update_notification_channel(
            user, channel_id, label=label,
            config={"webhook_url": webhook_url, "bot_token": bot_token, "chat_id": chat_id},
            events=await _events_from_request(request))
        request.session["flash"] = "Canal mis à jour — effectif au prochain envoi."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Le canal n'a pas pu être modifié", code="NOT-02", details=str(exc))
    return RedirectResponse("/notifications", status_code=303)


@router.post("/actions/notify-channels/{channel_id}/event")
def action_channel_event(request: Request, channel_id: str,
                         event: str = Form(...), value: str = Form(""),
                         csrf_token: str = Form(...)):
    """Bascule d'UN interrupteur directement sur la carte (switch inline)."""
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    from domain.services import EVENT_KEYS
    svc = request.app.state.service
    try:
        channel = next((c for c in svc.notification_channels(user) if c["id"] == channel_id), None)
        if channel is None or event not in EVENT_KEYS:
            request.session["flash"] = "Échec : canal ou événement inconnu."
        else:
            events = dict(channel["events"])
            events[event] = bool(value)
            svc.update_notification_channel(user, channel_id, events=events)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Le filtre d'événement n'a pas pu être basculé", code="NOT-03", details=str(exc))
    return RedirectResponse("/notifications", status_code=303)


@router.post("/actions/notify-channels/{channel_id}/toggle")
def action_channel_toggle(request: Request, channel_id: str,
                          enabled: str = Form(""), csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.update_notification_channel(
            user, channel_id, enabled=bool(enabled))
        request.session["flash"] = ("Canal réactivé." if enabled else "Canal suspendu.")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Le canal n'a pas pu être suspendu ou repris", code="NOT-04", details=str(exc))
    return RedirectResponse("/notifications", status_code=303)


@router.post("/actions/notify-channels/{channel_id}/delete")
def action_channel_delete(request: Request, channel_id: str, csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.remove_notification_channel(user, channel_id)
        request.session["flash"] = "Canal supprimé."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Le canal n'a pas pu être supprimé", code="NOT-05", details=str(exc))
    return RedirectResponse("/notifications", status_code=303)


@router.post("/actions/notify-channels/{channel_id}/test")
def action_channel_test(request: Request, channel_id: str, csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    svc = request.app.state.service
    try:
        channel = next((c for c in svc.notification_channels(user) if c["id"] == channel_id), None)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    request.session["flash"] = (_send_channel_test(svc, user, channel)
                                if channel else f"Échec : canal inconnu « {channel_id} ».")
    return RedirectResponse("/notifications", status_code=303)


@router.get("/performance", response_class=HTMLResponse)
def performance_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    ctx = _nav_context(request, user, "performance")
    range_key = request.query_params.get("range", "24h")
    hours = _PERF_RANGES.get(range_key, 24)
    if hours == 24:
        range_key = "24h"  # clé inconnue re-normalisée (le service borne aussi)
    try:
        perf = request.app.state.service.performance_snapshot(user, hours)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError:
        perf = None  # Prometheus injoignable -> page « indisponible »
    ctx["perf"] = perf
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    events = request.app.state.service.performance_events(user, since)
    thresholds = request.app.state.service.alert_thresholds(user)
    ctx["alert_thresholds"] = thresholds
    ctx["mspt_chart"] = (
        _mspt_chart(perf.mspt_history, hours, events=events, threshold_ms=thresholds.mspt_threshold_ms)
        if perf else None
    )
    ctx["perf_range"] = range_key
    ctx["perf_ranges"] = _PERF_RANGE_CHOICES
    ctx["can_spark"] = user.can(Permission.RCON_RAW)
    ctx["can_manage_alerts"] = user.can(Permission.SERVER_MANAGE)
    ctx["spark_profiles"] = (request.app.state.service.spark_profiles(user)
                             if ctx["can_spark"] else [])
    ctx["profiling_seconds"] = request.query_params.get("profiling", "")
    return templates.TemplateResponse(request, "performance.html", ctx)


_INCIDENT_KIND_LABELS = {
    "availability": "Indisponibilité",
    "performance": "Ralentissement",
    "disk": "Espace disque",
}
_ACTION_LABELS = {
    "restart": "Redémarrage",
    "restore": "Restauration",
    "backup": "Sauvegarde",
    "update": "Mise à jour",
    "maintenance": "Maintenance",
}


def _format_incident_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    rem = minutes % 60
    if hours < 24:
        return f"{hours} h {rem:02d}" if rem else f"{hours} h"
    days = hours // 24
    return f"{days} j {hours % 24} h"


@router.get("/incidents", response_class=HTMLResponse)
def incidents_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        raw = request.app.state.service.incidents(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    ctx = _nav_context(request, user, "incidents")
    now = datetime.now(timezone.utc)
    rows = []
    for incident, actions in raw:
        end = incident.ended_at or now
        try:
            duration = (end - incident.started_at).total_seconds()
        except TypeError:
            duration = 0
        rows.append({
            "label": incident.label or incident.subject,
            "kind": _INCIDENT_KIND_LABELS.get(incident.kind, incident.kind),
            "kind_key": incident.kind,
            "started": incident.started_at.astimezone().strftime("%d/%m %H:%M"),
            "ongoing": incident.ended_at is None,
            "duration": _format_incident_duration(duration),
            "detail": incident.detail,
            "actions": [
                {"when": when.astimezone().strftime("%H:%M"),
                 "label": _ACTION_LABELS.get(kind, kind)}
                for when, kind in actions
            ],
        })
    ctx["incidents"] = rows
    return templates.TemplateResponse(request, "incidents.html", ctx)


@router.post("/actions/performance/thresholds")
def action_set_alert_thresholds(
    request: Request,
    mspt_threshold_ms: float = Form(...),
    disk_min_free_gib: float = Form(...),
    mspt_sustained_minutes: float = Form(...),
    csrf_token: str = Form(...),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.set_alert_thresholds(
            user, mspt_threshold_ms, disk_min_free_gib, mspt_sustained_minutes)
        request.session["flash"] = "Seuils d'alerte mis à jour."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Les seuils n'ont pas pu être enregistrés", code="PRF-03", details=str(exc))
    return RedirectResponse("/performance", status_code=303)


@router.post("/actions/performance/profile")
def action_spark_start(request: Request, seconds: int = Form(30), csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.start_spark_profile(user, seconds)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Le profil spark n'a pas pu démarrer", code="PRF-01", details=str(exc))
        return RedirectResponse("/performance", status_code=303)
    # ?profiling=N : la page arme le compte à rebours puis déclenche le stop.
    return RedirectResponse(f"/performance?profiling={seconds}", status_code=303)


@router.post("/actions/performance/profile/stop")
def action_spark_stop(request: Request, csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.stop_spark_profile(user)
        request.session["flash"] = "Profil sauvegardé — télécharge-le puis glisse-le sur spark.lucko.me."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Le profil spark n'a pas pu être arrêté", code="PRF-02", details=str(exc))
    return RedirectResponse("/performance", status_code=303)


@router.get("/performance/spark/{filename}/download")
def spark_download(request: Request, filename: str):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        path = request.app.state.service.spark_profile_path(user, filename)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=filename, media_type="application/octet-stream")


_WORLD_LABELS = {"overworld": "Surface", "the_nether": "Nether", "the_end": "End"}


# Plages du graphe MSPT (clé URL -> heures) et libellés partagés route/template.
_PERF_RANGES = {"24h": 24, "3j": 72, "7j": 168}
_PERF_RANGE_CHOICES = (("24h", "24 h"), ("3j", "3 jours"), ("7j", "7 jours"))
_PERF_TICK_FORMATS = {24: "%H:%M", 72: "%d/%m %Hh", 168: "%d/%m"}
_PERF_WINDOW_LABELS = {24: "15 min", 72: "30 min", 168: "1 h"}


_PERF_EVENT_LABELS = {"backup": "Sauvegarde", "restart": "Redémarrage", "update": "Mise à jour"}


def _mspt_chart(history, hours=24, width=600.0, height=140.0, pad=6.0, events=(),
                threshold_ms=50.0):
    """Points SVG du graphe MSPT + ordonnée du seuil réglable (échelle
    commune — backlog fiabilité n° 4). Contrairement aux sparklines (échelle
    min-max), l'échelle inclut TOUJOURS 0 et le seuil : une ligne plate à
    2 ms doit se voir loin du danger. Les repères horaires sont rendus SOUS
    le svg (preserveAspectRatio=none déformerait du texte embarqué).

    `events` (sauvegardes/redémarrages/màj, cf. performance_events) sont
    replacés sur le MÊME axe temporel que les points (`now` calculé une
    seule fois, ci-dessous) — sans ça, une petite dérive entre l'instant du
    scan d'audit et celui du rendu déplacerait le marqueur du mauvais côté
    d'un pic MSPT."""
    if not history or len(history) < 2:
        return None
    top = max(max(history), threshold_ms * 1.1)
    span = width - 2 * pad
    step = span / (len(history) - 1)

    def y(value):
        return round(height - pad - (value / top) * (height - 2 * pad), 1)

    now = datetime.now().astimezone()
    fmt = _PERF_TICK_FORMATS.get(hours, "%H:%M")
    ticks = [(now - timedelta(hours=hours * (1.0 - f))).strftime(fmt)
             for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
    points = " ".join(f"{pad + i * step:.1f},{y(v)}" for i, v in enumerate(history))

    since = now - timedelta(hours=hours)
    markers = []
    for timestamp, kind in events:
        at = timestamp.astimezone()
        if at < since or at > now:
            continue
        frac = (at - since).total_seconds() / (hours * 3600)
        markers.append({
            "x": round(pad + frac * span, 1),
            "kind": kind,
            "label": _PERF_EVENT_LABELS.get(kind, kind),
            "at": at.strftime("%d/%m %H:%M"),
        })

    return {
        "points": points,
        "threshold_y": y(threshold_ms),
        "threshold_ms": threshold_ms,
        "max": round(max(history), 1),
        "over": any(v > threshold_ms for v in history),
        "ticks": ticks,
        "window": _PERF_WINDOW_LABELS.get(hours, "15 min"),
        "markers": markers,
    }


templates.env.globals["world_label"] = lambda w: _WORLD_LABELS.get(w, w)


@router.post("/actions/game/weather")
def action_game_weather(request: Request, kind: str = Form(...), csrf_token: str = Form(...)):
    return _game_action(request, csrf_token, lambda svc, u: svc.set_weather(u, kind))


@router.post("/actions/game/time")
def action_game_time(request: Request, moment: str = Form(...), csrf_token: str = Form(...)):
    return _game_action(request, csrf_token, lambda svc, u: svc.set_time(u, moment))
