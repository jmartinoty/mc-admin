"""Tests de l'adapter StorageHistoryPort (JsonStorageHistory) et du watcher
(StorageHistoryWatcher) — backlog fiabilité n° 6."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adapters.storage_history import JsonStorageHistory
from domain.errors import DomainError
from domain.model import StorageSnapshot
from storage_history_watcher import StorageHistoryWatcher, compute_snapshot


class FakeArchive:
    def __init__(self, filename: str, size_bytes: int):
        self.filename = filename
        self.size_bytes = size_bytes


class TestComputeSnapshot(unittest.TestCase):
    def test_groups_by_top_level_family(self):
        archives = [
            FakeArchive("manual/a.tar.gz", 100),
            FakeArchive("scheduled/b.tar.gz", 200),
            FakeArchive("scheduled/c.tar.gz", 50),
            FakeArchive("profiles/nuit/d.tar.gz", 10),
            FakeArchive("profiles/jour/e.tar.gz", 20),
        ]
        snapshot = compute_snapshot(archives, free_bytes=5000, today="2026-07-20")
        self.assertEqual(snapshot.date, "2026-07-20")
        self.assertEqual(snapshot.free_bytes, 5000)
        # « profiles » agrégé au niveau du dossier racine, pas par profil.
        self.assertEqual(snapshot.families, {"manual": 100, "scheduled": 250, "profiles": 30})

    def test_empty_archives_gives_empty_families(self):
        snapshot = compute_snapshot([], free_bytes=None, today="2026-07-20")
        self.assertEqual(snapshot.families, {})
        self.assertIsNone(snapshot.free_bytes)


class TestJsonStorageHistory(unittest.TestCase):
    def test_record_then_read_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "storage_history.json")
            store = JsonStorageHistory(path)
            store.record_today(StorageSnapshot(
                date="2026-07-19", free_bytes=1000, families={"manual": 400}))
            store.record_today(StorageSnapshot(
                date="2026-07-20", free_bytes=900, families={"manual": 500}))
            history = store.history(30)
            self.assertEqual([s.date for s in history], ["2026-07-19", "2026-07-20"])
            self.assertEqual(history[1].families, {"manual": 500})

    def test_same_day_recorded_twice_keeps_the_latest_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "storage_history.json")
            store = JsonStorageHistory(path)
            store.record_today(StorageSnapshot(
                date="2026-07-20", free_bytes=1000, families={"manual": 100}))
            store.record_today(StorageSnapshot(
                date="2026-07-20", free_bytes=800, families={"manual": 300}))
            history = store.history(30)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].free_bytes, 800)

    def test_days_window_keeps_only_the_most_recent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "storage_history.json")
            store = JsonStorageHistory(path)
            for day in range(1, 6):
                store.record_today(StorageSnapshot(
                    date=f"2026-07-{day:02d}", free_bytes=1000 - day, families={}))
            history = store.history(2)
            self.assertEqual([s.date for s in history], ["2026-07-04", "2026-07-05"])

    def test_missing_file_returns_empty_history(self):
        store = JsonStorageHistory("/nonexistent/storage_history.json")
        self.assertEqual(store.history(30), [])

    def test_corrupt_file_refuses_to_overwrite_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "storage_history.json")
            Path(path).write_text("{pas du json", encoding="utf-8")
            store = JsonStorageHistory(path)
            self.assertEqual(store.history(30), [])  # lecture tolérante
            with self.assertRaises(DomainError):
                store.record_today(StorageSnapshot(date="2026-07-20", free_bytes=1, families={}))


class TestStorageHistoryWatcher(unittest.TestCase):
    def test_tick_records_a_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "storage_history.json")

            class FakeArchivesPort:
                def list_archives(self):
                    return [FakeArchive("manual/a.tar.gz", 42)]

                def free_bytes(self):
                    return 12345

            from datetime import datetime, timezone
            watcher = StorageHistoryWatcher(
                FakeArchivesPort(), JsonStorageHistory(path),
                clock=lambda: datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc),
            )
            watcher.tick()
            history = JsonStorageHistory(path).history(30)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].date, "2026-07-20")
            self.assertEqual(history[0].families, {"manual": 42})
            self.assertEqual(history[0].free_bytes, 12345)

    def test_a_failing_tick_never_kills_the_thread(self):
        class BrokenArchivesPort:
            def list_archives(self):
                raise RuntimeError("volume injoignable")

            def free_bytes(self):
                return None

        events = []
        stop_after_one = {"count": 0}

        def fake_wait(_seconds):
            stop_after_one["count"] += 1
            return stop_after_one["count"] > 1  # True = interrompu, on sort

        watcher = StorageHistoryWatcher(
            BrokenArchivesPort(), object(), sleep=fake_wait,
        )
        watcher._run()  # ne doit PAS lever, malgré le tick en échec
        events.append("survived")
        self.assertEqual(events, ["survived"])


if __name__ == "__main__":
    unittest.main()
