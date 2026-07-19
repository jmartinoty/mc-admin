"""Adapter ModsPort : liste du dossier mods/ monté en lecture seule (V6).

Les métadonnées (nom, version, description) sont lues dans le
`fabric.mod.json` embarqué à la racine de chaque .jar (format Fabric ;
`quilt.mod.json` en repli). Best-effort intégral : un jar illisible ou sans
manifeste s'affiche avec son seul nom de fichier — la page ne casse jamais.
Un fichier `.jar.disabled` (convention répandue) est listé comme désactivé.
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from datetime import datetime, timezone

from domain.model import ModInfo


def _sha1_of(path: str) -> str:
    """Empreinte du .jar en flux (mémoire constante) — best-effort."""
    try:
        digest = hashlib.sha1()  # noqa: S324 — identifiant Modrinth, pas de la crypto
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _read_metadata(path: str) -> dict:
    try:
        with zipfile.ZipFile(path) as jar:
            for manifest in ("fabric.mod.json", "quilt.mod.json"):
                try:
                    with jar.open(manifest) as fh:
                        # strict=False : certains mods embarquent des \n bruts
                        # dans leur description, invalides en JSON strict.
                        data = json.loads(fh.read().decode("utf-8", errors="replace"), strict=False)
                    if manifest == "quilt.mod.json":
                        data = (data.get("quilt_loader") or {}).get("metadata") or {}
                    return data if isinstance(data, dict) else {}
                except KeyError:
                    continue
    except (OSError, zipfile.BadZipFile, ValueError):
        pass
    return {}


class FileMods:
    def __init__(self, mods_dir: str) -> None:
        self._dir = mods_dir
        # Cache par (nom, mtime, taille) : ne relit un jar que s'il a changé.
        self._cache: dict[tuple[str, float, int], ModInfo] = {}

    def list_mods(self) -> list[ModInfo]:
        try:
            entries = sorted(os.listdir(self._dir))
        except OSError:
            return []
        mods: list[ModInfo] = []
        fresh: dict[tuple[str, float, int], ModInfo] = {}
        for entry in entries:
            enabled = entry.endswith(".jar")
            if not enabled and not entry.endswith(".jar.disabled"):
                continue
            path = os.path.join(self._dir, entry)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            key = (entry, stat.st_mtime, stat.st_size)
            cached = self._cache.get(key)
            if cached is not None:
                fresh[key] = cached
                mods.append(cached)
                continue
            meta = _read_metadata(path)
            info = ModInfo(
                filename=entry,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                name=str(meta.get("name") or ""),
                version=str(meta.get("version") or ""),
                description=str(meta.get("description") or "").strip(),
                enabled=enabled,
                sha1=_sha1_of(path),
            )
            fresh[key] = info
            mods.append(info)
        self._cache = fresh
        mods.sort(key=lambda m: (m.name or m.filename).lower())
        return mods
