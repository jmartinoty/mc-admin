"""Pages et actions joueurs : liste unifiée, détail, whitelist, op, bans."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.routes.common import _current, _nav_context, _redirect_target, _require_csrf, flash_error, templates
from domain.errors import DomainError, PermissionDenied
from domain.model import Permission, PlayerSummary

router = APIRouter()


def _merge_known_players(
    history: list[PlayerSummary],
    known_names: set[str],
    online_names: set[str],
) -> tuple[list[PlayerSummary], set[str]]:
    history_names = {p.player.lower() for p in history}
    by_name = {p.player.lower(): p for p in history}
    online_keys = {name.lower() for name in online_names}
    now = datetime.now(timezone.utc)

    for name in sorted(known_names | online_names):
        key = name.lower()
        if key not in by_name:
            by_name[key] = PlayerSummary(
                player=name,
                total_seconds=0.0,
                last_seen=now,
                online=key in online_keys,
            )
        elif key in online_keys and not by_name[key].online:
            current = by_name[key]
            by_name[key] = PlayerSummary(
                player=current.player,
                total_seconds=current.total_seconds,
                last_seen=now,
                online=True,
            )

    def sort_key(player: PlayerSummary):
        has_history = player.player in history_names
        last_seen = -player.last_seen.timestamp() if has_history or player.online else 0
        return (not player.online, not has_history, last_seen, player.player.lower())

    players = sorted(by_name.values(), key=sort_key)
    return players, history_names


def _session_name_set(request: Request, key: str) -> set[str]:
    values = request.session.get(key) or []
    return {str(v).strip() for v in values if str(v).strip()}


def _remember_session_name(request: Request, key: str, player: str) -> None:
    values = _session_name_set(request, key)
    values.add(player)
    request.session[key] = sorted(values, key=str.lower)


def _forget_session_name(request: Request, key: str, player: str) -> None:
    player_key = player.lower()
    values = {v for v in _session_name_set(request, key) if v.lower() != player_key}
    request.session[key] = sorted(values, key=str.lower)


def _player_detail_context(request: Request, user, player_name: str) -> dict:
    svc = request.app.state.service
    try:
        summary, sessions, stats = svc.player_detail(user, player_name)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    key = player_name.lower()
    try:
        status = svc.get_status(user)
        online_player = next(
            (player for player in status.players_online if player.name.lower() == key),
            None,
        )
        if status.players_available and online_player is not None:
            summary = PlayerSummary(
                player=summary.player if summary is not None else online_player.name,
                total_seconds=summary.total_seconds if summary is not None else 0.0,
                last_seen=datetime.now(timezone.utc),
                online=True,
            )
    except DomainError:
        pass

    ctx = _nav_context(request, user, "players")
    ctx.update({
        "player_name": player_name,
        "page_title": f"{player_name} · Joueurs",
        "summary": summary,
        "sessions": sessions,
        "stats": stats,
        "op_entry": None,
        "pending_op_level": None,
        "is_whitelisted": False,
        "is_banned": False,
        "can_kick": user.can(Permission.KICK),
        "can_bans": user.can(Permission.BAN_MANAGE),
        "can_whitelist": user.can(Permission.WHITELIST_MANAGE),
        "can_op": user.can(Permission.OP_MANAGE),
        "op_levels": _OP_LEVELS,
    })
    if ctx["can_op"]:
        try:
            ctx["op_entry"] = next((o for o in svc.ops(user) if o.name.lower() == key), None)
        except DomainError:
            pass
        try:
            ctx["pending_op_level"] = next(
                (
                    item
                    for item in svc.pending_op_levels(user)
                    if item.name.lower() == key
                ),
                None,
            )
        except DomainError:
            pass
    if ctx["can_whitelist"]:
        try:
            whitelist = set(svc.whitelist(user)) | _session_name_set(
                request,
                "whitelist_hints",
            )
            ctx["is_whitelisted"] = key in {name.lower() for name in whitelist}
        except DomainError:
            pass
    if ctx["can_bans"]:
        try:
            ctx["is_banned"] = key in {ban.player.lower() for ban in svc.bans(user)}
        except DomainError:
            pass
    ctx["player_actions"] = (
        (ctx["can_kick"] and bool(summary and summary.online))
        or ctx["can_bans"]
        or ctx["can_whitelist"]
        or ctx["can_op"]
    )
    return ctx


@router.get("/players", response_class=HTMLResponse)
def players_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    svc = request.app.state.service
    ctx = _nav_context(request, user, "players")
    can_kick = user.can(Permission.KICK)
    can_bans = user.can(Permission.BAN_MANAGE)
    can_whitelist = user.can(Permission.WHITELIST_MANAGE)
    can_op = user.can(Permission.OP_MANAGE)
    ctx.update({
        "can_kick": can_kick,
        "can_bans": can_bans,
        "can_whitelist": can_whitelist,
        "can_op": can_op,
        "player_actions": can_kick or can_bans or can_whitelist or can_op,
        "whitelist_names": None,
        "whitelist_keys": set(),
        "ops_by_name": None,
        "ops_by_key": None,
        "pending_op_levels": {},
        "banned_players": None,
        "banned_keys": set(),
        "pending_by_player": {},
        "history_names": set(),
        "op_levels": _OP_LEVELS,
    })
    try:
        players = svc.player_history(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    known_names: set[str] = set()
    online_names: set[str] = set()
    try:
        status = svc.get_status(user)
        if status.players_available:
            online_names = {p.name for p in status.players_online}
            known_names.update(online_names)
    except DomainError:
        pass
    if can_whitelist:
        try:
            ctx["whitelist_names"] = set(svc.whitelist(user)) | _session_name_set(request, "whitelist_hints")
            ctx["whitelist_keys"] = {name.lower() for name in ctx["whitelist_names"]}
            known_names.update(ctx["whitelist_names"])
        except DomainError:
            pass
    if can_op:
        try:
            ctx["ops_by_name"] = {entry.name: entry for entry in svc.ops(user)}
            ctx["ops_by_key"] = {entry.name.lower(): entry for entry in ctx["ops_by_name"].values()}
            known_names.update(ctx["ops_by_name"])
        except DomainError:
            pass
        try:
            pending_levels = svc.pending_op_levels(user)
            ctx["pending_op_levels"] = {
                item.name.lower(): item for item in pending_levels
            }
            known_names.update(item.name for item in pending_levels)
        except DomainError:
            pass
    if can_bans:
        try:
            bans = svc.bans(user)
            ctx["banned_players"] = {entry.player for entry in bans}
            ctx["banned_keys"] = {entry.player.lower() for entry in bans}
            known_names.update(ctx["banned_players"])
            pending = svc.pending_unbans(user)
            ctx["pending_by_player"] = {p.player: p.unban_at for p in pending}
        except DomainError:
            pass
    # Stats vanilla : temps de jeu total compté par le serveur (depuis la
    # création du monde) — et des joueurs parfois jamais vus par le tailer.
    stats_by_key = {}
    try:
        stats_by_key = {st.player.lower(): st for st in svc.player_stats(user)}
        known_names.update(st.player for st in stats_by_key.values())
    except DomainError:
        pass
    ctx["stats_by_key"] = stats_by_key
    ctx["players"], ctx["history_names"] = _merge_known_players(players, known_names, online_names)
    return templates.TemplateResponse(request, "players.html", ctx)


@router.get("/players/{player_name}", response_class=HTMLResponse)
def player_detail_page(request: Request, player_name: str):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    ctx = _player_detail_context(request, user, player_name)
    return templates.TemplateResponse(request, "player_detail.html", ctx)


@router.get("/players/{player_name}/panel", response_class=HTMLResponse)
def player_detail_panel(request: Request, player_name: str):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    ctx = _player_detail_context(request, user, player_name)
    ctx["panel_mode"] = True
    return templates.TemplateResponse(request, "partials/player_panel.html", ctx)


@router.get("/whitelist", response_class=HTMLResponse)
def whitelist_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    ctx = _nav_context(request, user, "whitelist")
    try:
        ctx["whitelist"] = request.app.state.service.whitelist(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError:
        ctx["whitelist"] = None  # RCON injoignable -> « indisponible »
    return templates.TemplateResponse(request, "whitelist.html", ctx)


# Niveaux vanilla (op-permission-level) : 4 = contrôle total (défaut du
# formulaire, comportement inchangé pour qui ne touche pas au sélecteur).
_OP_LEVELS = [
    (1, "1 – ignore la protection du spawn"),
    (2, "2 – triche/blocs de commande"),
    (3, "3 – kick/ban/whitelist"),
    (4, "4 – contrôle total"),
]


@router.get("/op", response_class=HTMLResponse)
def op_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    ctx = _nav_context(request, user, "op")
    ctx["op_levels"] = _OP_LEVELS
    ctx["pending_op_levels"] = {}
    try:
        ctx["ops"] = request.app.state.service.ops(user)
        ctx["pending_op_levels"] = {
            item.name.lower(): item
            for item in request.app.state.service.pending_op_levels(user)
        }
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError:
        ctx["ops"] = None  # dégradation propre (fichier illisible…)
    return templates.TemplateResponse(request, "op.html", ctx)


@router.get("/bans", response_class=HTMLResponse)
def bans_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    ctx = _nav_context(request, user, "bans")
    try:
        ctx["bans"] = request.app.state.service.bans(user)
        pending = request.app.state.service.pending_unbans(user)
        ctx["pending_by_player"] = {p.player: p.unban_at for p in pending}
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError:
        ctx["bans"] = None  # dégradation propre (fichier illisible…)
        ctx["pending_by_player"] = {}
    return templates.TemplateResponse(request, "bans.html", ctx)


@router.post("/actions/ban")
def action_ban(
    request: Request,
    player: str = Form(...),
    reason: str = Form(""),
    hours: str = Form(""),
    csrf_token: str = Form(...),
    redirect_to: str = Form(""),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        hours_value = float(hours) if hours.strip() else 0.0
        if hours_value > 0:
            request.app.state.service.ban_temporary(user, player.strip(), hours_value, reason)
            request.session["flash"] = f"Bannissement temporaire ({hours_value:g}h) : {player.strip()}"
        else:
            request.app.state.service.ban(user, player.strip(), reason)
            request.session["flash"] = f"Bannissement : {player.strip()}"
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DomainError, ValueError) as exc:
        flash_error(request, "Le bannissement a échoué", code="JOU-01", details=str(exc))
    return RedirectResponse(_redirect_target(redirect_to, "/bans"), status_code=303)


@router.post("/actions/pardon")
def action_pardon(
    request: Request,
    player: str = Form(...),
    csrf_token: str = Form(...),
    redirect_to: str = Form(""),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        result = request.app.state.service.pardon(user, player.strip())
        request.session["flash"] = result
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "La levée de ban a échoué", code="JOU-02", details=str(exc))
    return RedirectResponse(_redirect_target(redirect_to, "/bans"), status_code=303)


@router.post("/actions/kick")
def action_kick(
    request: Request,
    player: str = Form(...),
    reason: str = Form(""),
    csrf_token: str = Form(...),
    redirect_to: str = Form(""),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        result = request.app.state.service.kick(user, player.strip(), reason)
        request.session["flash"] = result
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "L'expulsion a échoué", code="JOU-03", details=str(exc))
    return RedirectResponse(_redirect_target(redirect_to, "/"), status_code=303)


# --- whitelist (owner : WHITELIST_MANAGE, appliqué par le service) ---

@router.post("/actions/whitelist/add")
def action_whitelist_add(
    request: Request,
    player: str = Form(...),
    csrf_token: str = Form(...),
    redirect_to: str = Form(""),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    player_name = player.strip()
    try:
        result = request.app.state.service.whitelist_add(user, player_name)
        _remember_session_name(request, "whitelist_hints", player_name)
        request.session["flash"] = f"Whitelist : {result}"
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        if "already whitelisted" in str(exc).lower():
            _remember_session_name(request, "whitelist_hints", player_name)
        flash_error(request, "L'ajout à la whitelist a échoué", code="JOU-04", details=str(exc))
    return RedirectResponse(_redirect_target(redirect_to, "/whitelist"), status_code=303)


@router.post("/actions/whitelist/remove")
def action_whitelist_remove(
    request: Request,
    player: str = Form(...),
    csrf_token: str = Form(...),
    redirect_to: str = Form(""),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    player_name = player.strip()
    try:
        result = request.app.state.service.whitelist_remove(user, player_name)
        _forget_session_name(request, "whitelist_hints", player_name)
        request.session["flash"] = f"Whitelist : {result}"
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Le retrait de la whitelist a échoué", code="JOU-05", details=str(exc))
    return RedirectResponse(_redirect_target(redirect_to, "/whitelist"), status_code=303)


# --- opérateurs (owner : OP_MANAGE, appliqué par le service) ---

@router.post("/actions/op/add")
def action_op_add(
    request: Request,
    player: str = Form(...),
    level: str = Form("4"),
    csrf_token: str = Form(...),
    redirect_to: str = Form(""),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        level_value = int(level)
        result = request.app.state.service.op_add(user, player.strip(), level_value)
        pending = next(
            (
                item
                for item in request.app.state.service.pending_op_levels(user)
                if item.name.lower() == player.strip().lower()
            ),
            None,
        )
        request.session["flash"] = (
            f"Opérateur activé immédiatement. Niveau {pending.level} "
            "prévu au prochain redémarrage."
            if pending is not None
            else f"Opérateurs : {result}"
        )
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DomainError, ValueError) as exc:
        flash_error(request, "Le passage opérateur a échoué", code="JOU-06", details=str(exc))
    return RedirectResponse(_redirect_target(redirect_to, "/op"), status_code=303)


@router.post("/actions/op/remove")
def action_op_remove(
    request: Request,
    player: str = Form(...),
    csrf_token: str = Form(...),
    redirect_to: str = Form(""),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        result = request.app.state.service.op_remove(user, player.strip())
        request.session["flash"] = f"Opérateurs : {result}"
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "Le retrait d'opérateur a échoué", code="JOU-07", details=str(exc))
    return RedirectResponse(_redirect_target(redirect_to, "/op"), status_code=303)
