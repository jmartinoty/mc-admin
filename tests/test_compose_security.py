"""Contrats de sécurité statiques des fichiers Docker Compose."""
from __future__ import annotations

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class BlueMapComposeSecurityTests(unittest.TestCase):
    def test_bluemap_is_internal_and_reproducible(self) -> None:
        for filename in ("docker-compose.yml", "docker-compose.example.yml"):
            with self.subTest(filename=filename):
                compose = yaml.safe_load((ROOT / filename).read_text())
                services = compose["services"]
                bluemap = services["bluemap"]

                self.assertNotIn("ports", bluemap)
                self.assertEqual(bluemap["restart"], "unless-stopped")
                self.assertIn("@sha256:", bluemap["image"])
                self.assertIn("mc_map_internal", bluemap["networks"])
                self.assertIn("mc_map_internal", services["mc-admin"]["networks"])
                self.assertNotIn("internal", compose["networks"]["mc_map_internal"])
                self.assertTrue(
                    any(str(volume).endswith(":/app/world:ro")
                        for volume in bluemap["volumes"])
                )


if __name__ == "__main__":
    unittest.main()
