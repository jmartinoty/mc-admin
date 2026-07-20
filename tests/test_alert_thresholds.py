"""Tests de l'adapter AlertThresholdsPort (JsonAlertThresholds) — backlog
fiabilité n° 4."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adapters.alert_thresholds import JsonAlertThresholds
from domain.model import AlertThresholds


class TestJsonAlertThresholds(unittest.TestCase):
    def test_missing_file_returns_domain_defaults(self):
        store = JsonAlertThresholds("/nonexistent/alert_thresholds.json")
        self.assertEqual(store.get(), AlertThresholds())

    def test_set_then_get_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "alert_thresholds.json")
            store = JsonAlertThresholds(path)
            store.set(AlertThresholds(
                mspt_threshold_ms=80.0, disk_min_free_gib=25.0, mspt_sustained_minutes=5.0))
            self.assertEqual(
                store.get(),
                AlertThresholds(mspt_threshold_ms=80.0, disk_min_free_gib=25.0,
                                mspt_sustained_minutes=5.0),
            )

    def test_corrupt_field_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "alert_thresholds.json")
            Path(path).write_text('{"mspt_threshold_ms": "pas un nombre"}', encoding="utf-8")
            store = JsonAlertThresholds(path)
            self.assertEqual(store.get(), AlertThresholds())


if __name__ == "__main__":
    unittest.main()
