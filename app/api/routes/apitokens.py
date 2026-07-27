"""Gestion des jetons d'API locale (owner) — création, liste, révocation.

Le stockage vit dans la couche transport (`api/api_tokens.py`, comme les
sessions), mais la barrière RBAC et l'audit passent par `AdminService`
(`authorize_api_tokens` / `record_api_token_change`) — un refus est audité
comme partout, une création/révocation laisse une trace (jamais le secret).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.routes.common import _current, _nav_context, _require_csrf, templates
from domain.errors import PermissionDenied

router = APIRouter()


def _forbidden_or_login(request: Request):
    user = _current(request)
    if user is None:
        return None, RedirectResponse("/login", status_code=303)
    try:
        request.app.state.service.authorize_api_tokens(user)
    except PermissionDenied:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Réservé à l'administrateur")
    return user, None


@router.get("/api-tokens", response_class=HTMLResponse)
def api_tokens_page(request: Request):
    user, redirect = _forbidden_or_login(request)
    if redirect is not None:
        return redirect
    store = request.app.state.api_tokens
    context = _nav_context(request, user, "api_tokens")
    context["tokens"] = [
        {
            "token_id": info.token_id,
            "label": info.label,
            "role": info.role,
            "created": datetime.fromtimestamp(info.created_at, tz=timezone.utc)
            .strftime("%Y-%m-%d") if info.created_at else "—",
        }
        for info in store.list()
    ]
    context["roles"] = sorted(request.app.state.roles)
    # Jeton fraîchement créé, affiché UNE seule fois (retiré de la session).
    context["new_token"] = request.session.pop("new_api_token", None)
    return templates.TemplateResponse(request, "api_tokens.html", context)


@router.post("/actions/api-tokens/create")
def api_token_create(
    request: Request,
    label: str = Form(...),
    role: str = Form(...),
    csrf_token: str = Form(...),
):
    user, redirect = _forbidden_or_login(request)
    if redirect is not None:
        return redirect
    _require_csrf(request, csrf_token)
    if role not in request.app.state.roles:
        request.session["flash"] = "Rôle inconnu."
        return RedirectResponse("/api-tokens", status_code=303)
    token_id, raw = request.app.state.api_tokens.create(label, role)
    request.app.state.service.record_api_token_change(
        user, f"phase=api_token_created token_id={token_id} role={role}"
    )
    # Le secret n'est montré qu'ici : on le passe à la page via la session.
    request.session["new_api_token"] = raw
    return RedirectResponse("/api-tokens", status_code=303)


@router.post("/actions/api-tokens/revoke")
def api_token_revoke(request: Request, token_id: str = Form(...), csrf_token: str = Form(...)):
    user, redirect = _forbidden_or_login(request)
    if redirect is not None:
        return redirect
    _require_csrf(request, csrf_token)
    if request.app.state.api_tokens.revoke(token_id):
        request.app.state.service.record_api_token_change(
            user, f"phase=api_token_revoked token_id={token_id}"
        )
        request.session["flash"] = "Jeton révoqué."
    return RedirectResponse("/api-tokens", status_code=303)
