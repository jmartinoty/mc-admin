"""Bouton MAJ — catalogue GitHub, verdict persisté, checker 24 h, semver."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from adapters.app_update import GithubReleases, JsonAppUpdate
from app_update_checker import AppUpdateChecker
from domain.model import AppRelease
from domain.services.update import is_newer_app_version


class TestIsNewerAppVersion(unittest.TestCase):
    def test_semver_numeric(self):
        self.assertTrue(is_newer_app_version("0.10.0", "0.9.0"))   # 10 > 9, pas ordre lexical
        self.assertTrue(is_newer_app_version("1.0.0", "0.9.9"))
        self.assertFalse(is_newer_app_version("0.9.0", "0.9.0"))
        self.assertFalse(is_newer_app_version("0.8.2", "0.9.0"))

    def test_exotic_formats_fall_back_to_difference(self):
        self.assertTrue(is_newer_app_version("0.10.0-rc1", "0.9.0"))   # différents
        self.assertFalse(is_newer_app_version("abc", "abc"))           # identiques


class TestGithubReleases(unittest.TestCase):
    def test_parses_release(self):
        payload = {"tag_name": "v0.10.0", "body": "Des nouveautés.",
                   "html_url": "https://github.com/x/y/releases/tag/v0.10.0"}
        catalog = GithubReleases("x/y", fetch=lambda url: payload)
        release = catalog.latest()
        self.assertEqual(release.version, "0.10.0")                # « v » retiré
        self.assertEqual(release.notes, "Des nouveautés.")

    def test_error_gives_none(self):
        def fetch(url):
            raise OSError("réseau")
        self.assertIsNone(GithubReleases("x/y", fetch=fetch).latest())

    def test_empty_tag_gives_none(self):
        self.assertIsNone(GithubReleases("x/y", fetch=lambda url: {}).latest())

    def test_notes_are_bounded(self):
        payload = {"tag_name": "v1.0.0", "body": "x" * 100_000}
        release = GithubReleases("x/y", fetch=lambda url: payload).latest()
        self.assertLessEqual(len(release.notes), 4000)


class TestJsonAppUpdate(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonAppUpdate(os.path.join(tmp, "app_update.json"))
            self.assertIsNone(store.get())
            when = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
            store.set(AppRelease(version="0.10.0", notes="n", url="u"), when)
            release, checked_at = store.get()
            self.assertEqual((release.version, release.notes, release.url), ("0.10.0", "n", "u"))
            self.assertEqual(checked_at, when)

    def test_corrupt_file_gives_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "app_update.json")
            with open(path, "w") as fh:
                fh.write("{pas du json")
            self.assertIsNone(JsonAppUpdate(path).get())


class _FakeCatalog:
    def __init__(self, release):
        self.release = release
        self.calls = 0

    def latest(self):
        self.calls += 1
        return self.release


class _FakeState:
    def __init__(self):
        self.value = None

    def get(self):
        return self.value

    def set(self, release, checked_at):
        self.value = (release, checked_at)


class TestAppUpdateChecker(unittest.TestCase):
    def _checker(self, catalog, state, now):
        return AppUpdateChecker(catalog, state, clock=lambda: now[0])

    def test_first_tick_fetches_and_persists(self):
        now = [datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)]
        catalog = _FakeCatalog(AppRelease(version="0.10.0"))
        state = _FakeState()
        self.assertTrue(self._checker(catalog, state, now).tick())
        self.assertEqual(state.value[0].version, "0.10.0")

    def test_fresh_verdict_throttles_24h(self):
        now = [datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)]
        catalog = _FakeCatalog(AppRelease(version="0.10.0"))
        state = _FakeState()
        checker = self._checker(catalog, state, now)
        checker.tick()
        now[0] += timedelta(hours=23)
        self.assertFalse(checker.tick())                           # < 24 h : pas d'appel
        self.assertEqual(catalog.calls, 1)
        now[0] += timedelta(hours=2)
        self.assertTrue(checker.tick())                            # > 24 h : nouvelle passe
        self.assertEqual(catalog.calls, 2)

    def test_network_error_keeps_previous_verdict(self):
        now = [datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)]
        state = _FakeState()
        checker = self._checker(_FakeCatalog(AppRelease(version="0.10.0")), state, now)
        checker.tick()
        now[0] += timedelta(hours=25)
        broken = AppUpdateChecker(_FakeCatalog(None), state, clock=lambda: now[0])
        self.assertFalse(broken.tick())
        self.assertEqual(state.value[0].version, "0.10.0")         # verdict conservé


if __name__ == "__main__":
    unittest.main()
