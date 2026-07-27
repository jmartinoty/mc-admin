"""Jetons d'API locale (intégration nas-dashboard, scripts).

Un jeton porteur (`Authorization: Bearer <token>`) authentifie un appelant
non-navigateur. Il est lié à un RÔLE existant : l'API réutilise donc la même
RBAC et le même audit que l'UI (le jeton se résout en un `User` synthétique
porteur de ce rôle). Aucune logique de sécurité nouvelle.

Comme les autres secrets : fichier 0600, écritures atomiques. Le jeton en clair
n'est montré QU'UNE FOIS à la création ; seul son SHA-256 est stocké (comparé
en temps constant), jamais le jeton lui-même — une fuite du fichier ne rejoue
pas les jetons.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from adapters.atomic_json import atomic_write_json, load_json, shared_path_lock


@dataclass(frozen=True)
class ApiTokenInfo:
    """Projection d'affichage (jamais le secret)."""

    token_id: str
    label: str
    role: str
    created_at: float


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ApiTokenStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)

    def _read(self, *, strict: bool = False) -> dict:
        return load_json(
            self._path, expected_type=dict, default_factory=dict, strict=strict
        )

    def create(self, label: str, role: str) -> tuple[str, str]:
        """Crée un jeton pour `role`. Renvoie (token_id, jeton en clair).
        Le jeton n'est jamais reconstituable ensuite."""
        raw = secrets.token_urlsafe(32)
        token_id = secrets.token_hex(8)
        clean_label = " ".join((label or "").split())[:64] or "sans nom"
        with self._lock:
            data = self._read(strict=True)
            data[token_id] = {
                "label": clean_label,
                "role": role,
                "token_hash": _hash_token(raw),
                "created_at": time.time(),
            }
            atomic_write_json(self._path, data, mode=0o600)
        return token_id, raw

    def resolve(self, raw: str) -> ApiTokenInfo | None:
        """Retrouve le jeton par son secret (comparaison temps constant).
        None si aucun ne correspond."""
        if not raw:
            return None
        wanted = _hash_token(raw)
        for token_id, entry in self._read().items():
            if not isinstance(entry, dict):
                continue
            stored = entry.get("token_hash")
            if isinstance(stored, str) and hmac.compare_digest(stored, wanted):
                return ApiTokenInfo(
                    token_id=token_id,
                    label=str(entry.get("label") or "sans nom"),
                    role=str(entry.get("role") or ""),
                    created_at=float(entry.get("created_at") or 0.0),
                )
        return None

    def list(self) -> list[ApiTokenInfo]:
        infos = [
            ApiTokenInfo(
                token_id=token_id,
                label=str(entry.get("label") or "sans nom"),
                role=str(entry.get("role") or ""),
                created_at=float(entry.get("created_at") or 0.0),
            )
            for token_id, entry in self._read().items()
            if isinstance(entry, dict)
        ]
        infos.sort(key=lambda info: info.created_at, reverse=True)
        return infos

    def revoke(self, token_id: str) -> bool:
        with self._lock:
            data = self._read(strict=True)
            if token_id not in data:
                return False
            del data[token_id]
            atomic_write_json(self._path, data, mode=0o600)
            return True
