"""Tests ModrinthCatalog + JsonModChecks + ModUpdateChecker (mods enrichis)."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone

from adapters.mod_checks import JsonModChecks
from adapters.modrinth import ModrinthCatalog
from domain.model import ModUpdate
from mod_update_checker import ModUpdateChecker
from tests.fakes import FakeModChecks, FakeMods


class TestModrinthCatalog(unittest.TestCase):
    def _catalog(self):
        self.calls = []

        def fetch(url, payload):
            self.calls.append((url, payload))
            if url.endswith("/version_files"):
                return {"aaa111": {"id": "v1", "project_id": "lith", "version_number": "0.22.1"}}
            return {"aaa111": {"id": "v2", "project_id": "lith", "version_number": "0.23.0"}}
        return ModrinthCatalog(fetch=fetch)

    def test_identifies_and_flags_update(self):
        out = self._catalog().check(["aaa111", "zzz999"], "26.1")
        lith = out["aaa111"]
        self.assertTrue(lith.known)
        self.assertTrue(lith.update_available)                       # v2 != v1
        self.assertEqual(lith.latest_version, "0.23.0")
        self.assertEqual(lith.project_url, "https://modrinth.com/project/lith")
        self.assertFalse(out["zzz999"].known)                        # absent des réponses
        # deux appels, filtrés loader+version côté Modrinth
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1][1]["game_versions"], ["26.1"])
        self.assertEqual(self.calls[1][1]["loaders"], ["fabric"])

    def test_same_version_id_means_up_to_date(self):
        def fetch(url, payload):
            return {"aaa111": {"id": "v1", "project_id": "lith", "version_number": "0.22.1"}}
        out = ModrinthCatalog(fetch=fetch).check(["aaa111"], "26.1")
        self.assertTrue(out["aaa111"].known)
        self.assertFalse(out["aaa111"].update_available)

    def test_empty_hashes_no_network(self):
        catalog = ModrinthCatalog(fetch=lambda u, p: self.fail("appel réseau inattendu"))
        self.assertEqual(catalog.check([], "26.1"), {})


class TestJsonModChecks(unittest.TestCase):
    def test_roundtrip_and_corrupt_entries(self):
        with tempfile.TemporaryDirectory() as d:
            store = JsonModChecks(os.path.join(d, "mod_checks.json"))
            self.assertEqual(store.statuses(), {})
            self.assertIsNone(store.checked_at())
            now = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
            store.replace_all({"aaa": ModUpdate(sha1="aaa", known=True,
                                                latest_version="2.0",
                                                update_available=True,
                                                checked_at=now)}, now)
            back = store.statuses()["aaa"]
            self.assertTrue(back.update_available)
            self.assertEqual(store.checked_at(), now)


class RecordingCatalog:
    def __init__(self):
        self.calls = []

    def check(self, hashes, game_version, loader="fabric"):
        self.calls.append((tuple(hashes), game_version))
        return {h: ModUpdate(sha1=h, known=True) for h in hashes}


class TestModUpdateChecker(unittest.TestCase):
    def _checker(self, checked=None, version="26.1", checks=None):
        self.catalog = RecordingCatalog()
        self.store = FakeModChecks(checks=checks if checks is not None else {}, checked=checked)
        self.now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
        return ModUpdateChecker(FakeMods(), self.catalog, self.store,
                                version_provider=lambda: version,
                                clock=lambda: self.now)

    def test_first_pass_queries_and_persists(self):
        checker = self._checker()
        self.assertTrue(checker.tick())
        self.assertEqual(self.catalog.calls, [(("aaa111", "bbb222"), "26.1")])
        self.assertEqual(set(self.store.statuses()), {"aaa111", "bbb222"})
        self.assertEqual(self.store.checked_at(), self.now)

    def test_fresh_verdicts_skip_the_api(self):
        checker = self._checker(
            checked=datetime(2026, 7, 18, 6, 0, tzinfo=timezone.utc),
            checks={"aaa111": ModUpdate(sha1="aaa111"), "bbb222": ModUpdate(sha1="bbb222")})
        self.assertFalse(checker.tick())                             # < 24 h, rien de neuf
        self.assertEqual(self.catalog.calls, [])

    def test_new_jar_triggers_recheck_despite_freshness(self):
        checker = self._checker(
            checked=datetime(2026, 7, 18, 6, 0, tzinfo=timezone.utc),
            checks={"aaa111": ModUpdate(sha1="aaa111")})              # bbb222 inconnu
        self.assertTrue(checker.tick())

    def test_stale_verdicts_recheck_after_24h(self):
        checker = self._checker(
            checked=datetime(2026, 7, 16, 6, 0, tzinfo=timezone.utc),
            checks={"aaa111": ModUpdate(sha1="aaa111"), "bbb222": ModUpdate(sha1="bbb222")})
        self.assertTrue(checker.tick())

    def test_unknown_server_version_waits(self):
        checker = self._checker(version=None)
        self.assertFalse(checker.tick())                             # pas de filtre fiable -> on attend
        self.assertEqual(self.catalog.calls, [])


if __name__ == "__main__":
    unittest.main()
