"""Adapter : LA source de vérité des comptes (V6.5).

`/data/users.json` : {"users": {username: role}, "yaml_import_done": bool}

Depuis la V6.5, les comptes vivent ici et uniquement ici — roles.yml ne
définit plus que les rôles. Les comptes historiques d'un roles.yml sont
importés UNE FOIS (cf. config.migrate_yaml_users) ; le drapeau
`yaml_import_done` empêche une réimportation (donc la résurrection d'un
compte supprimé dans l'app). Effacer users.json = repartir de zéro : le
drapeau disparaît avec, et un roles.yml présent est réimporté.

Compat lecture : format plat V6.2 ({username: role}) et clé "removed"
V6.4 (tombstones — exposée via legacy_removed() pour que la migration ne
réimporte pas un compte supprimé, puis abandonnée à la première écriture).
"""
from __future__ import annotations

from adapters.atomic_json import atomic_write_json, load_json, shared_path_lock


class JsonUsers:
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
        if "users" not in data and "removed" not in data:
            data = {"users": data}  # format plat V6.2
        users = data.get("users")
        removed = data.get("removed")
        return {
            "users": {str(k): str(v) for k, v in users.items()} if isinstance(users, dict) else {},
            "removed": [str(u) for u in removed] if isinstance(removed, list) else [],
            "yaml_import_done": bool(data.get("yaml_import_done", False)),
        }

    def _write(self, data: dict) -> None:
        atomic_write_json(
            self._path,
            {"users": data["users"], "yaml_import_done": data["yaml_import_done"]},
            ensure_ascii=False,
            indent=2,
        )

    def all(self) -> dict[str, str]:
        return self._load()["users"]

    def add(self, username: str, role: str) -> None:
        """Crée un compte ou fixe le rôle d'un compte existant."""
        with self._lock:
            data = self._load(strict=True)
            data["users"][username] = role
            self._write(data)

    def remove(self, username: str) -> bool:
        with self._lock:
            data = self._load(strict=True)
            if username not in data["users"]:
                return False
            del data["users"][username]
            self._write(data)
            return True

    # ---- migration V6.5 (cf. config.migrate_yaml_users) ----

    def yaml_import_done(self) -> bool:
        return self._load()["yaml_import_done"]

    def legacy_removed(self) -> set[str]:
        """Tombstones V6.4 encore présents dans le fichier (jamais réécrits)."""
        return set(self._load()["removed"])

    def import_yaml_users(self, entries: dict[str, str]) -> None:
        """Ajoute les comptes importés (sans écraser l'existant) et pose le
        drapeau — une seule écriture, atomique."""
        with self._lock:
            data = self._load(strict=True)
            for username, role in entries.items():
                data["users"].setdefault(username, role)
            data["yaml_import_done"] = True
            self._write(data)
