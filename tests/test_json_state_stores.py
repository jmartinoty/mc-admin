"""Garanties communes des stores JSON persistants de mc-admin."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from adapters.archive_checks import JsonArchiveChecks
from adapters.atomic_json import CorruptJsonError
from adapters.backup_profiles import JsonBackupProfiles
from adapters.backup_schedule import JsonBackupSchedule
from adapters.credentials_store import JsonCredentials
from adapters.display_names import JsonDisplayNames
from adapters.mod_checks import JsonModChecks
from adapters.notification_config import JsonNotificationConfig
from adapters.recurring_restart import JsonRecurringRestart
from adapters.servers_store import JsonServers
from adapters.users_store import JsonUsers
from adapters.watched_store import JsonWatched
from domain.errors import DomainError
from domain.model import (
    BackupProfile,
    RecurringRestart,
    ServerEntry,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


class JsonStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def path(self, name: str) -> str:
        return os.path.join(self.directory.name, name)


class TestJsonCredentials(JsonStoreTestCase):
    def test_remove_persists_without_touching_other_accounts(self):
        path = self.path("passwords.json")
        first = JsonCredentials(path)
        first.set("alice", "hash-a")
        first.set("bob", "hash-b")

        self.assertTrue(first.remove("alice"))
        second = JsonCredentials(path)
        self.assertIsNone(second.get("alice"))
        self.assertEqual(second.get("bob"), "hash-b")
        self.assertFalse(second.remove("alice"))

    def test_write_tightens_existing_file_permissions(self):
        path = self.path("passwords.json")
        with open(path, "w", encoding="utf-8") as output:
            output.write("{}")
        os.chmod(path, 0o644)

        JsonCredentials(path).set("alice", "hash-a")

        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_two_instances_do_not_lose_concurrent_updates(self):
        path = self.path("passwords.json")
        first = JsonCredentials(path)
        second = JsonCredentials(path)
        first_has_read = threading.Event()
        release_first = threading.Event()
        second_done = threading.Event()
        original_read = first._read

        def delayed_read(*, strict=False):
            data = original_read(strict=strict)
            first_has_read.set()
            release_first.wait(timeout=2)
            return data

        first._read = delayed_read
        thread_a = threading.Thread(target=first.set, args=("alice", "hash-a"))
        thread_b = threading.Thread(
            target=lambda: (second.set("bob", "hash-b"), second_done.set())
        )
        thread_a.start()
        self.assertTrue(first_has_read.wait(timeout=1))
        thread_b.start()
        self.assertFalse(second_done.wait(timeout=0.05))
        release_first.set()
        thread_a.join(timeout=2)
        thread_b.join(timeout=2)

        result = JsonCredentials(path)
        self.assertEqual(result.get("alice"), "hash-a")
        self.assertEqual(result.get("bob"), "hash-b")

    def test_mutation_refuses_to_overwrite_malformed_file(self):
        path = self.path("passwords.json")
        with open(path, "w", encoding="utf-8") as output:
            output.write("{broken")
        store = JsonCredentials(path)

        self.assertIsNone(store.get("alice"))
        with self.assertRaises(CorruptJsonError):
            store.set("alice", "hash-a")
        with open(path, encoding="utf-8") as source:
            self.assertEqual(source.read(), "{broken")


class TestOtherJsonStores(JsonStoreTestCase):
    def test_users_refuse_to_overwrite_malformed_file(self):
        path = self.path("users.json")
        with open(path, "w", encoding="utf-8") as output:
            output.write("not-json")
        store = JsonUsers(path)

        self.assertEqual(store.all(), {})
        with self.assertRaises(CorruptJsonError):
            store.add("alice", "owner")

    def test_display_name_roundtrip_keeps_unicode(self):
        path = self.path("display-names.json")
        store = JsonDisplayNames(path)

        store.set("jeremy", "Jérémy")

        self.assertEqual(JsonDisplayNames(path).get("jeremy"), "Jérémy")

    def test_recurring_restart_roundtrip(self):
        path = self.path("recurring.json")
        store = JsonRecurringRestart(path)
        config = RecurringRestart(time_hhmm="06:30", lead_seconds=600)

        store.set_config(config)
        store.mark_fired("2026-07-18")

        self.assertEqual(JsonRecurringRestart(path).get(), config)
        self.assertEqual(JsonRecurringRestart(path).last_fired(), "2026-07-18")


class TestCorruptStateProtection(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def path(self, name: str) -> str:
        return os.path.join(self.directory.name, name)

    @staticmethod
    def corrupt(path: str) -> bytes:
        content = b"{not valid json"
        with open(path, "wb") as output:
            output.write(content)
        return content

    def assert_preserved(self, path: str, mutation) -> None:
        original = self.corrupt(path)
        with self.assertRaises(CorruptJsonError) as raised:
            mutation()
        self.assertIsInstance(raised.exception, DomainError)
        with open(path, "rb") as source:
            self.assertEqual(source.read(), original)

    def test_backup_schedule_corruption_is_visible_but_not_overwritten(self):
        path = self.path("schedule.json")
        store = JsonBackupSchedule(path)
        self.corrupt(path)
        self.assertFalse(store.get().enabled)
        self.assert_preserved(path, lambda: store.set_schedule(6, None))

    def test_backup_profiles_corruption_is_visible_but_not_overwritten(self):
        path = self.path("profiles.json")
        store = JsonBackupProfiles(path)
        self.corrupt(path)
        self.assertEqual(store.list_profiles(), [])
        profile = BackupProfile(
            id="manual",
            name="Manuelles",
            include=("all",),
            dest="manual",
        )
        self.assert_preserved(path, lambda: store.upsert(profile))

    def test_servers_corruption_is_visible_but_not_overwritten(self):
        path = self.path("servers.json")
        store = JsonServers(path)
        self.corrupt(path)
        self.assertEqual(store.list_servers(), [])
        entry = ServerEntry(id="main", name="Principal")
        self.assert_preserved(path, lambda: store.add(entry))

    def test_watched_corruption_is_visible_but_not_overwritten(self):
        path = self.path("watched.json")
        store = JsonWatched(path)
        self.corrupt(path)
        self.assertEqual(store.all(), [])
        self.assert_preserved(path, lambda: store.add("playit"))

    def test_notification_corruption_is_visible_but_not_overwritten(self):
        path = self.path("notifications.json")
        store = JsonNotificationConfig(path)
        self.corrupt(path)
        self.assertEqual(store.list_channels(), [])
        self.assert_preserved(
            path,
            lambda: store.add_channel(
                "discord",
                "Discord",
                {"webhook_url": "https://example.invalid/hook"},
                {},
            ),
        )

    def test_archive_cache_corruption_is_visible_but_not_overwritten(self):
        path = self.path("archive-checks.json")
        store = JsonArchiveChecks(path)
        self.corrupt(path)
        self.assertEqual(store.statuses(), {})
        self.assert_preserved(path, lambda: store.mark_checking("manual/a.tar.gz"))

    def test_mod_cache_corruption_is_visible_but_not_overwritten(self):
        path = self.path("mod-checks.json")
        store = JsonModChecks(path)
        self.corrupt(path)
        self.assertEqual(store.statuses(), {})
        self.assert_preserved(path, lambda: store.replace_all({}, NOW))

    def test_nested_schema_errors_are_not_silently_rewritten(self):
        cases = (
            (
                "schedule.json",
                {"hours": "six"},
                lambda path: JsonBackupSchedule(path).mark_run(NOW),
            ),
            (
                "profiles.json",
                {"profiles": "not-a-list"},
                lambda path: JsonBackupProfiles(path).upsert(
                    BackupProfile("manual", "Manual", ("all",), "manual")
                ),
            ),
            (
                "servers.json",
                {"servers": [{"id": 42}]},
                lambda path: JsonServers(path).add(
                    ServerEntry("main", "Principal")
                ),
            ),
            (
                "watched.json",
                {"containers": "playit"},
                lambda path: JsonWatched(path).add("playit"),
            ),
            (
                "archive-checks.json",
                {"manual/a.tar.gz": {"ok": "yes"}},
                lambda path: JsonArchiveChecks(path).mark_checking(
                    "manual/b.tar.gz"
                ),
            ),
            (
                "mod-checks.json",
                {"checks": []},
                lambda path: JsonModChecks(path).replace_all({}, NOW),
            ),
        )
        for filename, payload, mutation in cases:
            with self.subTest(filename=filename):
                path = self.path(filename)
                with open(path, "w", encoding="utf-8") as output:
                    json.dump(payload, output)
                with open(path, "rb") as source:
                    before = source.read()
                with self.assertRaises(CorruptJsonError):
                    mutation(path)
                with open(path, "rb") as source:
                    self.assertEqual(source.read(), before)

    def test_duplicate_entity_ids_are_not_silently_rewritten(self):
        cases = (
            (
                "profiles.json",
                {
                    "profiles": [
                        {
                            "id": "auto",
                            "name": "Auto",
                            "include": ["all"],
                            "dest": "scheduled",
                        },
                        {
                            "id": "auto",
                            "name": "Doublon",
                            "include": ["world"],
                            "dest": "other",
                        },
                    ]
                },
                lambda path: JsonBackupProfiles(path).delete("auto"),
            ),
            (
                "servers.json",
                {
                    "servers": [
                        {"id": "main", "name": "Principal"},
                        {"id": "main", "name": "Doublon"},
                    ]
                },
                lambda path: JsonServers(path).set_address(
                    "main",
                    "mc.example.test",
                ),
            ),
        )
        for filename, payload, mutation in cases:
            with self.subTest(filename=filename):
                path = self.path(filename)
                with open(path, "w", encoding="utf-8") as output:
                    json.dump(payload, output)
                with open(path, "rb") as source:
                    before = source.read()
                with self.assertRaises(CorruptJsonError):
                    mutation(path)
                with open(path, "rb") as source:
                    self.assertEqual(source.read(), before)


class TestSharedStateLocks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def path(self, name: str) -> str:
        return os.path.join(self.directory.name, name)

    def test_instances_targeting_same_path_share_one_lock(self):
        classes = (
            JsonBackupSchedule,
            JsonBackupProfiles,
            JsonServers,
            JsonWatched,
            JsonNotificationConfig,
            JsonArchiveChecks,
            JsonModChecks,
        )
        for store_class in classes:
            with self.subTest(store=store_class.__name__):
                path = self.path(f"{store_class.__name__}.json")
                first = store_class(path)
                second = store_class(path)
                self.assertIs(first._lock, second._lock)

    def test_schedule_and_last_run_survive_parallel_updates(self):
        path = self.path("schedule.json")
        first = JsonBackupSchedule(path)
        second = JsonBackupSchedule(path)

        with ThreadPoolExecutor(max_workers=2) as executor:
            set_future = executor.submit(first.set_schedule, 12, None)
            run_future = executor.submit(second.mark_run, NOW)
            set_future.result()
            run_future.result()

        schedule = first.get()
        self.assertEqual(schedule.hours, 12)
        self.assertEqual(schedule.last_run, NOW)

    def test_parallel_profile_upserts_do_not_lose_an_entry(self):
        path = self.path("profiles.json")
        first = JsonBackupProfiles(path)
        second = JsonBackupProfiles(path)
        profiles = (
            BackupProfile("manual", "Manual", ("all",), "manual"),
            BackupProfile("auto", "Auto", ("world",), "scheduled"),
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(first.upsert, profiles[0]),
                executor.submit(second.upsert, profiles[1]),
            )
            for future in futures:
                future.result()

        self.assertEqual(
            {profile.id for profile in first.list_profiles()},
            {"manual", "auto"},
        )

    def test_parallel_channel_adds_get_distinct_ids(self):
        path = self.path("notifications.json")
        first = JsonNotificationConfig(path)
        second = JsonNotificationConfig(path)

        def add(store, webhook):
            return store.add_channel(
                "discord",
                "Copains",
                {"webhook_url": webhook},
                {},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(add, first, "https://example.invalid/1"),
                executor.submit(add, second, "https://example.invalid/2"),
            )
            ids = {future.result() for future in futures}

        self.assertEqual(ids, {"copains", "copains-2"})
        self.assertEqual(len(first.list_channels()), 2)


class TestSensitiveStatePermissions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "notifications.json")

    def test_existing_notification_file_is_hardened_on_startup(self):
        with open(self.path, "w", encoding="utf-8") as output:
            json.dump({"channels": [], "env_import_done": False}, output)
        os.chmod(self.path, 0o644)

        JsonNotificationConfig(self.path)

        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_new_notification_file_is_private(self):
        store = JsonNotificationConfig(self.path)
        store.add_channel(
            "discord",
            "Discord",
            {"webhook_url": "https://example.invalid/hook"},
            {},
        )

        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
