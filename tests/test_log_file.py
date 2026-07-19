"""Tests de l'adapter FileLog (tail d'un fichier de log monté en RO)."""
from __future__ import annotations

import os
import tempfile
import unittest

from adapters.log_file import FileLog


class TestFileLog(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "latest.log")

    def tearDown(self):
        self._dir.cleanup()

    def _write(self, lines):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def test_missing_file_returns_empty(self):
        self.assertEqual(FileLog(self.path).recent(), [])

    def test_returns_last_n_lines(self):
        self._write([f"line {i}" for i in range(5)])
        self.assertEqual(FileLog(self.path).recent(2), ["line 3", "line 4"])

    def test_more_requested_than_available_returns_all(self):
        self._write(["a", "b"])
        self.assertEqual(FileLog(self.path).recent(100), ["a", "b"])

    def test_zero_or_negative_returns_empty(self):
        self._write(["a", "b"])
        self.assertEqual(FileLog(self.path).recent(0), [])
        self.assertEqual(FileLog(self.path).recent(-3), [])

    def test_default_filter_drops_rcon_noise(self):
        self._write([
            "[11:34:36] [Server thread/INFO]: alice joined the game",
            "[11:34:36] [RCON Listener #2/INFO]: Thread RCON Client /172.29.0.4 started",
            "[11:34:36] [RCON Client /172.29.0.4 #114/INFO]: Thread RCON Client /172.29.0.4 shutting down",
            "[11:34:40] [Server thread/INFO]: alice left the game",
        ])
        got = FileLog(self.path).recent(10)
        self.assertEqual(len(got), 2)
        self.assertNotIn("RCON", "".join(got))

    def test_filter_still_returns_n_useful_lines(self):
        # 5 lignes utiles noyées dans du bruit : on veut N lignes UTILES, pas N-bruit.
        lines = []
        for i in range(5):
            lines.append(f"[INFO] utile {i}")
            lines.append("[RCON Listener #2/INFO]: Thread RCON Client /x started")
        self._write(lines)
        got = FileLog(self.path).recent(4)
        self.assertEqual(got, [f"[INFO] utile {i}" for i in range(1, 5)])

    def test_filter_disabled_keeps_everything(self):
        self._write([
            "[INFO] utile",
            "[RCON Listener #2/INFO]: Thread RCON Client /x started",
        ])
        got = FileLog(self.path, exclude=None).recent(10)
        self.assertEqual(len(got), 2)

    def test_non_utf8_bytes_do_not_crash(self):
        with open(self.path, "wb") as fh:
            fh.write(b"[INFO] ok\n[INFO] \xff\xfe bad\n")
        got = FileLog(self.path).recent(10)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0], "[INFO] ok")


if __name__ == "__main__":
    unittest.main()


