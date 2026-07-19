"""Primitives de sécurité indépendantes du framework (donc testables en stdlib).

CSRF : pattern « synchronizer token » — un jeton aléatoire est stocké dans la
session (cookie signé) et rendu dans chaque formulaire ; à la soumission, on
compare en temps constant le jeton du formulaire à celui de la session.
"""
from __future__ import annotations

import hmac
import secrets


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(session_token: str | None, form_token: str | None) -> bool:
    if not session_token or not form_token:
        return False
    # compare_digest : évite les attaques par timing sur la comparaison.
    return hmac.compare_digest(session_token, form_token)
