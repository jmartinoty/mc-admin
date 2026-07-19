"""Adapter BackupProfilesPort : profils de sauvegarde persistés en JSON (V6).

Même famille que backup_schedule/pending_unbans : un fichier dans /data,
un seul process écrivain (contrainte process unique documentée). La
MIGRATION du monde d'avant (deux conteneurs figés manual/scheduled) est
faite ici, au premier chargement : deux profils équivalents sont créés,
l'ancien backup_schedule.json est absorbé par le profil « Automatiques ».

La traduction catégories -> motifs tar (INCLUDES/EXCLUDES) vit aussi ici :
les catégories sont une liste FERMÉE, l'UI ne fournit jamais de motif libre.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from adapters.atomic_json import (
    CorruptJsonError,
    atomic_write_json,
    load_json,
    shared_path_lock,
)
from domain.model import BackupProfile

# Catégories proposées par l'UI -> motifs tar relatifs au dossier serveur.
# `world` est résolu dynamiquement (le nom du monde varie selon le serveur).
# NB : « mods_jars » est un alias hérité de « mods » (accepté en lecture,
# plus proposé par l'UI). Sur Fabric, les RÉGLAGES des mods vivent dans
# config/ (catégorie « config ») — mods/ ne contient que les .jar.
CATEGORY_LABELS = {
    "world": "Monde",
    "mods": "Mods",
    "config": "Configurations",
    "admin": "Listes & joueurs",
    "all": "Tout le serveur",
}

_ADMIN_FILES = "ops.json,whitelist.json,banned-players.json,banned-ips.json,usercache.json"
# On sauvegarde EN ENTIER ce qui est coché (les .jar des mods compris — un
# « tout le serveur » doit permettre une reconstruction totale) ; seuls le
# cache et les logs, régénérables et volumineux, sont écartés.
_DEFAULT_EXCLUDES = "cache,logs"

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower().strip()).strip("-")
    return slug[:32] or "profil"


def includes_for(categories: tuple[str, ...], world_dir: str) -> tuple[str, str]:
    """(INCLUDES, EXCLUDES) pour l'image mc-backup, à partir des catégories."""
    if "all" in categories:
        includes = "."
    else:
        parts: list[str] = []
        if "world" in categories:
            parts.append(world_dir)
        if "mods" in categories or "mods_jars" in categories:
            parts.append("mods")
        if "config" in categories:
            parts.extend(["config", "server.properties"])
        if "admin" in categories:
            parts.extend(_ADMIN_FILES.split(","))
        includes = ",".join(parts) or "."
    return includes, _DEFAULT_EXCLUDES


