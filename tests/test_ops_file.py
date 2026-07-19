"""Tests de l'adapter OpsFile (lecture + écriture du niveau dans ops.json)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from adapters.ops_file import OpsFile
from domain.model import OpEntry


class TestOpsFileList(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "ops.json")

    def tearDown(self):
        self._dir.cleanup()

    def _write(self, data):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_missing_file_returns_empty(self):
        self.assertEqual(OpsFile(self.path).list_ops(), [])

    def test_parses_real_ops_json_shape(self):
        self._write([
            {"uuid": "6e5fc5ec-afcd-42e4-8ecf-9325a0f94670", "name": "Trappeur", "level": 4, "bypassesPlayerLimit": False},
            {"uuid": "ae397f97-bc0b-4b1a-ab99-c7183fb3863e", "name": "Rovandil", "level": 3, "bypassesPlayerLimit": False},
        ])
        self.assertEqual(
            OpsFile(self.path).list_ops(),
            [OpEntry(name="Trappeur", level=4), OpEntry(name="Rovandil", level=3)],
        )

    def test_empty_array(self):
        self._write([])
        self.assertEqual(OpsFile(self.path).list_ops(), [])

    def test_malformed_json_degrades_to_empty(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        self.assertEqual(OpsFile(self.path).list_ops(), [])

    def test_unexpected_shape_degrades_to_empty(self):
        self._write({"not": "a list"})
        self.assertEqual(OpsFile(self.path).list_ops(), [])

    def test_entries_without_name_are_skipped(self):
        self._write([{"uuid": "x"}, {"name": "Valid"}])
        self.assertEqual(OpsFile(self.path).list_ops(), [OpEntry(name="Valid", level=4)])

    def test_entry_missing_level_defaults_to_four(self):
        self._write([{"name": "Valid"}])
        self.assertEqual(OpsFile(self.path).list_ops(), [OpEntry(name="Valid", level=4)])

    def test_directory_instead_of_file_degrades_to_empty(self):
        os.makedirs(self.path)
        self.assertEqual(OpsFile(self.path).list_ops(), [])


if __name__ == "__main__":
    unittest.main()
