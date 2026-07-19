"""Tests de l'adapter JsonTempBans."""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from adapters.atomic_json import CorruptJsonError
from adapters.temp_bans import JsonTempBans

T0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _cleanup(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


class TestJsonTempBans(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)  # le fichier doit être créé PAR l'adapter
        self.addCleanup(lambda: _cleanup(self.path))

    def test_pending_on_missing_file_is_empty(self):
        self.assertEqual(JsonTempBans(self.path).pending(), [])

    def test_schedule_then_pending_roundtrip(self):
        store = JsonTempBans(self.path)
        store.schedule("toto", T0 + timedelta(hours=24))
        pending = store.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].player, "toto")
        self.assertEqual(pending[0].unban_at, T0 + timedelta(hours=24))

    def test_persists_across_instances(self):
        JsonTempBans(self.path).schedule("toto", T0)
        pending = JsonTempBans(self.path).pending()
        self.assertEqual([p.player for p in pending], ["toto"])

    def test_rescheduling_replaces_entry(self):
        store = JsonTempBans(self.path)
        store.schedule("toto", T0)
        store.schedule("toto", T0 + timedelta(hours=48))
        pending = store.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].unban_at, T0 + timedelta(hours=48))

    def test_cancel_removes_entry(self):
        store = JsonTempBans(self.path)
        store.schedule("toto", T0)
        store.schedule("alice", T0)
        store.cancel("toto")
        self.assertEqual([p.player for p in store.pending()], ["alice"])

    def test_cancel_on_missing_entry_is_a_noop(self):
        store = JsonTempBans(self.path)
        store.cancel("nobody")  # ne doit pas lever
        self.assertEqual(store.pending(), [])

    def test_due_filters_by_time(self):
        store = JsonTempBans(self.path)
        store.schedule("expired", T0 - timedelta(hours=1))
        store.schedule("future", T0 + timedelta(hours=1))
        due = store.due(T0)
        self.assertEqual([p.player for p in due], ["expired"])

    def test_malformed_json_degrades_to_empty(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        self.assertEqual(JsonTempBans(self.path).pending(), [])

    def test_malformed_json_is_not_overwritten_by_mutation(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        with self.assertRaises(CorruptJsonError):
            JsonTempBans(self.path).schedule("toto", T0)
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{not valid json")

    def test_directory_instead_of_file_degrades_to_empty(self):
        os.makedirs(self.path)
        self.assertEqual(JsonTempBans(self.path).pending(), [])

    def test_entries_missing_fields_are_skipped(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write('[{"player": "toto"}, {"unban_at": "bad"}, {"player": "ok", "unban_at": "2026-07-01T12:00:00+00:00"}]')
        pending = JsonTempBans(self.path).pending()
        self.assertEqual([p.player for p in pending], ["ok"])

    def test_non_object_entry_is_skipped_but_blocks_mutation(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(
                '["broken", {"player": "ok", '
                '"unban_at": "2026-07-01T12:00:00+00:00"}]'
            )
        store = JsonTempBans(self.path)

        self.assertEqual([entry.player for entry in store.pending()], ["ok"])
        with self.assertRaises(CorruptJsonError):
            store.schedule("alice", T0)

    def test_two_instances_do_not_lose_concurrent_schedules(self):
        first = JsonTempBans(self.path)
        second = JsonTempBans(self.path)
        first_has_read = threading.Event()
        release_first = threading.Event()
        second_done = threading.Event()
        original_read = first._read

        def delayed_read(*, strict=False):
            entries = original_read(strict=strict)
            first_has_read.set()
            release_first.wait(timeout=2)
            return entries

        first._read = delayed_read
        thread_a = threading.Thread(target=first.schedule, args=("alice", T0))
        thread_b = threading.Thread(
            target=lambda: (second.schedule("bob", T0), second_done.set())
        )
        thread_a.start()
        self.assertTrue(first_has_read.wait(timeout=1))
        thread_b.start()
        self.assertFalse(second_done.wait(timeout=0.05))
        release_first.set()
        thread_a.join(timeout=2)
        thread_b.join(timeout=2)

        self.assertEqual(
            {entry.player for entry in JsonTempBans(self.path).pending()},
            {"alice", "bob"},
        )


if __name__ == "__main__":
    unittest.main()
