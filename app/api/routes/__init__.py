"""Assemblage des routes : un routeur par domaine, montés ici.

`from api.routes import router` reste l'unique point d'entrée (cf. app.py).
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from api.routes import apitokens, audit, auth, backups, dashboard, players

router = APIRouter()


# --- santé (sans auth, pour le healthcheck du conteneur) ---

@router.get("/healthz")
def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


for sub in (auth.router, dashboard.router, players.router, backups.router, audit.router, apitokens.router):
    router.include_router(sub)
