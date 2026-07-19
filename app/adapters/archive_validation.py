"""Inspection complète et sûre des archives de sauvegarde Minecraft."""
from __future__ import annotations

import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath

from domain.model import ArchiveCheck


_MAX_SERVER_PROPERTIES_BYTES = 1024 * 1024
RESTORE_TRANSACTION_NAMES = frozenset({
    ".mc-restore-stage",
    ".mc-restore-rollback",
    ".mc-restore-transaction.json",
    ".mc-restore-transaction.json.tmp",
})


@dataclass(frozen=True)
class ArchiveInspection:
    ok: bool
    restorable: bool
    detail: str = ""
    world_name: str = ""


def safe_archive_member_name(name: str) -> str:
    if (
        not name
        or any(character in name for character in ("\x00", "\n", "\r", "\\"))
    ):
        raise ValueError("chemin d'archive invalide")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"chemin dangereux dans l'archive : {name}")
    normalised = "/".join(part for part in path.parts if part not in ("", "."))
    root_name = normalised.split("/", 1)[0]
    if root_name in RESTORE_TRANSACTION_NAMES:
        raise ValueError(f"nom réservé dans l'archive : {root_name}")
    return normalised or "."


def inspect_archive(path) -> ArchiveInspection:
    """Relit tout le flux gzip/tar et détermine si un monde est restaurable.

    Aucun contenu n'est extrait. Les liens et types spéciaux sont refusés :
    l'archive sera ensuite extraite par un conteneur privilégié et ne doit
    donc offrir aucun moyen d'écrire hors de la racine Minecraft.
    """
    regular_sizes: dict[str, int] = {}
    seen_entries: set[str] = set()
    server_properties: bytes | None = None
    regular_count = 0

    try:
        with tarfile.open(path, mode="r|gz") as archive:
            for member in archive:
                name = safe_archive_member_name(member.name)
                if member.issym() or member.islnk():
                    raise ValueError(f"lien interdit dans l'archive : {member.name}")
                if member.isdev() or member.isfifo():
                    raise ValueError(f"fichier spécial interdit dans l'archive : {member.name}")
                if not member.isdir() and not member.isreg():
                    raise ValueError(f"type de fichier non pris en charge : {member.name}")
                if member.isdir():
                    continue
                if name in seen_entries:
                    raise ValueError(f"entrée dupliquée dans l'archive : {member.name}")
                seen_entries.add(name)
                regular_count += 1
                regular_sizes[name] = member.size

                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"fichier illisible dans l'archive : {member.name}")
                captured = bytearray() if name == "server.properties" else None
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    if captured is not None:
                        if len(captured) + len(chunk) > _MAX_SERVER_PROPERTIES_BYTES:
                            raise ValueError("server.properties est anormalement volumineux")
                        captured.extend(chunk)
                if captured is not None:
                    if server_properties is not None:
                        raise ValueError("server.properties est présent plusieurs fois")
                    server_properties = bytes(captured)
    except Exception as exc:  # noqa: BLE001 - tout échec rend l'archive impropre
        return ArchiveInspection(False, False, str(exc))

    if regular_count == 0:
        return ArchiveInspection(False, False, "l'archive ne contient aucun fichier")
    if server_properties is None:
        return ArchiveInspection(
            True,
            False,
            "archive intègre, mais server.properties est absent à la racine",
        )

    level_name = ""
    for raw_line in server_properties.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "!")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "level-name":
            level_name = value.strip()
            break
    if not level_name:
        return ArchiveInspection(
            True,
            False,
            "archive intègre, mais level-name est absent de server.properties",
        )
    try:
        safe_world_name = safe_archive_member_name(level_name)
    except ValueError as exc:
        return ArchiveInspection(True, False, str(exc))
    level_dat = f"{safe_world_name}/level.dat"
    if regular_sizes.get(level_dat, 0) <= 0:
        return ArchiveInspection(
            True,
            False,
            f"archive intègre, mais {level_dat} est absent ou vide",
            safe_world_name,
        )
    return ArchiveInspection(True, True, world_name=safe_world_name)


class FileArchiveValidator:
    """Validation synchrone d'une archive nommée via BackupArchivesPort."""

    def __init__(self, archives, clock=None) -> None:
        self._archives = archives
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def validate(self, filename: str) -> ArchiveCheck:
        metadata = next(
            (archive for archive in self._archives.list_archives() if archive.filename == filename),
            None,
        )
        path = self._archives.archive_path(filename)
        if metadata is None or path is None:
            return ArchiveCheck(
                filename=filename,
                ok=False,
                checked_at=self._clock(),
                size_bytes=0,
                detail="archive introuvable",
                restorable=False,
            )
        inspection = inspect_archive(path)
        return ArchiveCheck(
            filename=filename,
            ok=inspection.ok,
            checked_at=self._clock(),
            size_bytes=metadata.size_bytes,
            detail=inspection.detail,
            restorable=inspection.restorable,
            world_name=inspection.world_name,
            archive_created_at=metadata.created_at,
        )
