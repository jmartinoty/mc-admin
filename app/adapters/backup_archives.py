"""Adapter BackupArchivesPort : liste les archives existantes.

Lecture SEULE d'un répertoire monté en lecture seule (même politique que les
logs/ops.json) : mc-admin liste les fichiers et ne supprime que le périmètre
explicitement autorisé. La rétention mc-admin ne cible que le sous-dossier
`scheduled/` : les archives `manual/`, `restore-safety/` et les archives
historiques à la racine restent protégées.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from domain.model import BackupArchive


class FileBackupArchives:
    def __init__(self, directory: str) -> None:
        self._directory = Path(directory)

    def list_archives(self) -> list[BackupArchive]:
        if not self._directory.exists():
            return []
        archives = []
        for path in self._directory.rglob("*.tar.gz"):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue  # supprimé entre le listdir et le stat (rétention concurrente)
            if not path.is_file():
                continue  # ignore le fichier de verrou de mc-backup et tout non-archive
            name = path.relative_to(self._directory).as_posix()
            archives.append(
                BackupArchive(
                    filename=name,
                    size_bytes=stat.st_size,
                    created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                )
            )
        archives.sort(key=lambda a: a.created_at, reverse=True)
        return archives

    def archive_path(self, filename: str) -> Path | None:
        """Résout une archive existante sans permettre de sortie de répertoire."""
        if not filename or filename.startswith("/") or not filename.endswith(".tar.gz"):
            return None
        requested = Path(filename)
        if any(part in ("", ".", "..") for part in requested.parts):
            return None
        path = self._directory / filename
        try:
            resolved_dir = self._directory.resolve(strict=True)
            resolved_path = path.resolve(strict=True)
        except FileNotFoundError:
            return None
        try:
            resolved_path.relative_to(resolved_dir)
        except ValueError:
            return None
        if not resolved_path.is_file():
            return None
        return resolved_path

    def free_bytes(self) -> int | None:
        """Espace libre du volume des sauvegardes (None si illisible)."""
        try:
            import shutil
            return shutil.disk_usage(self._directory).free
        except OSError:
            return None

    def delete_archive(self, filename: str) -> bool:
        """Supprime une archive — UNIQUEMENT sous scheduled/ (les manuelles,
        restore-safety/ et l'historique sont protégés). Renvoie False si le nom
        est hors périmètre ou introuvable."""
        if not (filename.startswith("scheduled/") or filename.startswith("profiles/")):
            return False  # manuelles, sécurité et historique : jamais supprimables par mc-admin
        path = self.archive_path(filename)  # anti-traversal + existence
        if path is None:
            return False
        try:
            path.unlink()
        except OSError:
            return False
        return True
