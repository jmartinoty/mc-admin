"""Noms affichés (préférence UI, per-user) — stockés hors du domaine RBAC.

Le pseudo de connexion reste la clé RBAC (config/roles.yml, lecture seule) et
l'identité d'audit ; le « nom affiché » est purement cosmétique et modifiable
par chaque utilisateur. Stocké dans un fichier inscriptible (/data), même
pattern que JsonTempBans. Accédé par la couche API (app.state), PAS par
AdminService : ce n'est pas une action serveur soumise à la RBAC.
"""
from __future__ import annotations

from adapters.atomic_json import atomic_write_json, load_json, shared_path_lock


class JsonDisplayNames:
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
        name = self._read().get(username)
        return name if isinstance(name, str) and name else None

    def set(self, username: str, display_name: str) -> None:
        with self._lock:
            data = self._read(strict=True)
            if display_name:
                data[username] = display_name
            else:
                data.pop(username, None)  # nom vide = retour au pseudo par défaut
            atomic_write_json(self._path, data, ensure_ascii=False)
