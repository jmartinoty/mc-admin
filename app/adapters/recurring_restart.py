"""Adapter RecurringRestartPort : config du redémarrage quotidien récurrent.

Fichier JSON persistant (/data), même pattern que JsonTempBans : la config ET
le dernier jour déclenché survivent au redémarrage de mc-admin (contrairement
au one-shot InMemoryRestartSchedule, volontairement volatil).
"""
from __future__ import annotations

from adapters.atomic_json import atomic_write_json, load_json, shared_path_lock
from domain.model import RecurringRestart


class JsonRecurringRestart:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)

    def _read(self, *, strict: bool = False) -> dict:
        return load_json(
            self._path,
            expected_type=dict,
            default_factory=dict,
            strict=strict,
        )

    def _write(self, data: dict) -> None:
        atomic_write_json(self._path, data)

    def get(self) -> RecurringRestart | None:
        data = self._read()
        time_hhmm = data.get("time")
        if not isinstance(time_hhmm, str) or not time_hhmm:
            return None
        lead = data.get("lead_seconds")
        return RecurringRestart(
            time_hhmm=time_hhmm,
            lead_seconds=lead if isinstance(lead, int) and lead > 0 else 300,
            # Absent d'une config écrite avant cette option -> False (migration
            # douce, aucun redémarrage historique n'est modifié).
            defer_if_players=bool(data.get("defer_if_players", False)),
        )

    def set_config(self, config: RecurringRestart) -> None:
        with self._lock:
            data = self._read(strict=True)
            data["time"] = config.time_hhmm
            data["lead_seconds"] = config.lead_seconds
            data["defer_if_players"] = config.defer_if_players
            data.pop("last_fired", None)  # nouvelle config -> repart de zéro
            self._write(data)

    def clear(self) -> None:
        with self._lock:
            self._read(strict=True)
            self._write({})

    def last_fired(self) -> str | None:
        day = self._read().get("last_fired")
        return day if isinstance(day, str) and day else None

    def mark_fired(self, day: str) -> None:
        with self._lock:
            data = self._read(strict=True)
            data["last_fired"] = day
            self._write(data)
