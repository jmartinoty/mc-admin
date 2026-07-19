"""Persistance des niveaux OP à appliquer au prochain redémarrage.

Le fichier principal reçoit les nouvelles demandes de mc-admin. Le worker
déplace atomiquement un instantané vers le fichier ``applying`` avant de
piloter Minecraft. Un verrou ``flock`` partagé évite les courses entre les
deux conteneurs, en complément du verrou de processus d'``atomic_json``.
"""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from collections.abc import Iterator

from domain.errors import OpLevelsUnavailable
from domain.model import PendingOpLevel

from .atomic_json import (
    CorruptJsonError,
    atomic_write_json,
    load_json,
    shared_path_lock,
)

_VERSION = 1


class JsonPendingOpLevels:
    def __init__(self, path: str) -> None:
        self._path = path
        root, extension = os.path.splitext(path)
        self._applying_path = f"{root}.applying{extension or '.json'}"
        self._lock_path = f"{path}.lock"
        self._lock = shared_path_lock(path)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        parent = os.path.dirname(self._lock_path) or "."
        os.makedirs(parent, exist_ok=True)
        with self._lock:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    @staticmethod
    def _decode(path: str, *, strict: bool) -> dict[str, PendingOpLevel]:
        raw = load_json(
            path,
            expected_type=dict,
            default_factory=dict,
            strict=strict,
        )
        if not raw:
            return {}
        levels = raw.get("levels")
        if raw.get("version") != _VERSION or not isinstance(levels, dict):
            if strict:
                raise CorruptJsonError(f"état des niveaux OP invalide : {path}")
            return {}

        decoded: dict[str, PendingOpLevel] = {}
        for key, item in levels.items():
            valid = (
                isinstance(key, str)
                and isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and isinstance(item.get("level"), int)
                and not isinstance(item.get("level"), bool)
                and 1 <= item["level"] <= 4
                and key == item["name"].lower()
            )
            if not valid:
                if strict:
                    raise CorruptJsonError(
                        f"entrée de niveau OP invalide dans {path}"
                    )
                continue
            decoded[key] = PendingOpLevel(
                name=item["name"],
                level=item["level"],
            )
        return decoded

    @staticmethod
    def _payload(levels: dict[str, PendingOpLevel]) -> dict:
        return {
            "version": _VERSION,
            "levels": {
                key: {"name": item.name, "level": item.level}
                for key, item in sorted(levels.items())
            },
        }

    def _write(self, path: str, levels: dict[str, PendingOpLevel]) -> None:
        atomic_write_json(
            path,
            self._payload(levels),
            ensure_ascii=True,
            indent=2,
            mode=0o600,
        )

    def list_pending(self) -> list[PendingOpLevel]:
        try:
            with self._locked():
                applying = self._decode(self._applying_path, strict=True)
                pending = self._decode(self._path, strict=True)
        except CorruptJsonError as exc:
            raise OpLevelsUnavailable(
                "état des niveaux OP en attente corrompu"
            ) from exc
        merged = {
            key: PendingOpLevel(item.name, item.level, applying=True)
            for key, item in applying.items()
        }
        merged.update(pending)
        return sorted(merged.values(), key=lambda item: item.name.lower())

    def set_level(self, player_name: str, level: int) -> None:
        if not player_name or level not in (1, 2, 3, 4):
            raise ValueError("niveau OP en attente invalide")
        key = player_name.lower()
        with self._locked():
            pending = self._decode(self._path, strict=True)
            pending[key] = PendingOpLevel(player_name, level)
            self._write(self._path, pending)

    def remove(self, player_name: str) -> None:
        key = player_name.lower()
        with self._locked():
            pending = self._decode(self._path, strict=True)
            if pending.pop(key, None) is not None:
                self._write(self._path, pending)

    def claim(self) -> list[PendingOpLevel]:
        """Réclame les demandes pour le worker, y compris un ancien lot interrompu."""
        with self._locked():
            applying = self._decode(self._applying_path, strict=True)
            pending = self._decode(self._path, strict=True)
            applying.update(pending)
            self._write(self._applying_path, applying)
            self._write(self._path, {})
        return sorted(applying.values(), key=lambda item: item.name.lower())

    def complete_claim(self, unresolved: list[PendingOpLevel] | None = None) -> None:
        """Termine le lot et remet les opérateurs absents dans la file."""
        unresolved = unresolved or []
        with self._locked():
            pending = self._decode(self._path, strict=True)
            for item in unresolved:
                pending.setdefault(item.name.lower(), PendingOpLevel(item.name, item.level))
            self._write(self._path, pending)
            self._write(self._applying_path, {})

    def restore_claim(
        self,
        claimed: list[PendingOpLevel] | None = None,
    ) -> None:
        """Remet le lot réclamé en attente après un échec transactionnel."""
        claimed = claimed or []
        with self._locked():
            applying = self._decode(self._applying_path, strict=True)
            pending = self._decode(self._path, strict=True)
            for item in claimed:
                applying[item.name.lower()] = PendingOpLevel(
                    item.name,
                    item.level,
                )
            applying.update(pending)
            self._write(self._path, applying)
            self._write(self._applying_path, {})
