"""Adapter TempBanPort : suivi persistant des bans temporisés.

Vanilla Minecraft n'a AUCUNE durée native sur `/ban` (confirmé — cf.
CLAUDE.md §8) : mc-admin planifie lui-même la levée automatique. Un simple
fichier JSON (volume `/data`, déjà lecture/écriture) suffit — pas de nouvelle
techno, cohérent avec l'échelle du projet (cf. `scheduler.py`).

Persisté (pas seulement en mémoire) : un redémarrage de mc-admin ne doit
jamais perdre la trace d'un ban temporisé en cours.
"""
from __future__ import annotations

from contextlib import suppress
from datetime import datetime

from adapters.atomic_json import (
    CorruptJsonError,
    atomic_write_json,
    load_json,
    shared_path_lock,
)
from domain.model import PendingUnban


class JsonTempBans:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)

    def _read(self, *, strict: bool = False) -> list[dict]:
        entries = load_json(
            self._path,
            expected_type=list,
            default_factory=list,
            strict=strict,
        )
        if all(isinstance(entry, dict) for entry in entries):
            return entries
        if strict:
            raise CorruptJsonError(
                f"entrée de bannissement temporaire invalide : {self._path}"
            )
        return [entry for entry in entries if isinstance(entry, dict)]

    def _write(self, entries: list[dict]) -> None:
        atomic_write_json(self._path, entries)

    def schedule(self, player: str, unban_at: datetime) -> None:
        with self._lock:
            entries = [e for e in self._read(strict=True) if e.get("player") != player]
            entries.append({"player": player, "unban_at": unban_at.isoformat()})
            self._write(entries)

    def cancel(self, player: str) -> None:
        with self._lock:
            entries = [e for e in self._read(strict=True) if e.get("player") != player]
            self._write(entries)

    def pending(self) -> list[PendingUnban]:
        result = []
        for e in self._read():
            with suppress(KeyError, TypeError, ValueError):
                result.append(PendingUnban(player=e["player"], unban_at=datetime.fromisoformat(e["unban_at"])))
        return result

    def due(self, now: datetime) -> list[PendingUnban]:
        return [p for p in self.pending() if p.unban_at <= now]
