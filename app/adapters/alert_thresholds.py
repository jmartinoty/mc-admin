"""Adapter AlertThresholdsPort : seuils d'alerte de PerfWatcher, réglables
dans l'UI (backlog fiabilité n° 4).

Même pattern que JsonRecurringRestart (config simple en JSON) : `get()` ne
retourne JAMAIS None — un fichier absent/corrompu retombe sur les défauts du
domaine (mêmes valeurs que le comportement historique câblé en dur), la
validation des nouvelles valeurs vit dans le SERVICE, pas ici.
"""
from __future__ import annotations

from adapters.atomic_json import atomic_write_json, load_json, shared_path_lock
from domain.model import AlertThresholds


class JsonAlertThresholds:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)

    def _read(self, *, strict: bool = False) -> dict:
        return load_json(self._path, expected_type=dict, default_factory=dict, strict=strict)

    def get(self) -> AlertThresholds:
        data = self._read()
        defaults = AlertThresholds()
        try:
            return AlertThresholds(
                mspt_threshold_ms=float(data.get("mspt_threshold_ms", defaults.mspt_threshold_ms)),
                disk_min_free_gib=float(data.get("disk_min_free_gib", defaults.disk_min_free_gib)),
                mspt_sustained_minutes=float(
                    data.get("mspt_sustained_minutes", defaults.mspt_sustained_minutes)
                ),
            )
        except (TypeError, ValueError):
            return defaults  # champ corrompu : défauts plutôt qu'une exception

    def set(self, thresholds: AlertThresholds) -> None:
        with self._lock:
            self._read(strict=True)
            atomic_write_json(self._path, {
                "mspt_threshold_ms": thresholds.mspt_threshold_ms,
                "disk_min_free_gib": thresholds.disk_min_free_gib,
                "mspt_sustained_minutes": thresholds.mspt_sustained_minutes,
            })
