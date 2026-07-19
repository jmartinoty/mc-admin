"""Tests des garanties communes des petits stores JSON."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from adapters.atomic_json import CorruptJsonError, atomic_write_json, load_json


class TestAtomicWriteJson(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "state.json")

    def test_failed_replace_preserves_previous_file_and_cleans_temp(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"version": "old"}, fh)

        with (
            patch("adapters.atomic_json.os.replace", side_effect=OSError("disk error")),
            self.assertRaises(OSError),
        ):
            atomic_write_json(self.path, {"version": "new"})

        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"version": "old"})
        self.assertEqual(os.listdir(self.directory.name), ["state.json"])

    def test_existing_permissions_are_preserved(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({}, fh)
        os.chmod(self.path, 0o640)

        atomic_write_json(self.path, {"ok": True})

        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)

    def test_new_file_is_private(self):
        atomic_write_json(self.path, {"secret": True})

        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_read_error_is_tolerated_but_strict_read_reports_corruption(self):
        with patch("builtins.open", side_effect=PermissionError("denied")):
            self.assertEqual(
                load_json(
                    self.path,
                    expected_type=dict,
                    default_factory=dict,
                ),
                {},
            )
            with self.assertRaises(CorruptJsonError):
                load_json(
                    self.path,
                    expected_type=dict,
                    default_factory=dict,
                    strict=True,
                )


if __name__ == "__main__":
    unittest.main()
