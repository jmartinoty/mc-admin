"""Tests de l'adapter FileBackupArchives."""
from __future__ import annotations

import os
import tempfile
import unittest

from adapters.backup_archives import FileBackupArchives


class TestFileBackupArchives(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = self._dir.name

    def tearDown(self):
        self._dir.cleanup()

    def _touch(self, name, size=100, mtime=None):
        full = os.path.join(self.path, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(b"0" * size)
        if mtime is not None:
            os.utime(full, (mtime, mtime))

    def test_missing_directory_returns_empty(self):
        self.assertEqual(FileBackupArchives("/nonexistent/dir").list_archives(), [])

    def test_empty_directory_returns_empty(self):
        self.assertEqual(FileBackupArchives(self.path).list_archives(), [])

    def test_lists_files_with_size_and_date(self):
        self._touch("world-1.tar.gz", size=500, mtime=1751328000)  # 2025-07-01
        archives = FileBackupArchives(self.path).list_archives()
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].filename, "world-1.tar.gz")
        self.assertEqual(archives[0].size_bytes, 500)

    def test_sorted_most_recent_first(self):
        self._touch("old.tar.gz", mtime=1000)
        self._touch("new.tar.gz", mtime=2000)
        archives = FileBackupArchives(self.path).list_archives()
        self.assertEqual([a.filename for a in archives], ["new.tar.gz", "old.tar.gz"])

    def test_subdirectories_are_ignored(self):
        os.mkdir(os.path.join(self.path, "subdir"))
        self._touch("archive.tar.gz")
        archives = FileBackupArchives(self.path).list_archives()
        self.assertEqual([a.filename for a in archives], ["archive.tar.gz"])

    def test_non_archive_files_ignored(self):
        self._touch(".mc-backup-lock", size=0)  # fichier de verrou de mc-backup
        self._touch("world.tar.gz")
        archives = FileBackupArchives(self.path).list_archives()
        self.assertEqual([a.filename for a in archives], ["world.tar.gz"])

    def test_archive_path_returns_existing_archive(self):
        self._touch("world.tar.gz")
        path = FileBackupArchives(self.path).archive_path("world.tar.gz")
        self.assertIsNotNone(path)
        self.assertEqual(path.name, "world.tar.gz")

    def test_lists_and_resolves_archives_in_subdirectories(self):
        self._touch("scheduled/world.tar.gz")
        archives = FileBackupArchives(self.path).list_archives()
        self.assertEqual([a.filename for a in archives], ["scheduled/world.tar.gz"])
        path = FileBackupArchives(self.path).archive_path("scheduled/world.tar.gz")
        self.assertIsNotNone(path)
        self.assertEqual(path.name, "world.tar.gz")

    def test_archive_path_rejects_traversal_and_non_archives(self):
        self._touch("world.tar.gz")
        adapter = FileBackupArchives(self.path)
        self.assertIsNone(adapter.archive_path("../world.tar.gz"))
        self.assertIsNone(adapter.archive_path("notes.txt"))
        self.assertIsNone(adapter.archive_path("missing.tar.gz"))


if __name__ == "__main__":
    unittest.main()
