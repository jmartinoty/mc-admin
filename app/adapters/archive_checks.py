"""Adapter ArchiveChecksPort : résultats de vérification persistés en JSON.

Même famille que les autres états de /data (pending_unbans, backup_schedule) :
un seul process écrit (le vérificateur de fond), le service lit. Fichier
absent/corrompu = aucun résultat, jamais d'erreur — les badges de la page
Sauvegardes affichent simplement « vérification à venir ».
"""
from __future__ import annotations

from datetime import datetime

from adapters.atomic_json import (
    CorruptJsonError,
    atomic_write_json,
    load_json,
    shared_path_lock,
)
from domain.model import ArchiveCheck


# Clé réservée du fichier JSON : archive en cours de vérification (affichage).
_CHECKING_KEY = "__checking__"


class JsonArchiveChecks:
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
        if strict:
            for filename, raw in data.items():
                if filename == _CHECKING_KEY:
                    if raw is not None and not isinstance(raw, str):
                        raise CorruptJsonError(
                            f"schéma JSON inattendu : {self._path}"
                        )
                    continue
                try:
                    if (
                        not isinstance(raw, dict)
                        or not isinstance(raw.get("ok"), bool)
                        or not (
                            isinstance(raw.get("checked_at"), str)
                            and raw["checked_at"]
                        )
                        or not (
                            isinstance(raw.get("size_bytes"), int)
                            and not isinstance(raw.get("size_bytes"), bool)
                        )
                        or not isinstance(raw.get("detail", ""), str)
                        or (
                            raw.get("restorable") is not None
                            and not isinstance(raw.get("restorable"), bool)
                        )
                        or not isinstance(raw.get("world_name", ""), str)
                        or (
                            raw.get("archive_created_at") is not None
                            and not isinstance(raw.get("archive_created_at"), str)
                        )
                    ):
                        raise TypeError
                    self._to_check(filename, raw)
                except (KeyError, TypeError, ValueError):
                    raise CorruptJsonError(
                        f"schéma JSON inattendu : {self._path}"
                    ) from None
        return data

    @staticmethod
    def _to_check(filename: str, raw: dict) -> ArchiveCheck:
        return ArchiveCheck(
            filename=filename,
            ok=bool(raw["ok"]),
            checked_at=datetime.fromisoformat(raw["checked_at"]),
            size_bytes=int(raw["size_bytes"]),
            detail=str(raw.get("detail", "")),
            restorable=(
                raw.get("restorable")
                if isinstance(raw.get("restorable"), bool)
                else None
            ),
            world_name=str(raw.get("world_name", "")),
            archive_created_at=(
                datetime.fromisoformat(raw["archive_created_at"])
                if isinstance(raw.get("archive_created_at"), str)
                else None
            ),
        )

    def statuses(self) -> dict[str, ArchiveCheck]:
        result: dict[str, ArchiveCheck] = {}
        for filename, raw in self._load().items():
            if filename == _CHECKING_KEY:
                continue
            try:
                result[filename] = self._to_check(filename, raw)
            except (KeyError, TypeError, ValueError):
                continue  # entrée corrompue : ignorée, sera revérifiée
        return result

    def checking(self) -> str | None:
        """Archive en cours de vérification, s'il y en a une."""
        value = self._load().get(_CHECKING_KEY)
        return value if isinstance(value, str) else None

    def mark_checking(self, filename: str | None) -> None:
        """Pose (ou efface avec None) le marqueur « vérification en cours »."""
        with self._lock:
            data = self._load(strict=True)
            if filename is None:
                if _CHECKING_KEY not in data:
                    return
                data.pop(_CHECKING_KEY, None)
            else:
                data[_CHECKING_KEY] = filename
            self._write(data)

    def record(self, check: ArchiveCheck) -> None:
        with self._lock:
            data = self._load(strict=True)
            data.pop(_CHECKING_KEY, None)  # le verdict remplace le marqueur
            data[check.filename] = {
                "ok": check.ok,
                "checked_at": check.checked_at.isoformat(),
                "size_bytes": check.size_bytes,
                "detail": check.detail,
                "restorable": check.restorable,
                "world_name": check.world_name,
                "archive_created_at": (
                    check.archive_created_at.isoformat()
                    if check.archive_created_at is not None
                    else None
                ),
            }
            self._write(data)

    def forget_missing(self, existing: set[str]) -> None:
        """Purge les résultats d'archives disparues (rétention, suppression)."""
        with self._lock:
            data = self._load(strict=True)
            kept = {k: v for k, v in data.items()
                    if k in existing or k == _CHECKING_KEY}
            if len(kept) != len(data):
                self._write(kept)

    def _write(self, data: dict) -> None:
        atomic_write_json(
            self._path,
            data,
            ensure_ascii=False,
            indent=2,
        )
