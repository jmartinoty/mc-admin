"""Authentification : vérification de mot de passe (Argon2) + décision d'accès.

`authenticate` est PURE (hasher injecté) → testable sans argon2. L'implémentation
réelle `Argon2Hasher` importe argon2 paresseusement.
"""
from __future__ import annotations

from typing import Protocol

from domain.model import User


class PasswordHasher(Protocol):
    def verify(self, stored_hash: str, password: str) -> bool: ...
    def hash(self, password: str) -> str: ...


class Argon2Hasher:
    """Adapter Argon2 (argon2-cffi). Construit au démarrage de l'app."""

    def __init__(self) -> None:
        from argon2 import PasswordHasher as _PH  # import paresseux
        self._ph = _PH()

    def verify(self, stored_hash: str, password: str) -> bool:
        # argon2 lève sur non-correspondance / hash invalide → on renvoie un bool.
        try:
            return bool(self._ph.verify(stored_hash, password))
        except Exception:
            return False

    def hash(self, password: str) -> str:
        return self._ph.hash(password)


def authenticate(
    credentials: dict[str, str],
    users: dict[str, User],
    username: str,
    password: str,
    hasher: PasswordHasher,
    *,
    fallback_hash: str | None = None,
) -> User | None:
    """Renvoie l'User si le couple (username, password) est valide, sinon None."""
    stored = credentials.get(username)
    user = users.get(username)
    if not stored or user is None:
        if fallback_hash is not None:
            hasher.verify(fallback_hash, password)
        return None
    if hasher.verify(stored, password):
        return user
    return None
