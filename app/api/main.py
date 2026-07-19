"""Point d'entrée uvicorn : `uvicorn api.main:app`.

Séparé de la fabrique (api.app) pour que celle-ci reste importable par les
tests sans construire l'application (qui exige SESSION_SECRET, roles.yml…).
"""
from __future__ import annotations

from api.app import create_app

app = create_app()
