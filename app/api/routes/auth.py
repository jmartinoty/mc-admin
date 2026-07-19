"""Routes d'authentification et de profil (self-service, hors RBAC)."""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.auth import authenticate
from api.routes.common import _csrf, _current, _redirect_target, _require_csrf, templates
from domain.errors import PermissionDenied
from domain.model import User

router = APIRouter()
logger = logging.getLogger("mc_admin")

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
_PERSISTENCE_ERRORS = (OSError, ValueError)


def _persist_new_account(request: Request, username: str, role: str, password: str) -> None:
    """Persiste le secret avant le compte, avec compensation si la suite échoue."""
    credentials = request.app.state.password_overrides
    credentials.set(username, request.app.state.hasher.hash(password))
    try:
        request.app.state.users_store.add(username, role)
    except Exception:
        try:
            credentials.remove(username)
        except Exception:
            logger.exception(
                "nettoyage impossible après l'échec de création du compte %s",
                username,
            )
        raise


def _log_persistence_error(action: str, exc: Exception) -> None:
    logger.error("%s : stockage local indisponible : %s", action, exc, exc_info=True)


# --- premier lancement (V6.2) : créer le compte administrateur dans l'UI ---

def _setup_needed(request: Request) -> bool:
    return not request.app.state.users


@router.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request):
    if not _setup_needed(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request, "setup.html", {"csrf_token": _csrf(request), "error": None}
    )


@router.post("/setup")
def setup_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    csrf_token: str = Form(...),
):
    _require_csrf(request, csrf_token)
    # Verrou : dès qu'UN compte existe, cette route est morte — aucune
    # possibilité de s'ajouter un compte sur une instance déjà configurée.
    if not _setup_needed(request):
        return RedirectResponse("/login", status_code=303)
    username = username.strip()
    error = None
    if not _USERNAME_RE.match(username):
        error = "Nom invalide : 3 à 32 caractères (lettres, chiffres, - et _)."
    elif len(password) < 8:
        error = "Mot de passe trop court (8 caractères minimum)."
    elif password != password_confirm:
        error = "Les deux mots de passe ne correspondent pas."
    if error:
        return templates.TemplateResponse(
            request, "setup.html", {"csrf_token": _csrf(request), "error": error},
            status_code=400,
        )
    owner_role = request.app.state.roles.get("owner")
    try:
        _persist_new_account(request, username, "owner", password)
    except _PERSISTENCE_ERRORS as exc:
        _log_persistence_error("création du premier compte", exc)
        return templates.TemplateResponse(
            request,
            "setup.html",
            {
                "csrf_token": _csrf(request),
                "error": (
                    "Impossible d'enregistrer le compte pour le moment. "
                    "Les données existantes ont été conservées."
                ),
            },
            status_code=503,
        )
    request.app.state.users[username] = User(username=username, role=owner_role)
    request.app.state.service.record_first_account(username)
    # Connexion directe : la personne vient de choisir ses identifiants, les
    # lui redemander serait une friction inutile (retour Jeremy).
    request.session["user"] = username
    request.session["flash"] = f"Bienvenue, {username} ! Branchons ton serveur."
    return RedirectResponse("/servers", status_code=303)


# --- gestion des comptes (owner, V6.4) ---

