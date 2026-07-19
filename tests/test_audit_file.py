"""Tests de l'adapter FileAuditLog (journal JSONL append-only)."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

from adapters.audit_file import FileAuditLog
from domain.model import AuditEntry


def _entry(action="RESTART", outcome="allowed", detail="", user="jeremy"):
    return AuditEntry(
        timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        username=user,
        role="owner",
        action=action,
        target="minecraft",
        outcome=outcome,
        detail=detail,
    )


class TestFileAuditLog(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "sub", "audit.jsonl")  # sous-dossier créé auto

    def tearDown(self):
        self._dir.cleanup()

    def test_recent_on_missing_file_returns_empty(self):
        self.assertEqual(FileAuditLog(self.path).recent(), [])

    def test_record_then_recent_roundtrip(self):
        log = FileAuditLog(self.path)
        log.record(_entry(action="RESTART"))
        log.record(_entry(action="BACKUP_TRIGGER", outcome="denied", user="sam"))
        got = log.recent()
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0].action, "RESTART")
        self.assertEqual(got[1].outcome, "denied")
        self.assertEqual(got[1].username, "sam")
        self.assertEqual(got[0].timestamp, datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc))

    def test_append_persists_across_instances(self):
        FileAuditLog(self.path).record(_entry(action="A"))
        FileAuditLog(self.path).record(_entry(action="B"))
        got = FileAuditLog(self.path).recent()
        self.assertEqual([e.action for e in got], ["A", "B"])

    def test_limit_returns_last_n(self):
        log = FileAuditLog(self.path)
        for i in range(5):
            log.record(_entry(detail=str(i)))
        got = log.recent(limit=2)
        self.assertEqual([e.detail for e in got], ["3", "4"])

    def test_non_ascii_detail_preserved(self):
        log = FileAuditLog(self.path)
        log.record(_entry(detail="say bonjour à tous « é »"))
        self.assertEqual(log.recent()[0].detail, "say bonjour à tous « é »")


class TestMonthlyRotation(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, "audit.jsonl")

    def _entry(self, detail, ts):
        return AuditEntry(timestamp=ts, username="jeremy", role="owner",
                          action="RESTART", target="minecraft", outcome="allowed", detail=detail)

    def test_writes_go_to_monthly_file_and_reads_span_months(self):
        june = datetime(2026, 6, 15, tzinfo=timezone.utc)
        july = datetime(2026, 7, 15, tzinfo=timezone.utc)
        moment = {"now": june}
        log = FileAuditLog(self.path, clock=lambda: moment["now"])
        log.record(self._entry("entree-juin", june))
        moment["now"] = july
        log.record(self._entry("entree-juillet", july))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "audit-2026-06.jsonl")))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "audit-2026-07.jsonl")))
        self.assertFalse(os.path.exists(self.path))          # plus d'écriture au fichier unique
        details = [e.detail for e in log.recent(10)]
        self.assertEqual(details, ["entree-juin", "entree-juillet"])  # ordre chronologique

    def test_legacy_single_file_still_read_as_oldest(self):
        legacy = self._entry("entree-historique", datetime(2026, 5, 1, tzinfo=timezone.utc))
        with open(self.path, "w", encoding="utf-8") as fh:
            import json
            fh.write(json.dumps({
                "timestamp": legacy.timestamp.isoformat(), "username": legacy.username,
                "role": legacy.role, "action": legacy.action, "target": legacy.target,
                "outcome": legacy.outcome, "detail": legacy.detail}) + "\n")
        july = datetime(2026, 7, 15, tzinfo=timezone.utc)
        log = FileAuditLog(self.path, clock=lambda: july)
        log.record(self._entry("entree-recente", july))
        details = [e.detail for e in log.recent(10)]
        self.assertEqual(details, ["entree-historique", "entree-recente"])

    def test_limit_stops_at_recent_months(self):
        july = datetime(2026, 7, 15, tzinfo=timezone.utc)
        log = FileAuditLog(self.path, clock=lambda: july)
        for i in range(5):
            log.record(self._entry(f"e{i}", july))
        self.assertEqual([e.detail for e in log.recent(2)], ["e3", "e4"])




class TestCorruptLines(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "audit.jsonl")
        self.addCleanup(self.dir.cleanup)

    def test_corrupt_line_is_skipped_not_fatal(self):
        july = datetime(2026, 7, 15, tzinfo=timezone.utc)
        log = FileAuditLog(self.path, clock=lambda: july)
        log.record(AuditEntry(timestamp=july, username="jeremy", role="owner",
                              action="RESTART", target="minecraft",
                              outcome="allowed", detail="avant"))
        # Écriture interrompue / édition manuelle : trois formes de corruption.
        with open(log._month_path(), "a", encoding="utf-8") as fh:
            fh.write("{pas du json\n")
            fh.write('{"timestamp": "n-importe-quoi", "username": "x"}\n')
            fh.write('{"username": "sans-timestamp"}\n')
        log.record(AuditEntry(timestamp=july, username="jeremy", role="owner",
                              action="RESTART", target="minecraft",
                              outcome="allowed", detail="apres"))
        details = [e.detail for e in log.recent(10)]
        self.assertEqual(details, ["avant", "apres"])


if __name__ == "__main__":
    unittest.main()
