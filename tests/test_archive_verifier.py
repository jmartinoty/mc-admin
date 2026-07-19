"""Tests d'ArchiveVerifier + JsonArchiveChecks (vraies archives temporaires)."""
from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import unittest
from io import BytesIO
from datetime import datetime, timedelta, timezone

from adapters.archive_checks import JsonArchiveChecks
from adapters.archive_validation import inspect_archive
from adapters.backup_archives import FileBackupArchives
from archive_verifier import ArchiveVerifier, verify_archive

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


class TestArchiveVerifier(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        os.makedirs(os.path.join(self.dir, "manual"))
        self.archives = FileBackupArchives(self.dir)
        self.checks = JsonArchiveChecks(os.path.join(self.dir, "checks.json"))
        self.verifier = ArchiveVerifier(self.archives, self.checks, clock=lambda: NOW)

    def _make_archive(self, name, corrupt=False, age_seconds=600, with_world=True):
        path = os.path.join(self.dir, "manual", name)
        with tarfile.open(path, "w:gz") as tf:
            content = os.path.join(self.dir, "world.dat")
            with open(content, "wb") as fh:
                fh.write(b"x" * 4096)
            if with_world:
                tf.add(content, arcname="old_world_1/level.dat")
                properties = os.path.join(self.dir, "server.properties")
                with open(properties, "w", encoding="utf-8") as fh:
                    fh.write("level-name=old_world_1\n")
                tf.add(properties, arcname="server.properties")
            else:
                tf.add(content, arcname="mods/example.jar")
        if corrupt:
            with open(path, "r+b") as fh:
                fh.seek(120)
                fh.write(b"\x00" * 64)  # corruption au milieu du flux gzip
        mtime = (NOW - timedelta(seconds=age_seconds)).timestamp()
        os.utime(path, (mtime, mtime))
        return path

    def test_verify_archive_ok_and_corrupt(self):
        ok, detail = verify_archive(self._make_archive("bonne.tar.gz"))
        self.assertTrue(ok)
        ok, detail = verify_archive(self._make_archive("cassee.tar.gz", corrupt=True))
        self.assertFalse(ok)
        self.assertTrue(detail)

    def test_inspection_identifies_declared_world(self):
        inspection = inspect_archive(self._make_archive("monde.tar.gz"))
        self.assertTrue(inspection.ok)
        self.assertTrue(inspection.restorable)
        self.assertEqual(inspection.world_name, "old_world_1")

    def test_integrity_does_not_make_a_mods_only_archive_restorable(self):
        inspection = inspect_archive(
            self._make_archive("mods-seuls.tar.gz", with_world=False)
        )
        self.assertTrue(inspection.ok)
        self.assertFalse(inspection.restorable)
        self.assertIn("server.properties", inspection.detail)

    def test_path_traversal_is_rejected(self):
        path = os.path.join(self.dir, "manual", "dangereuse.tar.gz")
        with tarfile.open(path, "w:gz") as tf:
            member = tarfile.TarInfo("../outside")
            member.size = 1
            tf.addfile(member, BytesIO(b"x"))
        inspection = inspect_archive(path)
        self.assertFalse(inspection.ok)
        self.assertIn("chemin dangereux", inspection.detail)

    def test_symbolic_link_is_rejected(self):
        path = os.path.join(self.dir, "manual", "lien.tar.gz")
        with tarfile.open(path, "w:gz") as tf:
            member = tarfile.TarInfo("world/link")
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc"
            tf.addfile(member)
        inspection = inspect_archive(path)
        self.assertFalse(inspection.ok)
        self.assertIn("lien interdit", inspection.detail)

    def test_restore_transaction_names_are_rejected(self):
        path = os.path.join(self.dir, "manual", "transaction.tar.gz")
        with tarfile.open(path, "w:gz") as tf:
            member = tarfile.TarInfo(".mc-restore-rollback/level.dat")
            member.size = 1
            tf.addfile(member, BytesIO(b"x"))
        inspection = inspect_archive(path)
        self.assertFalse(inspection.ok)
        self.assertIn("nom réservé", inspection.detail)

    def test_tick_records_verdicts_one_per_pass(self):
        self._make_archive("bonne.tar.gz")
        self._make_archive("cassee.tar.gz", corrupt=True)
        first = self.verifier.tick()   # une archive par passe (charge lissée)
        second = self.verifier.tick()
        self.assertIsNone(self.verifier.tick())  # tout est vérifié
        statuses = self.checks.statuses()
        self.assertEqual({first, second}, {"manual/bonne.tar.gz", "manual/cassee.tar.gz"})
        self.assertTrue(statuses["manual/bonne.tar.gz"].ok)
        self.assertTrue(statuses["manual/bonne.tar.gz"].restorable)
        self.assertEqual(statuses["manual/bonne.tar.gz"].world_name, "old_world_1")
        self.assertFalse(statuses["manual/cassee.tar.gz"].ok)

    def test_fresh_archive_is_skipped_until_min_age(self):
        # Une archive trop récente est peut-être encore en cours d'écriture.
        self._make_archive("toute-fraiche.tar.gz", age_seconds=30)
        self.assertIsNone(self.verifier.tick())
        self.assertEqual(self.checks.statuses(), {})

    def test_size_change_triggers_reverification(self):
        path = self._make_archive("bonne.tar.gz")
        self.verifier.tick()
        with open(path, "ab") as fh:
            fh.write(b"suite")
        mtime = (NOW - timedelta(seconds=600)).timestamp()
        os.utime(path, (mtime, mtime))
        self.assertEqual(self.verifier.tick(), "manual/bonne.tar.gz")

    def test_missing_archives_are_forgotten(self):
        path = self._make_archive("ephemere.tar.gz")
        self.verifier.tick()
        os.remove(path)
        self.verifier.tick()
        self.assertEqual(self.checks.statuses(), {})


if __name__ == "__main__":
    unittest.main()
