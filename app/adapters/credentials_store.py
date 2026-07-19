"""Overlay inscriptible des hash de mot de passe (per-user).

config/roles.yml (rôles + permissions + hash initiaux) reste monté en LECTURE
SEULE : on n'y touche jamais. Quand un utilisateur change son mot de passe,
le nouveau hash Argon2 est écrit ici (/data, inscriptible) et l'auth le
consulte EN PRIORITÉ sur celui de roles.yml. Même pattern que JsonDisplayNames.
"""
from __future__ import annotations

from adapters.atomic_json import atomic_write_json, load_json, shared_path_lock


class JsonCredentials:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)

    def _read(self, *, strict: bool = False) -> dict:
        return load_json(
            self._path,
            expected_type=dict,
            default_factory=dict,
            strict=strict,
        )

    def get(self, username: str) -> str | None:
        h = self._read().get(username)
        return h if isinstance(h, str) and h else None

    def set(self, username: str, password_hash: str) -> None:
        with self._lock:
            data = self._read(strict=True)
            data[username] = password_hash
            atomic_write_json(self._path, data, mode=0o600)

    def remove(self, username: str) -> bool:
        with self._lock:
            data = self._read(strict=True)
            if username not in data:
                return False
            del data[username]
            atomic_write_json(self._path, data, mode=0o600)
            return True
