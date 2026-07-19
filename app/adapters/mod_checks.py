"""Adapter ModChecksPort : verdicts Modrinth persistés en JSON.

Même famille que archive_checks : le checker de fond ÉCRIT, le service LIT.
Fichier absent/corrompu = aucun verdict (badges « vérification à venir »),
jamais d'erreur. Clé = empreinte SHA-1 (un jar renommé garde son verdict).
"""
from __future__ import annotations

from datetime import datetime

from adapters.atomic_json import (
    CorruptJsonError,
    atomic_write_json,
    load_json,
    shared_path_lock,
)
from domain.model import ModUpdate


class JsonModChecks:
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
        checks = data.get("checks", {})
        if not isinstance(checks, dict):
            if strict:
                raise CorruptJsonError(
                    f"schéma JSON inattendu : {self._path}"
                )
            data = dict(data)
            data["checks"] = {}
            checks = {}
        if strict:
            checked_at = data.get("checked_at")
            if checked_at is not None:
                if not isinstance(checked_at, str):
                    raise CorruptJsonError(
                        f"schéma JSON inattendu : {self._path}"
                    )
                try:
                    datetime.fromisoformat(checked_at)
                except ValueError:
                    raise CorruptJsonError(
                        f"schéma JSON inattendu : {self._path}"
                    ) from None
            for sha1, raw in checks.items():
                try:
                    if (
                        not isinstance(raw, dict)
                        or not isinstance(raw.get("known"), bool)
                        or not isinstance(raw.get("project_url", ""), str)
                        or not isinstance(raw.get("installed_version", ""), str)
                        or not isinstance(raw.get("latest_version", ""), str)
                        or not isinstance(
                            raw.get("update_available", False),
                            bool,
                        )
                        or (
                            raw.get("checked_at") is not None
                            and not isinstance(raw.get("checked_at"), str)
                        )
                    ):
                        raise TypeError
                    self._to_update(sha1, raw)
                except (KeyError, TypeError, ValueError):
                    raise CorruptJsonError(
                        f"schéma JSON inattendu : {self._path}"
                    ) from None
        return data

    @staticmethod
    def _to_update(sha1: str, raw: dict) -> ModUpdate:
        return ModUpdate(
            sha1=sha1,
            known=bool(raw["known"]),
            project_url=str(raw.get("project_url", "")),
            installed_version=str(raw.get("installed_version", "")),
            latest_version=str(raw.get("latest_version", "")),
            update_available=bool(raw.get("update_available", False)),
            checked_at=(
                datetime.fromisoformat(raw["checked_at"])
                if raw.get("checked_at")
                else None
            ),
        )

    def statuses(self) -> dict[str, ModUpdate]:
        out: dict[str, ModUpdate] = {}
        for sha1, raw in (self._load().get("checks") or {}).items():
            try:
                out[sha1] = self._to_update(sha1, raw)
            except (KeyError, TypeError, ValueError):
                continue  # entrée corrompue : sera revérifiée
        return out

    def checked_at(self) -> datetime | None:
        raw = self._load().get("checked_at")
        try:
            return datetime.fromisoformat(raw) if raw else None
        except (TypeError, ValueError):
            return None

    def replace_all(self, checks: dict[str, ModUpdate], at: datetime) -> None:
        with self._lock:
            self._load(strict=True)
            payload = {
                "checked_at": at.isoformat(),
                "checks": {
                    sha1: {
                        "known": chk.known,
                        "project_url": chk.project_url,
                        "installed_version": chk.installed_version,
                        "latest_version": chk.latest_version,
                        "update_available": chk.update_available,
                        "checked_at": chk.checked_at.isoformat() if chk.checked_at else None,
                    }
                    for sha1, chk in checks.items()
                },
            }
            atomic_write_json(
                self._path,
                payload,
                ensure_ascii=False,
                indent=2,
            )