class JsonBackupProfiles:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)

    # ---- persistance ----

    @staticmethod
    def _validate_raw(raw: dict) -> None:
        if (
            not isinstance(raw.get("id"), str)
            or not raw["id"]
            or not isinstance(raw.get("name", raw["id"]), str)
            or not isinstance(raw.get("include", ["all"]), list)
            or any(
                not isinstance(category, str)
                for category in raw.get("include", ["all"])
            )
            or not isinstance(
                raw.get("dest", f"profiles/{raw['id']}"),
                str,
            )
            or not (
                isinstance(raw.get("hours", 0), (int, float))
                and not isinstance(raw.get("hours", 0), bool)
            )
            or (
                raw.get("daily_at") is not None
                and not isinstance(raw.get("daily_at"), str)
            )
            or not (
                isinstance(raw.get("retention_daily_days", 0), int)
                and not isinstance(raw.get("retention_daily_days", 0), bool)
            )
            or not (
                isinstance(raw.get("retention_monthly_months", 0), int)
                and not isinstance(raw.get("retention_monthly_months", 0), bool)
            )
            or not isinstance(raw.get("enabled", True), bool)
            or (
                raw.get("last_run") is not None
                and not isinstance(raw.get("last_run"), str)
            )
        ):
            raise TypeError

    def _load(self, *, strict: bool = False) -> list[dict]:
        data = load_json(
            self._path,
            expected_type=dict,
            default_factory=dict,
            strict=strict,
        )
        profiles = data.get("profiles", [])
        if not isinstance(profiles, list):
            if strict:
                raise CorruptJsonError(
                    f"schéma JSON inattendu : {self._path}"
                )
            return []
        valid: list[dict] = []
        seen: set[str] = set()
        for raw in profiles:
            try:
                if not isinstance(raw, dict):
                    raise TypeError
                self._validate_raw(raw)
                self._to_profile(raw)
                if strict and raw["id"] in seen:
                    raise TypeError
            except (KeyError, TypeError, ValueError):
                if strict:
                    raise CorruptJsonError(
                        f"schéma JSON inattendu : {self._path}"
                    ) from None
                continue
            seen.add(raw["id"])
            valid.append(raw)
        return valid

    def _write(self, profiles: list[dict]) -> None:
        atomic_write_json(
            self._path,
            {"profiles": profiles},
            ensure_ascii=False,
            indent=2,
        )

    @staticmethod
    def _to_profile(raw: dict) -> BackupProfile:
        last_run = raw.get("last_run")
        return BackupProfile(
            id=raw["id"],
            name=raw.get("name", raw["id"]),
            include=tuple(raw.get("include", ("all",))),
            dest=raw.get("dest", f"profiles/{raw['id']}"),
            hours=float(raw.get("hours", 0)),
            daily_at=raw.get("daily_at"),
            retention_daily_days=int(raw.get("retention_daily_days", 0)),
            retention_monthly_months=int(raw.get("retention_monthly_months", 0)),
            enabled=bool(raw.get("enabled", True)),
            last_run=datetime.fromisoformat(last_run) if last_run else None,
        )

    @staticmethod
    def _to_raw(profile: BackupProfile) -> dict:
        return {
            "id": profile.id,
            "name": profile.name,
            "include": list(profile.include),
            "dest": profile.dest,
            "hours": profile.hours,
            "daily_at": profile.daily_at,
            "retention_daily_days": profile.retention_daily_days,
            "retention_monthly_months": profile.retention_monthly_months,
            "enabled": profile.enabled,
            "last_run": profile.last_run.isoformat() if profile.last_run else None,
        }

    # ---- port ----

    def list_profiles(self) -> list[BackupProfile]:
        return [self._to_profile(raw) for raw in self._load()]

    def get(self, profile_id: str) -> BackupProfile | None:
        return next((p for p in self.list_profiles() if p.id == profile_id), None)

    def upsert(self, profile: BackupProfile) -> None:
        with self._lock:
            profiles = self._load(strict=True)
            raw = self._to_raw(profile)
            for i, existing in enumerate(profiles):
                if existing["id"] == profile.id:
                    profiles[i] = raw
                    break
            else:
                profiles.append(raw)
            self._write(profiles)

    def delete(self, profile_id: str) -> bool:
        with self._lock:
            profiles = self._load(strict=True)
            kept = [p for p in profiles if p["id"] != profile_id]
            if len(kept) == len(profiles):
                return False
            self._write(kept)
            return True

    def mark_run(self, profile_id: str, at: datetime) -> None:
        with self._lock:
            profiles = self._load(strict=True)
            for raw in profiles:
                if raw["id"] == profile_id:
                    raw["last_run"] = at.isoformat()
                    break
            self._write(profiles)

    # ---- migration depuis le monde d'avant (V5) ----

    def seed_from_legacy(self, legacy_schedule) -> bool:
        """Crée les profils « Manuelles » et « Automatiques » si le fichier
        n'existe pas encore, en absorbant la planification V5. Idempotent."""
        with self._lock:
            if os.path.exists(self._path):
                return False
            schedule = legacy_schedule.get() if legacy_schedule is not None else None
            self._write([
                self._to_raw(BackupProfile(
                    id="manual", name="Manuelles", include=("all",), dest="manual",
                )),
                self._to_raw(BackupProfile(
                    id="auto", name="Automatiques", include=("all",), dest="scheduled",
                    hours=getattr(schedule, "hours", 0) or 0,
                    daily_at=getattr(schedule, "daily_at", None),
                    retention_daily_days=30, retention_monthly_months=12,
                    last_run=getattr(schedule, "last_run", None),
                )),
            ])
            return True
