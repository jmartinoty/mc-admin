"""API locale documentée (`/api/v1`) — intégration nas-dashboard, scripts.

Sous-application FastAPI MONTÉE sous `/api/v1`, avec sa PROPRE doc OpenAPI
(`/api/v1/docs`, `/api/v1/openapi.json`) : les routes navigateur (HTML) restent
hors du schéma, la doc ne décrit que l'API machine.

Authentification par jeton porteur (`Authorization: Bearer <token>`), résolu en
un `User` synthétique porteur du RÔLE du jeton : l'API réutilise EXACTEMENT la
RBAC et l'audit d'`AdminService` (aucune logique dupliquée, mêmes barrières que
l'UI). Le CSRF ne s'applique pas (il protège l'auth par cookie ambiant, pas un
porteur explicite). Endpoints en LECTURE SEULE pour l'instant (barrière STATUS).

L'état partagé (service, rôles, jetons) est posé sur `app.state` de CETTE
sous-app par `create_app` après construction — mêmes objets que l'app parente.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from domain.errors import DomainError, PermissionDenied
from domain.model import User


# ---- modèles de réponse (schéma OpenAPI) ----

class ContainerStateModel(BaseModel):
    name: str
    running: bool
    status: str
    health: str | None = None
    started_at: datetime | None = None


class StatusModel(BaseModel):
    online: bool
    players_available: bool
    players_online: list[str]
    container: ContainerStateModel


class PlayerModel(BaseModel):
    name: str
    online: bool
    total_seconds: float
    last_seen: datetime


class MetricModel(BaseModel):
    key: str
    label: str
    unit: str
    value: float | None


class ContainerHealthModel(BaseModel):
    name: str
    running: bool
    status: str
    health: str | None = None


def _bearer_user(request: Request) -> User:
    """Résout le jeton porteur en `User` (rôle du jeton). 401 si absent/inconnu,
    403 si le rôle référencé n'existe plus."""
    header = request.headers.get("authorization", "")
    scheme, _, raw = header.partition(" ")
    if scheme.lower() != "bearer" or not raw.strip():
        raise HTTPException(
            status_code=401,
            detail="Jeton d'API requis (Authorization: Bearer …).",
            headers={"WWW-Authenticate": "Bearer"},
        )
    info = request.app.state.api_tokens.resolve(raw.strip())
    if info is None:
        raise HTTPException(
            status_code=401,
            detail="Jeton d'API invalide.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    role = request.app.state.roles.get(info.role)
    if role is None:
        raise HTTPException(status_code=403, detail="Le rôle de ce jeton n'existe plus.")
    return User(username=f"api:{info.label}", role=role)


def create_api_app() -> FastAPI:
    api = FastAPI(
        title="mc-admin API",
        version="v1",
        summary="API locale en lecture seule (intégration dashboard, scripts).",
    )

    @api.exception_handler(PermissionDenied)
    async def _denied(_request: Request, exc: PermissionDenied):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @api.exception_handler(DomainError)
    async def _unavailable(_request: Request, exc: DomainError):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @api.get("/ping", summary="Test de disponibilité (aucune donnée sensible).")
    def ping() -> dict:
        return {"ok": True, "service": "mc-admin"}

    @api.get("/status", response_model=StatusModel, summary="État du serveur.")
    def status(request: Request, user: User = Depends(_bearer_user)) -> StatusModel:
        result = request.app.state.service.get_status(user)
        return StatusModel(
            online=result.container.running,
            players_available=result.players_available,
            players_online=[p.name for p in result.players_online],
            container=ContainerStateModel(
                name=result.container.name,
                running=result.container.running,
                status=result.container.status,
                health=result.container.health,
                started_at=result.container.started_at,
            ),
        )

    @api.get("/players", response_model=list[PlayerModel], summary="Historique des joueurs.")
    def players(request: Request, user: User = Depends(_bearer_user)) -> list[PlayerModel]:
        return [
            PlayerModel(
                name=summary.player,
                online=summary.online,
                total_seconds=summary.total_seconds,
                last_seen=summary.last_seen,
            )
            for summary in request.app.state.service.player_history(user)
        ]

    @api.get("/metrics", response_model=list[MetricModel], summary="Métriques instantanées.")
    def metrics(request: Request, user: User = Depends(_bearer_user)) -> list[MetricModel]:
        return [
            MetricModel(key=m.key, label=m.label, unit=m.unit, value=m.value)
            for m in request.app.state.service.metrics_snapshot(user)
        ]

    @api.get("/infra", response_model=list[ContainerHealthModel], summary="État des conteneurs surveillés.")
    def infra(request: Request, user: User = Depends(_bearer_user)) -> list[ContainerHealthModel]:
        return [
            ContainerHealthModel(
                name=state.name,
                running=state.running,
                status=state.status,
                health=state.health,
            )
            for state in request.app.state.service.infra_status(user)
        ]

    return api