@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        request.app.state.service.authorize_user_management(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    from api.routes.common import _nav_context
    ctx = _nav_context(request, user, "users")
    dn = request.app.state.display_names
    ctx["accounts"] = sorted(
        ({
            "username": u.username,
            "display_name": dn.get(u.username) or u.username,
            "role": u.role.name,
            "is_me": u.username == user.username,
        } for u in request.app.state.users.values()),
        key=lambda a: a["username"].lower(),
    )
    ctx["roles"] = sorted(request.app.state.roles)
    return templates.TemplateResponse(request, "users.html", ctx)


@router.post("/actions/users/add")
def action_user_add(
    request: Request,
    username: str = Form(...),
    role: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.authorize_user_management(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    username = username.strip()
    role_obj = request.app.state.roles.get(role)
    if not _USERNAME_RE.match(username):
        request.session["flash"] = "Échec : nom invalide (3 à 32 caractères, lettres/chiffres/-/_)."
    elif username in request.app.state.users:
        request.session["flash"] = f"Échec : le compte « {username} » existe déjà."
    elif role_obj is None:
        request.session["flash"] = f"Échec : rôle inconnu « {role} »."
    elif len(password) < 8:
        request.session["flash"] = "Échec : mot de passe trop court (8 caractères minimum)."
    else:
        try:
            _persist_new_account(request, username, role, password)
        except _PERSISTENCE_ERRORS as exc:
            _log_persistence_error(f"création du compte {username}", exc)
            request.session["flash"] = (
                "Échec : stockage local indisponible. Aucun compte n'a été ajouté."
            )
        else:
            request.app.state.users[username] = User(username=username, role=role_obj)
            request.app.state.service.record_account_change(
                user, f"phase=account_created account={username} role={role}")
            request.session["flash"] = f"Compte « {username} » créé ({role})."
    return RedirectResponse("/users", status_code=303)


@router.post("/actions/users/reset-password")
def action_user_reset_password(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.authorize_user_management(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if username not in request.app.state.users:
        request.session["flash"] = f"Échec : compte inconnu « {username} »."
    elif len(password) < 8:
        request.session["flash"] = "Échec : mot de passe trop court (8 caractères minimum)."
    else:
        try:
            request.app.state.password_overrides.set(
                username,
                request.app.state.hasher.hash(password),
            )
        except _PERSISTENCE_ERRORS as exc:
            _log_persistence_error(f"réinitialisation du mot de passe de {username}", exc)
            request.session["flash"] = "Échec : le mot de passe n'a pas été modifié."
        else:
            request.app.state.service.record_account_change(
                user, f"phase=password_reset account={username}")
            request.session["flash"] = f"Mot de passe de « {username} » réinitialisé."
    return RedirectResponse("/users", status_code=303)


@router.post("/actions/users/role")
def action_user_role(
    request: Request,
    username: str = Form(...),
    role: str = Form(...),
    csrf_token: str = Form(...),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.authorize_user_management(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    role_obj = request.app.state.roles.get(role)
    # Se modifier soi-même est refusé : celui qui agit garde ainsi toujours
    # la gestion des comptes — impossible de scier la branche.
    if username == user.username:
        request.session["flash"] = "Échec : impossible de modifier son propre rôle."
    elif username not in request.app.state.users:
        request.session["flash"] = f"Échec : compte inconnu « {username} »."
    elif role_obj is None:
        request.session["flash"] = f"Échec : rôle inconnu « {role} »."
    else:
        try:
            request.app.state.users_store.add(username, role)
        except _PERSISTENCE_ERRORS as exc:
            _log_persistence_error(f"changement du rôle de {username}", exc)
            request.session["flash"] = "Échec : le rôle n'a pas été modifié."
        else:
            request.app.state.users[username] = User(username=username, role=role_obj)
            request.app.state.service.record_account_change(
                user, f"phase=role_changed account={username} role={role}")
            request.session["flash"] = f"« {username} » est maintenant {role}."
    return RedirectResponse("/users", status_code=303)


@router.post("/actions/users/delete")
def action_user_delete(
    request: Request,
    username: str = Form(...),
    csrf_token: str = Form(...),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.authorize_user_management(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if username == user.username:
        request.session["flash"] = "Échec : impossible de supprimer son propre compte."
    elif username not in request.app.state.users:
        request.session["flash"] = f"Échec : compte inconnu « {username} »."
    else:
        try:
            removed = request.app.state.users_store.remove(username)
        except _PERSISTENCE_ERRORS as exc:
            _log_persistence_error(f"suppression du compte {username}", exc)
            request.session["flash"] = "Échec : le compte n'a pas été supprimé."
        else:
            if not removed:
                request.session["flash"] = (
                    "Échec : le compte n'existe plus dans le stockage local."
                )
                return RedirectResponse("/users", status_code=303)
            request.app.state.users.pop(username, None)
            credentials_removed = True
            try:
                request.app.state.password_overrides.remove(username)
            except _PERSISTENCE_ERRORS as exc:
                credentials_removed = False
                _log_persistence_error(
                    f"nettoyage du mot de passe du compte supprimé {username}",
                    exc,
                )
            request.app.state.service.record_account_change(
                user,
                (
                    f"phase=account_deleted account={username} "
                    f"credentials_removed={str(credentials_removed).lower()}"
                ),
            )
            if credentials_removed:
                request.session["flash"] = f"Compte « {username} » supprimé."
            else:
                request.session["flash"] = (
                    f"Compte « {username} » supprimé, mais le nettoyage de son "
                    "ancien mot de passe doit être vérifié."
                )
    return RedirectResponse("/users", status_code=303)


# --- authentification ---

@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if _setup_needed(request):
        return RedirectResponse("/setup", status_code=303)
    if _current(request) is not None:
        return RedirectResponse("/", status_code=303)
    # Signature Starlette moderne : TemplateResponse(request, name, context).
    return templates.TemplateResponse(
        request, "login.html", {"csrf_token": _csrf(request), "error": None}
    )


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    _require_csrf(request, csrf_token)
    peer_host = request.client.host if request.client is not None else None
    client_ip = request.app.state.client_ip_resolver.resolve(
        peer_host,
        request.headers.get("x-forwarded-for"),
    )
    attempt = request.app.state.login_limiter.begin(client_ip, username)
    if attempt is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"csrf_token": _csrf(request), "error": "Trop de tentatives — réessaie dans quelques minutes."},
            status_code=429,
        )
    try:
        # V6.5 : le store /data est LA source des hash (roles.yml n'en porte plus).
        stored = request.app.state.password_overrides.get(username)
        user = authenticate(
            {username: stored} if stored else {},
            request.app.state.users,
            username,
            password,
            request.app.state.hasher,
            fallback_hash=request.app.state.login_dummy_hash,
        )
    except Exception:
        request.app.state.login_limiter.cancel(attempt)
        raise
    if user is None:
        request.app.state.login_limiter.failure(attempt)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"csrf_token": _csrf(request), "error": "Identifiants invalides"},
            status_code=401,
        )
    request.app.state.login_limiter.success(attempt)
    # Anti-fixation de session : on repart d'une session propre après login.
    request.session.clear()
    request.session["user"] = user.username
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    _require_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.post("/actions/display-name")
def action_display_name(
    request: Request,
    display_name: str = Form(""),
    csrf_token: str = Form(...),
    redirect_to: str = Form(""),
):
    """Change le nom affiché (préférence UI, per-user) — pas soumis à la RBAC :
    chacun modifie le sien. Le pseudo de connexion (clé RBAC + audit) est
    inchangé. Nom vide = retour au pseudo par défaut."""
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    clean = " ".join((display_name or "").split())[:32]  # trim + borne, pas de retour ligne
    request.app.state.display_names.set(user.username, clean)
    request.session["flash"] = "Nom affiché mis à jour." if clean else "Nom affiché réinitialisé."
    return RedirectResponse(_redirect_target(redirect_to, "/"), status_code=303)


@router.post("/actions/password")
def action_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    csrf_token: str = Form(...),
    redirect_to: str = Form(""),
):
    """Change SON propre mot de passe (self-service, pas de RBAC).
    Vérifie d'abord le mot de passe actuel."""
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    hasher = request.app.state.hasher
    current_hash = request.app.state.password_overrides.get(user.username)
    if not current_hash or not hasher.verify(current_hash, current_password):
        request.session["flash"] = "Mot de passe actuel incorrect."
    elif len(new_password) < 8:
        request.session["flash"] = "Le nouveau mot de passe doit faire au moins 8 caractères."
    else:
        try:
            request.app.state.password_overrides.set(
                user.username,
                hasher.hash(new_password),
            )
        except _PERSISTENCE_ERRORS as exc:
            _log_persistence_error(
                f"changement du mot de passe de {user.username}",
                exc,
            )
            request.session["flash"] = "Échec : le mot de passe n'a pas été modifié."
        else:
            request.session["flash"] = "Mot de passe mis à jour."
    return RedirectResponse(_redirect_target(redirect_to, "/"), status_code=303)
