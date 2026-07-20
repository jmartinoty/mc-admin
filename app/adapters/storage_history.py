"""Adapter StorageHistoryPort : historique quotidien de l'occupation des
sauvegardes, persisté en JSON (liste triée par date croissante).

Même famille que JsonArchiveChecks : un seul écrivain (StorageHistoryWatcher,
observation passive), le service lit. Fichier absent/corrompu = historique
vide, jamais d'erreur — la page /backups affiche juste « pas encore assez
d'historique », comme le graphe MSPT sans données.
"""
from __future__ import annotations

from adapters.atomic_json import CorruptJsonError, atomic_write_json, load_json, shared_path_lock
from domain.model import StorageSnapshot

# Bornée pour ne pas grossir indéfiniment (~3 ans à 1 point/jour) — largement
# assez pour une projection de saturation, qui ne regarde que les 30 derniers.
_MAX_POINTS = 1100


class JsonStorageHistory:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)

    def _load(self, *, strict: bool = False) -> list:
        data = load_json(self._path, expected_type=list, default_factory=list, strict=strict)
        if strict:
            for raw in data:
                if (
                    not isinstance(raw, dict)
                    or not isinstance(raw.get("date"), str)
                    or not raw["date"]
                    or not isinstance(raw.get("families"), dict)
                    or (raw.get("free_bytes") is not None and not isinstance(raw.get("free_bytes"), int))
                ):
                    raise CorruptJsonError(f"schéma JSON inattendu : {self._path}")
        return data

    @staticmethod
    def _to_snapshot(raw: dict) -> StorageSnapshot:
        families = {
            str(name): int(value)
            for name, value in raw.get("families", {}).items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        return StorageSnapshot(
            date=raw["date"],
            free_bytes=raw.get("free_bytes") if isinstance(raw.get("free_bytes"), int) else None,
            families=families,
        )

    def record_today(self, snapshot: StorageSnapshot) -> None:
        with self._lock:
            data = self._load(strict=True)
            data = [raw for raw in data if raw.get("date") != snapshot.date]
            data.append({
                "date": snapshot.date,
                "free_bytes": snapshot.free_bytes,
                "families": dict(snapshot.families),
            })
            data.sort(key=lambda raw: raw["date"])
            if len(data) > _MAX_POINTS:
                data = data[-_MAX_POINTS:]
            atomic_write_json(self._path, data, ensure_ascii=False, indent=2)

    def history(self, days: int = 30) -> list[StorageSnapshot]:
        snapshots: list[StorageSnapshot] = []
        for raw in self._load():
            try:
                snapshots.append(self._to_snapshot(raw))
            except (KeyError, TypeError, ValueError):
                continue  # entrée corrompue : ignorée, pas de raison de tout perdre
        snapshots.sort(key=lambda s: s.date)
        return snapshots[-days:] if days > 0 else snapshots
