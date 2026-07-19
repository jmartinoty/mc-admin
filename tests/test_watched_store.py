"""Tests de l'adapter JsonWatched (A2.1) : persistance + amorçage one-shot."""
from __future__ import annotations

import os
import tempfile
import unittest

from adapters.atomic_json import CorruptJsonError
from adapters.watched_store import JsonWatched


class TestJsonWatched(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)
        self.store = JsonWatched(os.path.join(self.dir, "watched.json"))

    def test_roundtrip_add_remove(self):
        self.assertEqual(self.store.all(), [])
        self.assertTrue(self.store.add("playit"))
        self.assertFalse(self.store.add("playit"))          # doublon refusé
        self.assertEqual(self.store.all(), ["playit"])
        self.assertTrue(self.store.remove("playit"))
        self.assertFalse(self.store.remove("playit"))
        self.assertEqual(self.store.all(), [])

    def test_env_import_is_one_shot(self):
        self.assertEqual(self.store.import_from_env(["playit", "frp"]), ["playit", "frp"])
        self.store.remove("frp")                            # retiré dans l'UI
        self.assertEqual(self.store.import_from_env(["playit", "frp"]), [])  # ne ressuscite pas
        self.assertEqual(self.store.all(), ["playit"])

    def test_empty_env_on_fresh_install_writes_nothing(self):
        self.assertEqual(self.store.import_from_env([]), [])
        self.assertFalse(os.path.exists(self.store._path))  # pas de fichier fantôme
        self.assertEqual(self.store.import_from_env(["playit"]), ["playit"])  # amorçage ultérieur OK

    def test_corrupt_file_degrades_for_reads_but_blocks_mutation(self):
        with open(self.store._path, "w", encoding="utf-8") as fh:
            fh.write("{pas du json")
        self.assertEqual(self.store.all(), [])
        with self.assertRaises(CorruptJsonError):
            self.store.add("playit")
        with open(self.store._path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{pas du json")


if __name__ == "__main__":
    unittest.main()
