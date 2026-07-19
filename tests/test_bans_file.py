"""Tests de l'adapter FileBans (lecture de banned-players.json monté en RO)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import timezone

from adapters.bans_file import FileBans


class TestFileBans(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "banned-players.json")

    def tearDown(self):
        self._dir.cleanup()

    def _write(self, data):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_missing_file_returns_empty(self):
        self.assertEqual(FileBans(self.path).list_bans(), [])

    def test_parses_real_shape(self):
        self._write([
            {
                "uuid": "069a79f4-44e9-4726-a5be-fca90e38aaf5",
                "name": "griefer",
                "created": "2026-06-01 12:34:56 +0000",
                "source": "jeremy",
                "expires": "forever",
                "reason": "Griefing spawn",
            }
        ])
        entries = FileBans(self.path).list_bans()
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e.player, "griefer")
        self.assertEqual(e.reason, "Griefing spawn")
        self.assertEqual(e.expires, "forever")
        self.assertEqual(e.created_at.year, 2026)
        self.assertEqual(e.created_at.tzinfo, timezone.utc)

    def test_empty_array(self):
        self._write([])
        self.assertEqual(FileBans(self.path).list_bans(), [])

    def test_malformed_json_degrades_to_empty(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        self.assertEqual(FileBans(self.path).list_bans(), [])

    def test_unexpected_shape_degrades_to_empty(self):
        self._write({"not": "a list"})
        self.assertEqual(FileBans(self.path).list_bans(), [])

    def test_entries_without_name_are_skipped(self):
        self._write([{"uuid": "x"}, {"name": "Valid", "reason": "r"}])
        entries = FileBans(self.path).list_bans()
        self.assertEqual([e.player for e in entries], ["Valid"])

    def test_missing_optional_fields_degrade_gracefully(self):
        self._write([{"name": "toto"}])  # pas de reason/created/expires
        e = FileBans(self.path).list_bans()[0]
        self.assertEqual(e.reason, "")
        self.assertIsNone(e.created_at)
        self.assertEqual(e.expires, "forever")

    def test_unparsable_created_date_degrades_to_none(self):
        self._write([{"name": "toto", "created": "not a date"}])
        self.assertIsNone(FileBans(self.path).list_bans()[0].created_at)

    def test_directory_instead_of_file_degrades_to_empty(self):
        os.makedirs(self.path)  # simule le piège Docker (bind mount d'un fichier absent)
        self.assertEqual(FileBans(self.path).list_bans(), [])


if __name__ == "__main__":
    unittest.main()
