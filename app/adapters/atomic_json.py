"""Primitives communes pour les petits fichiers JSON persistants.

Les adaptateurs de mc-admin tournent dans un seul processus, mais plusieurs
instances peuvent viser le même fichier. Le verrou est donc partagé par chemin
et l'écriture remplace atomiquement l'ancienne version après synchronisation.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from domain.errors import DomainError

T = TypeVar("T")


class CorruptJsonError(ValueError, DomainError):
    """Le fichier existe, mais son contenu ne peut pas être muté sans risque."""


_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def shared_path_lock(path: str) -> threading.RLock:
    """Retourne le même verrou pour toutes les instances visant ``path``."""
    key = os.path.realpath(os.path.abspath(path))
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def load_json(
    path: str,
    *,
    expected_type: type[T],
    default_factory: Callable[[], T],
    strict: bool = False,
) -> T:
    """Charge un JSON typé.

    Les lectures restent tolérantes pour garder l'interface disponible. Les
    mutations utilisent ``strict=True`` afin de ne jamais remplacer
    silencieusement un fichier existant mais corrompu.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data: Any = json.load(fh)
    except FileNotFoundError:
        return default_factory()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        if strict:
            raise CorruptJsonError(f"état JSON illisible : {path}") from exc
        return default_factory()
    if not isinstance(data, expected_type):
        if strict:
            raise CorruptJsonError(f"type JSON inattendu : {path}")
        return default_factory()
    return data


def atomic_write_json(
    path: str,
    data: Any,
    *,
    ensure_ascii: bool = True,
    indent: int | None = None,
    mode: int | None = None,
) -> None:
    """Écrit ``data`` sans exposer de fichier partiel aux autres lecteurs."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=parent,
    )
    try:
        if mode is None:
            try:
                mode = stat.S_IMODE(os.stat(path).st_mode)
            except FileNotFoundError:
                pass
        if mode is not None:
            os.fchmod(fd, mode)

        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            json.dump(data, fh, ensure_ascii=ensure_ascii, indent=indent)
            fh.flush()
            os.fsync(fh.fileno())

        os.replace(tmp_path, path)
        _fsync_directory(parent)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def _fsync_directory(path: str) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
