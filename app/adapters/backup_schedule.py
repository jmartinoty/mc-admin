"""Adapter BackupSchedulePort : planification persistante des sauvegardes.

Fichier JSON dans /data (même pattern que JsonRecurringRestart) : l'intervalle
ET le dernier déclenchement survivent aux redéploiements — c'est le point :
l'ancien scheduler comptait depuis le démarrage du conteneur, donc en période
de dev (déploiements fréquents) l'intervalle n'était jamais atteint.
"""
from __future__ import annotations

from datetime import datetime

from adapters.atomic_json import (
    CorruptJsonError,
    atomic_write_json,
    load_json,
    shared_path_lock,
)
from domain.model import BackupSchedule


class JsonBackupSchedule:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)

    def _read(self, *, strict: bool = False) -> dict:
        data = load_json(
            self._path,
            expected_type=dict,
            default_factory=dict,
            strict=strict,
        )
        if strict:
            hours = data.get("hours")
            daily = data.get("daily_at")
            last_run = data.get("last_run")
            valid_hours = hours is None or (
                isinstance(hours, (int, float)) and not isinstance(hours, bool)
            )
            valid_daily = daily is None or isinstance(daily, str)
            valid_last_run = last_run is None or isinstance(last_run, str)
            if isinstance(last_run, str) and last_run:
                try:
                    datetime.fromisoformat(last_run)
                except ValueError:
                    valid_last_run = False
            if not (valid_hours and valid_daily and valid_last_run):
                raise CorruptJsonError(
                    f"schéma JSON inattendu : {self._path}"
                )
        return data

    def _write(self, data: dict) -> None:
        atomic_write_json(self._path, data)

    def exists(self) -> bool:
        """True si une config a déjà été écrite (≠ « désactivé ») — sert au
        seed one-shot depuis BACKUP_SCHEDULE_HOURS (compat env)."""
        return bool(self._read())

    def get(self) -> BackupSchedule:
        data = self._read()
        hours = data.get("hours")
        daily = data.get("daily_at")
        last_raw = data.get("last_run")
        last_run = None
        if isinstance(last_raw, str) and last_raw:
            try:
                last_run = datetime.fromisoformat(last_raw)
            except ValueError:
                last_run = None
        return BackupSchedule(
            hours=(
                float(hours)
                if isinstance(hours, (int, float))
                and not isinstance(hours, bool)
                and hours > 0
                else 0.0
            ),
            daily_at=daily if isinstance(daily, str) and daily else None,
            last_run=last_run,
        )

    def set_schedule(self, hours: float, daily_at: str | None) -> None:
        # Modes exclusifs : régler l'un remet l'autre à zéro.
        with self._lock:
            data = self._read(strict=True)
            data["hours"] = hours if not daily_at else 0.0
            data["daily_at"] = daily_at
            self._write(data)

    def mark_run(self, at: datetime) -> None:
        with self._lock:
            data = self._read(strict=True)
            data["last_run"] = at.isoformat()
            self._write(data)
