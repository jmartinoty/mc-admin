"""Helpers partagés des routes (adapter entrant / driving).

La RBAC autoritative vit dans AdminService (qui audite refus/succès) ; ici on
ne fait que : authentifier, valider le CSRF, appeler le service, et masquer
les boutons selon le rôle (UX uniquement).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from api.activity import activity_payload
from api.security import csrf_ok, new_csrf_token
from config import APP_VERSION
from domain.model import Permission

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION


def spark_points(history, width: float = 100.0, height: float = 28.0, pad: float = 2.0) -> str:
    """Convertit une série (floats) en points de <polyline> SVG normalisés.

    Présentation pure : le domaine fournit les valeurs (MetricReading.history),
    la géométrie SVG reste dans la couche vue. Série plate = ligne médiane."""
    values = list(history or ())
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    step = (width - 2 * pad) / (len(values) - 1)
    if hi == lo:  # série plate : ligne médiane (plutôt que collée en bas)
        return " ".join(f"{pad + i * step:.1f},{height / 2:.1f}" for i in range(len(values)))
    span = hi - lo
    return " ".join(
        f"{pad + i * step:.1f},{height - pad - (v - lo) / span * (height - 2 * pad):.1f}"
        for i, v in enumerate(values)
    )


templates.env.globals["spark_points"] = spark_points


def flash_error(request: Request, message: str, *, code: str = "", details: str = "") -> None:
    """Flash d'erreur structuré (retour Jeremy 18/07/2026) : message court en
    langage utilisateur + code stable pour en parler + détails techniques
    complets relégués dans un dialogue « Détails » (copiables, jamais dans le
    toast). Un message trop long bascule de lui-même dans les détails."""
    text = " ".join(str(message).split())
    if len(text) > 140:
        details = text if not details else f"{text}\n\n{details}"
        text = text[:137].rsplit(" ", 1)[0] + "…"
    request.session["flash"] = {
        "error": True,
        "text": text,
        "code": code,
        "details": details.strip(),
    }


# --- helpers d'accès à l'état applicatif (peuplé dans create_app) ---

def _client_ip(request: Request) -> str:
    """IP observable du client, en passant par le résolveur de confiance
    (même barrière anti-spoofing que l'anti-bruteforce)."""
    resolver = getattr(request.app.state, "client_ip_resolver", None)
    peer_host = request.client.host if request.client is not None else None
    if resolver is None:
        return (peer_host or "").strip()
    return resolver.resolve(peer_host, request.headers.get("x-forwarded-for"))


def _register_session(request: Request, username: str) -> None:
    """Ouvre une session côté serveur et pose son `sid` dans le cookie signé.
    Appelé au login et au setup — c'est ce qui rend la session révocable."""
    registry = getattr(request.app.state, "sessions", None)
    if registry is None:
        return
    sid = registry.register(
        username, _client_ip(request), request.headers.get("user-agent", "")
    )
    request.session["sid"] = sid


def _current(request: Request):
    username = request.session.get("user")
    if not username:
        return None
    user = request.app.state.users.get(username)
    if user is None:
        return None
    registry = getattr(request.app.state, "sessions", None)
    if registry is None:
        return user
    sid = request.session.get("sid")
    if sid:
        # Session enrôlée : si son sid a été révoqué (ou a expiré), la session
        # n'est plus valide — on nettoie le cookie et on déconnecte.
        if not registry.is_valid(sid, username):
            request.session.clear()
            return None
        registry.touch(sid)
    else:
        # Cookie hérité (émis avant cette fonctionnalité) : on l'enrôle À LA
        # VOLÉE pour qu'il apparaisse et devienne révocable, sans forcer de
        # reconnexion. Le cookie étant signé, un sid ne peut pas être retiré
        # pour contourner la révocation.
        _register_session(request, username)
    return user


def _csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = new_csrf_token()
        request.session["csrf"] = token
    return token


def _require_csrf(request: Request, token: str) -> None:
    if not csrf_ok(request.session.get("csrf"), token):
        raise HTTPException(status_code=400, detail="Jeton CSRF invalide")


def _redirect_target(value: str, default: str) -> str:
    allowed = {"/", "/players", "/whitelist", "/op", "/bans", "/backups", "/audit", "/game", "/sessions"}
    return value if value in allowed else default


def _nav_context(request: Request, user, active: str) -> dict:
    """Contexte commun au shell (sidebar) : présent sur CHAQUE page rendue
    (pas le fragment pollé, qui vit à l'intérieur d'une page déjà rendue)."""
    page_titles = {
        "status": "Tableau de bord",
        "console": "Console RCON",
        "performance": "Performances",
        "map": "Carte",
        "players": "Joueurs",
        "whitelist": "Whitelist",
        "op": "Opérateurs",
        "bans": "Bannissements",
        "game": "Jeu",
        "mods": "Mods",
        "backups": "Sauvegardes",
        "audit": "Journal",
        "notifications": "Notifications",
        "watch": "Surveillance",
        "servers": "Serveurs",
        "users": "Comptes",
        "sessions": "Appareils connectés",
        "security": "Sécurité",
        "api_tokens": "Jetons d'API",
    }
    activity_enabled = (
        user.can(Permission.BACKUP_TRIGGER)
        or user.can(Permission.RESTART)
    )
    return {
        "user": user,
        "display_name": request.app.state.display_names.get(user.username) or user.username,
        "current_path": request.url.path,
        "csrf_token": _csrf(request),
        "flash": request.session.pop("flash", None),
        "active": active,
        "page_title": page_titles.get(active, "mc-admin"),
        "activity_enabled": activity_enabled,
        "activity": activity_payload(request, user) if activity_enabled else None,
        # L'entrée « Carte » n'apparaît que si une URL est configurée (page
        # Serveurs) — lecture par page rendue, servers.json est petit et local.
        "map_url": (request.app.state.service.server_map_url(user)
                    if user.can(Permission.STATUS) else ""),
        "can_players": user.can(Permission.STATUS),
        "can_whitelist": user.can(Permission.WHITELIST_MANAGE),
        "can_op": user.can(Permission.OP_MANAGE),
        "can_bans": user.can(Permission.BAN_MANAGE),
        "can_backups": user.can(Permission.BACKUP_TRIGGER),
        "can_backup_download": user.can(Permission.BACKUP_DOWNLOAD),
        "can_backup_retention": user.can(Permission.BACKUP_RETENTION),
        "can_game": user.can(Permission.GAME_SETTINGS),
        "can_servers": user.can(Permission.SERVER_MANAGE),
        "can_users": user.can(Permission.USER_MANAGE),
        "can_console": user.can(Permission.RCON_RAW),
        "can_audit": user.can(Permission.AUDIT_VIEW),
    }
