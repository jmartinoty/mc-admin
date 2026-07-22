"""Secrets TOTP par utilisateur (deuxième facteur).

Un secret TOTP est un secret partagé au même titre qu'un hash de mot de passe :
fichier en 0600, écritures atomiques (mêmes primitives que JsonCredentials).

Schéma : {username: {"secret": <base32>, "confirmed": bool}}.
- `confirmed=false` : secret généré mais pas encore validé par un premier code
  (l'utilisateur est en train de configurer son app) — 2FA PAS encore exigée
  au login.
- `confirmed=true` : 2FA active, un code sera demandé après le mot de passe.
"""
from __future__ import annotations

from adapters.atomic_json import atomic_write_json, load_json, shared_path_lock


class JsonTotp:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)

    def _read(self, *, strict: bool = False) -> dict:
        return load_json(
            self._path, expected_type=dict, default_factory=dict, strict=strict
        )

    def _entry(self, username: str) -> dict | None:
        entry = self._read().get(username)
        if not isinstance(entry, dict):
            return None
        secret = entry.get("secret")
        if not isinstance(secret, str) or not secret:
            return None
        return {"secret": secret, "confirmed": bool(entry.get("confirmed"))}

    def is_enabled(self, username: str) -> bool:
        """2FA active (secret confirmé) pour ce compte ?"""
        entry = self._entry(username)
        return bool(entry and entry["confirmed"])

    def has_pending(self, username: str) -> bool:
        """Un secret en cours de configuration (non confirmé) existe-t-il ?"""
        entry = self._entry(username)
        return bool(entry and not entry["confirmed"])

    def secret(self, username: str) -> str | None:
        entry = self._entry(username)
        return entry["secret"] if entry else None

    def set_pending(self, username: str, secret: str) -> None:
        """Enregistre un secret non confirmé (début de configuration)."""
        with self._lock:
            data = self._read(strict=True)
            data[username] = {"secret": secret, "confirmed": False}
            atomic_write_json(self._path, data, mode=0o600)

    def confirm(self, username: str) -> bool:
        """Passe le secret en confirmé (2FA active). False si rien à confirmer."""
        with self._lock:
            data = self._read(strict=True)
            entry = data.get(username)
            if not isinstance(entry, dict) or not entry.get("secret"):
                return False
            entry["confirmed"] = True
            atomic_write_json(self._path, data, mode=0o600)
            return True

    def remove(self, username: str) -> bool:
        with self._lock:
            data = self._read(strict=True)
            if username not in data:
                return False
            del data[username]
            atomic_write_json(self._path, data, mode=0o600)
            return True
