"""Adapter ScheduledMaintenancePort : liste des maintenances programmées.

Fichier JSON persistant (/data). Deux formes d'entrée (date précise `once` ou
jours de semaine `weekly`), plus l'état « dernier jour armé » par entrée
(`fired`) pour n'armer qu'une fois par jour. Persistant comme le redémarrage
récurrent : la liste survit au redémarrage de mc-admin (contrairement au
one-shot `InMemoryPendingMaintenance`, volontairement volatil).
"""
from __future__ import annotations

import secrets

from adapters.atomic_json import atomic_write_json, load_json, shared_path_lock
from domain.model import ScheduledMaintenance


def _clean_weekdays(raw) -> tuple[int, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    days = {int(d) for d in raw if isinstance(d, (int, float)) and 0 <= int(d) <= 6}
    return tuple(sorted(days))


class JsonScheduledMaintenance:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)

    def _read(self, *, strict: bool = False) -> dict:
        return load_json(
            self._path, expected_type=dict, default_factory=dict, strict=strict
        )

    def _write(self, data: dict) -> None:
        atomic_write_json(self._path, data)

    def list(self) -> list[ScheduledMaintenance]:
        data = self._read()
        entries = []
        for raw in data.get("entries") or []:
            if not isinstance(raw, dict):
                continue
            kind = raw.get("kind")
            time_hhmm = raw.get("time")
            entry_id = raw.get("id")
            if kind not in ("once", "weekly") or not isinstance(time_hhmm, str) or not entry_id:
                continue
            lead = raw.get("lead_seconds")
            entries.append(
                ScheduledMaintenance(
                    id=str(entry_id),
                    kind=kind,
                    time_hhmm=time_hhmm,
                    date=str(raw.get("date") or ""),
                    weekdays=_clean_weekdays(raw.get("weekdays")),
                    lead_seconds=lead if isinstance(lead, int) and lead > 0 else 300,
                    message=str(raw.get("message") or ""),
                    until=str(raw.get("until") or ""),
                    defer_if_players=bool(raw.get("defer_if_players", False)),
                )
            )
        return entries

    def add(self, entry: ScheduledMaintenance) -> str:
        entry_id = entry.id or secrets.token_hex(8)
        with self._lock:
            data = self._read(strict=True)
            entries = data.get("entries")
            if not isinstance(entries, list):
                entries = []
            entries.append({
                "id": entry_id,
                "kind": entry.kind,
                "time": entry.time_hhmm,
                "date": entry.date,
                "weekdays": list(entry.weekdays),
                "lead_seconds": entry.lead_seconds,
                "message": entry.message,
                "until": entry.until,
                "defer_if_players": entry.defer_if_players,
            })
            data["entries"] = entries
            self._write(data)
        return entry_id

    def remove(self, entry_id: str) -> bool:
        with self._lock:
            data = self._read(strict=True)
            entries = data.get("entries") or []
            kept = [e for e in entries if isinstance(e, dict) and e.get("id") != entry_id]
            had = len(kept) != len(entries)
            data["entries"] = kept
            fired = data.get("fired")
            if isinstance(fired, dict):
                fired.pop(entry_id, None)
            if had:
                self._write(data)
            return had

    def last_fired(self, entry_id: str) -> str | None:
        fired = self._read().get("fired")
        if not isinstance(fired, dict):
            return None
        day = fired.get(entry_id)
        return day if isinstance(day, str) and day else None

    def mark_fired(self, entry_id: str, day: str) -> None:
        with self._lock:
            data = self._read(strict=True)
            fired = data.get("fired")
            if not isinstance(fired, dict):
                fired = {}
            fired[entry_id] = day
            data["fired"] = fired
            self._write(data)
