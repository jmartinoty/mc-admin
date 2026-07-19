"""Adapter : conteneurs COMPAGNONS surveillés (A2.1 — un seul cerveau).

`/data/watched_containers.json` est LA source de vérité de la liste que le
HealthWatcher observe en plus du serveur (tunnel playit, proxy…) :

    {"containers": ["playit"], "env_import_done": true}

`HEALTH_EXTRA_CONTAINERS` n'est plus qu'un amorçage : importée UNE FOIS
(drapeau `env_import_done`, même pattern que users.json), puis la liste se
gère dans l'UI. Le watcher RELIT ce fichier à chaque sonde — un ajout est
effectif en ~30 s, sans redémarrage.
"""
from __future__ import annotations

import os

from adapters.atomic_json import (
    CorruptJsonError,
    atomic_write_json,
    load_json,
    shared_path_lock,
)


class JsonWatched:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)

    def _load(self, *, strict: bool = False) -> dict:
        data = load_json(
            self._path,
            expected_type=dict,
            default_factory=dict,
            strict=strict,
        )
        containers = data.get("containers")
        if strict and (
            (containers is not None and not isinstance(containers, list))
            or (
                isinstance(containers, list)
                and any(not isinstance(item, str) for item in containers)
            )
            or (
                "env_import_done" in data
                and not isinstance(data["env_import_done"], bool)
            )
        ):
            raise CorruptJsonError(f"schéma JSON inattendu : {self._path}")
        return {
            "containers": [str(c) for c in containers] if isinstance(containers, list) else [],
            "env_import_done": bool(data.get("env_import_done", False)),
        }

    def _write(self, data: dict) -> None:
        atomic_write_json(self._path, data, ensure_ascii=False, indent=2)

    def all(self) -> list[str]:
        return self._load()["containers"]

    def add(self, name: str) -> bool:
        with self._lock:
            data = self._load(strict=True)
            if name in data["containers"]:
                return False
            data["containers"].append(name)
            self._write(data)
            return True

    def remove(self, name: str) -> bool:
        with self._lock:
            data = self._load(strict=True)
            if name not in data["containers"]:
                return False
            data["containers"].remove(name)
            self._write(data)
            return True

    def import_from_env(self, names: list[str]) -> list[str]:
        """Amorçage one-shot depuis HEALTH_EXTRA_CONTAINERS. Idempotent, et
        sans écriture quand il n'y a rien à importer ni fichier existant
        (le drapeau ne sert qu'à ne pas ressusciter un nom retiré en UI)."""
        with self._lock:
            data = self._load(strict=True)
            if data["env_import_done"]:
                return []
            imported = [n for n in names if n and n not in data["containers"]]
            if not imported and not os.path.exists(self._path):
                return []
            data["containers"].extend(imported)
            data["env_import_done"] = True
            self._write(data)
            return imported
