"""Tests de FileMods (vrais .jar temporaires) et de la page /mods."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import zipfile

from adapters.mods import FileMods


def _make_jar(path, manifest=None, name="fabric.mod.json"):
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        if manifest is not None:
            jar.writestr(name, json.dumps(manifest))


class TestFileMods(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.adapter = FileMods(self.dir)

    def test_reads_fabric_metadata(self):
        _make_jar(os.path.join(self.dir, "lithium-0.22.1.jar"), {
            "id": "lithium", "name": "Lithium", "version": "0.22.1",
            "description": "Optimisations générales.",
        })
        (mod,) = self.adapter.list_mods()
        self.assertEqual((mod.name, mod.version), ("Lithium", "0.22.1"))
        self.assertIn("Optimisations", mod.description)
        self.assertTrue(mod.enabled)

    def test_unreadable_jar_falls_back_to_filename(self):
        with open(os.path.join(self.dir, "casse.jar"), "wb") as fh:
            fh.write(b"pas un zip")
        _make_jar(os.path.join(self.dir, "sans-manifeste.jar"))
        mods = self.adapter.list_mods()
        self.assertEqual([m.filename for m in mods], ["casse.jar", "sans-manifeste.jar"])
        self.assertTrue(all(m.name == "" for m in mods))

    def test_disabled_jar_listed_as_disabled(self):
        _make_jar(os.path.join(self.dir, "vieux.jar.disabled"), {"name": "Vieux", "version": "1"})
        (mod,) = self.adapter.list_mods()
        self.assertFalse(mod.enabled)

    def test_non_jar_files_ignored_and_missing_dir_degrades(self):
        with open(os.path.join(self.dir, "notes.txt"), "w") as fh:
            fh.write("x")
        self.assertEqual(self.adapter.list_mods(), [])
        self.assertEqual(FileMods("/nonexistent/mods").list_mods(), [])

    def test_sorted_by_display_name(self):
        _make_jar(os.path.join(self.dir, "zzz.jar"), {"name": "Alpha", "version": "1"})
        _make_jar(os.path.join(self.dir, "aaa.jar"), {"name": "Zêta", "version": "1"})
        self.assertEqual([m.name for m in self.adapter.list_mods()], ["Alpha", "Zêta"])


if __name__ == "__main__":
    unittest.main()
