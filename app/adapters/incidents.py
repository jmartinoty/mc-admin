"""Adapter IncidentLogPort : historique des incidents persisté en JSON.

Même famille que les autres états de /data (archive_checks, storage_history) :
les WATCHERS écrivent (open/close aux transitions qu'ils détectent déjà — c'est
de l'observation passive, pas une action utilisateur, donc PAS via AdminService,
comme PlayerLogWatcher), le SERVICE lit (barrière STATUS). Fichier absent ou
corrompu = aucun incident, jamais d'erreur.

L'adapter HORODATE lui-même (clock injectable) : le watcher dit seulement
« sujet X down/rétabli maintenant », il n'a pas à porter d'horloge. `open` est
idempotent par sujet — une chute déjà ouverte n'en crée pas une deuxième, et
un `close` sans incident ouvert est un no-op (rétablissement d'un serveur qui
n'avait jamais été déclaré down, ex. juste après un démarrage de mc-admin).
"""
from __future__ import annotations

from datetime import datetime, timezone

from adapters.atomic_json import atomic_write_json, load_json, shared_path_lock
from domain.model import Incident

# Borne l'historique conservé sur disque : au-delà, les plus anciens tombent.
_HISTORY_CAP = 500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JsonIncidents:
    def __init__(self, path: str, clock=_utcnow) -> None:
        self._path = path
        self._lock = shared_path_lock(path)
        self._clock = clock

    def _load(self, *, strict: bool = False) -> dict:
        data = load_json(
            self._path, expected_type=dict, default_factory=dict, strict=strict
        )
        # Schéma : {"open": {subject: record}, "history": [record, ...]}.
        open_map = data.get("open")
        history = data.get("history")
        return {
            "open": open_map if isinstance(open_map, dict) else {},
            "history": history if isinstance(history, list) else [],
        }

    def open(self, subject: str, kind: str, label: str, detail: str = "") -> None:
        with self._lock:
            data = self._load(strict=True)
            if subject in data["open"]:
                return  # incident déjà en cours pour ce sujet : idempotent
            now = self._clock()
            data["open"][subject] = {
                "id": f"{subject}:{int(now.timestamp())}",
                "subject": subject,
                "kind": kind,
                "label": label,
                "started_at": now.isoformat(),
                "detail": detail,
            }
            atomic_write_json(self._path, data)

    def close(self, subject: str, detail: str = "") -> None:
        with self._lock:
            data = self._load(strict=True)
            record = data["open"].pop(subject, None)
            if record is None:
                return  # rien d'ouvert : rétablissement sans chute déclarée
            record["ended_at"] = self._clock().isoformat()
            if detail:
                record["detail"] = detail
            data["history"].append(record)
            data["history"] = data["history"][-_HISTORY_CAP:]
            atomic_write_json(self._path, data)

    def recent(self, limit: int = 100) -> list[Incident]:
        data = self._load()
        records = list(data["open"].values()) + list(data["history"])
        incidents = []
        for record in records:
            parsed = _parse(record)
            if parsed is not None:
                incidents.append(parsed)
        incidents.sort(key=lambda inc: inc.started_at, reverse=True)
        return incidents[:limit]


def _parse(record) -> Incident | None:
    if not isinstance(record, dict):
        return None
    started = record.get("started_at")
    if not isinstance(started, str):
        return None
    try:
        started_at = datetime.fromisoformat(started)
    except ValueError:
        return None
    ended_raw = record.get("ended_at")
    ended_at = None
    if isinstance(ended_raw, str):
        try:
            ended_at = datetime.fromisoformat(ended_raw)
        except ValueError:
            ended_at = None
    return Incident(
        id=str(record.get("id") or ""),
        subject=str(record.get("subject") or ""),
        kind=str(record.get("kind") or ""),
        label=str(record.get("label") or ""),
        started_at=started_at,
        ended_at=ended_at,
        detail=str(record.get("detail") or ""),
    )