class TestArchiveBackfill(unittest.TestCase):
    """Historique à travers les rotations : latest.log insuffisant -> on
    complète depuis les archives .log.gz (plus récentes d'abord)."""

    def setUp(self):
        import gzip as _gzip
        import os as _os
        import tempfile as _tempfile
        self._gzip = _gzip
        self._os = _os
        self._dir = _tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = _os.path.join(self._dir.name, "latest.log")

    def _write_latest(self, *lines):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("".join(line + "\n" for line in lines))

    def _write_archive(self, name, *lines, mtime=None, raw=None):
        p = self._os.path.join(self._dir.name, name)
        if raw is not None:
            with open(p, "wb") as fh:
                fh.write(raw)
        else:
            with self._gzip.open(p, "wt", encoding="utf-8") as fh:
                fh.write("".join(line + "\n" for line in lines))
        if mtime is not None:
            self._os.utime(p, (mtime, mtime))
        return p

    def test_backfills_from_newest_archive_when_latest_is_short(self):
        self._write_latest("[c] recent")
        self._write_archive("2026-07-05-1.log.gz", "[a] old-1", "[b] old-2", mtime=1000)
        log = FileLog(self.path, exclude=None)
        self.assertEqual(log.recent(3), ["[a] old-1", "[b] old-2", "[c] recent"])

    def test_older_archives_come_first_across_multiple_files(self):
        self._write_latest("[d] now")
        self._write_archive("2026-07-04-1.log.gz", "[a] ancien", mtime=1000)
        self._write_archive("2026-07-05-1.log.gz", "[b] hier", "[c] hier-2", mtime=2000)
        log = FileLog(self.path, exclude=None)
        self.assertEqual(log.recent(4), ["[a] ancien", "[b] hier", "[c] hier-2", "[d] now"])

    def test_no_backfill_when_latest_satisfies_request(self):
        self._write_latest("[a] 1", "[b] 2", "[c] 3")
        self._write_archive("2026-07-05-1.log.gz", "[z] jamais lu", mtime=1000)
        log = FileLog(self.path, exclude=None)
        self.assertEqual(log.recent(2), ["[b] 2", "[c] 3"])  # archives non consultées

    def test_exclude_filter_applies_to_archives_too(self):
        self._write_latest()
        self._write_archive("2026-07-05-1.log.gz",
                            "[10:00] Thread RCON Client /1.2.3.4 started",
                            "[10:01] alice joined the game", mtime=1000)
        log = FileLog(self.path)  # DEFAULT_EXCLUDE
        self.assertEqual(log.recent(5), ["[10:01] alice joined the game"])

    def test_corrupt_archive_is_skipped(self):
        self._write_latest("[b] now")
        self._write_archive("2026-07-05-1.log.gz", raw=b"pas du gzip", mtime=2000)
        self._write_archive("2026-07-04-1.log.gz", "[a] ok", mtime=1000)
        log = FileLog(self.path, exclude=None)
        self.assertEqual(log.recent(3), ["[a] ok", "[b] now"])

    def test_archive_cache_hits_on_second_call(self):
        self._write_latest("[b] now")
        p = self._write_archive("2026-07-05-1.log.gz", "[a] old", mtime=1000)
        log = FileLog(self.path, exclude=None)
        self.assertEqual(log.recent(2), ["[a] old", "[b] now"])
        # Supprimer le CONTENU de l'archive (mtime conservé) : le cache doit servir.
        with open(p, "wb") as fh:
            fh.write(b"invalide")
        self._os.utime(p, (1000, 1000))
        self.assertEqual(log.recent(2), ["[a] old", "[b] now"])


class TestBootMarker(unittest.TestCase):
    """L'affichage est borné au DERNIER démarrage du serveur quand le marqueur
    de boot (Fabric ou vanilla) est trouvé."""

    def setUp(self):
        import gzip as _gzip
        import os as _os
        import tempfile as _tempfile
        self._gzip = _gzip
        self._os = _os
        self._dir = _tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = _os.path.join(self._dir.name, "latest.log")

    BOOT = "[12:00:00] [main/INFO]: Loading Minecraft 26.1 with Fabric Loader 0.19.3"

    def _write_latest(self, *lines):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("".join(line + "\n" for line in lines))

    def _write_archive(self, name, *lines, mtime=None):
        p = self._os.path.join(self._dir.name, name)
        with self._gzip.open(p, "wt", encoding="utf-8") as fh:
            fh.write("".join(line + "\n" for line in lines))
        if mtime is not None:
            self._os.utime(p, (mtime, mtime))

    def test_cuts_at_last_boot_in_latest(self):
        self._write_latest("[a] run précédent", self.BOOT, "[b] après boot")
        log = FileLog(self.path, exclude=None)
        self.assertEqual(log.recent(50), [self.BOOT, "[b] après boot"])

    def test_vanilla_marker_also_matches(self):
        vanilla = "[12:00:00] [Server thread/INFO]: Starting minecraft server version 26.1"
        self._write_latest("[a] avant", vanilla, "[b] après")
        log = FileLog(self.path, exclude=None)
        self.assertEqual(log.recent(50), [vanilla, "[b] après"])

    def test_boot_found_in_archive_stops_backfill(self):
        self._write_latest("[d] après rotation")
        self._write_archive("2026-07-04-1.log.gz", "[x] run d'avant, jamais affiché", mtime=1000)
        self._write_archive("2026-07-05-1.log.gz", "[a] vieux run", self.BOOT, "[b] boot log", mtime=2000)
        log = FileLog(self.path, exclude=None)
        self.assertEqual(log.recent(50), [self.BOOT, "[b] boot log", "[d] après rotation"])

    def test_no_marker_falls_back_to_line_cap(self):
        self._write_latest("[a] 1", "[b] 2", "[c] 3")
        log = FileLog(self.path, exclude=None)
        self.assertEqual(log.recent(2), ["[b] 2", "[c] 3"])

    def test_cap_still_applies_to_huge_boot_log(self):
        lines = [self.BOOT] + [f"[l{i}]" for i in range(10)]
        self._write_latest(*lines)
        log = FileLog(self.path, exclude=None)
        out = log.recent(5)
        self.assertEqual(len(out), 5)          # plafond respecté
        self.assertEqual(out[-1], "[l9]")      # les plus récentes gagnent
