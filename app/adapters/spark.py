"""Adapter SparkProfilesPort : profils spark sauvés sur disque (perf palier 3).

Constat RCON validé en réel le 17/07/2026 : spark répond en asynchrone —
TOUTES ses sorties RCON sont perdues, et l'upload automatique du mode
`--timeout` ne laisse aucune trace exploitable. Le SEUL chemin fiable :
`spark profiler stop --save-to-file` écrit `profile-*.sparkprofile` dans
config/spark/ (monté ici en LECTURE SEULE). mc-admin liste ces fichiers et
les sert au téléchargement — à glisser sur https://spark.lucko.me pour
l'analyse complète (par chunk, par mod, par tick).
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from domain.model import SparkProfile

_PROFILE_RE = re.compile(r"^profile-[\w.\-]+\.sparkprofile$")


class FileSparkProfiles:
    def __init__(self, directory: str) -> None:
        self._dir = directory

    def list_profiles(self, limit: int = 10) -> list[SparkProfile]:
        try:
            entries = os.listdir(self._dir)
        except OSError:
            return []
        out: list[SparkProfile] = []
        for name in entries:
            if not _PROFILE_RE.match(name):
                continue
            try:
                stat = os.stat(os.path.join(self._dir, name))
            except OSError:
                continue
            out.append(SparkProfile(
                filename=name, size_bytes=stat.st_size,
                created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)))
        out.sort(key=lambda p: p.created_at, reverse=True)
        return out[:limit]

    def path(self, filename: str) -> str | None:
        """Chemin absolu d'un profil — motif STRICT, jamais de traversal."""
        if not _PROFILE_RE.match(filename):
            return None
        path = os.path.join(self._dir, filename)
        return path if os.path.isfile(path) else None
