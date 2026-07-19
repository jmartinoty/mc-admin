"""Tests de l'adapter MinecraftUpdater (fetch et client Docker factices)."""
from __future__ import annotations

import unittest

from adapters.updater import MOJANG_MANIFEST_URL, MinecraftUpdater, is_newer
from domain.errors import ServerUnavailable, UpdateUnavailable

from tests.test_docker_proxy import FakeClient, FakeContainerObj


def _prom_payload(version):
    return {"status": "success", "data": {"result": [{"metric": {"server_version": version}, "value": [0, "1"]}]}}


def _manifest(release):
    return {"latest": {"release": release, "snapshot": "whatever"}}


def _fetch(current="26.1", latest="26.2", manifest_error=False):
    def fetch(url):
        if url == MOJANG_MANIFEST_URL:
            if manifest_error:
                raise OSError("réseau down")
            return _manifest(latest)
        return _prom_payload(current)
    return fetch


class TestIsNewer(unittest.TestCase):
    def test_numeric_comparison(self):
        self.assertTrue(is_newer("26.2", "26.1"))
        self.assertFalse(is_newer("26.1", "26.1"))
        self.assertFalse(is_newer("26.1", "26.2"))   # serveur en avance != update
        self.assertTrue(is_newer("26.10", "26.9"))   # pas de comparaison lexicale

    def test_non_numeric_falls_back_to_difference(self):
        self.assertTrue(is_newer("26.2", "26.2-snapshot"))
        self.assertFalse(is_newer("26.2", "26.2"))


class TestCheck(unittest.TestCase):
    def test_update_available(self):
        upd = MinecraftUpdater("http://prom:9090", "mc-updater", fetch=_fetch("26.1", "26.2"))
        status = upd.check()
        self.assertEqual((status.current_version, status.latest_version), ("26.1", "26.2"))
        self.assertTrue(status.update_available)
        self.assertEqual(status.changelog_url, "https://minecraft.wiki/w/Java_Edition_26.2")

    def test_up_to_date(self):
        upd = MinecraftUpdater("http://prom:9090", "mc-updater", fetch=_fetch("26.2", "26.2"))
        self.assertFalse(upd.check().update_available)

    def test_current_version_unknown_degrades(self):
        def fetch(url):
            if url == MOJANG_MANIFEST_URL:
                return _manifest("26.2")
            raise OSError("prometheus down")
        upd = MinecraftUpdater("http://prom:9090", "mc-updater", fetch=fetch)
        status = upd.check()
        self.assertIsNone(status.current_version)
        self.assertFalse(status.update_available)  # pas d'affirmation sans les deux versions

    def test_manifest_unreachable_raises(self):
        upd = MinecraftUpdater("http://prom:9090", "mc-updater", fetch=_fetch(manifest_error=True))
        with self.assertRaises(ServerUnavailable):
            upd.check()

    def test_check_is_cached(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return _manifest("26.2") if url == MOJANG_MANIFEST_URL else _prom_payload("26.1")
        now = [0.0]
        upd = MinecraftUpdater("http://prom:9090", "mc-updater", fetch=fetch, clock=lambda: now[0])
        upd.check()
        upd.check()                       # dans le TTL -> pas de nouveau fetch
        self.assertEqual(len(calls), 2)   # 1 prometheus + 1 mojang
        now[0] = 120.0                    # TTL expiré
        upd.check()
        self.assertEqual(len(calls), 4)


class TestApply(unittest.TestCase):
    def _updater(self, container):
        client = FakeClient({container.name: container})
        return MinecraftUpdater("http://prom:9090", container.name, fetch=_fetch(), client=client)

    def test_apply_starts_stopped_updater(self):
        c = FakeContainerObj(name="mc-updater", status="exited")
        self._updater(c).apply()
        self.assertTrue(c.started)

    def test_apply_refuses_when_already_running(self):
        c = FakeContainerObj(name="mc-updater", status="running")
        with self.assertRaises(UpdateUnavailable):
            self._updater(c).apply()
        self.assertFalse(c.started)

    def test_apply_missing_container_raises_update_unavailable(self):
        upd = MinecraftUpdater("http://prom:9090", "mc-updater", fetch=_fetch(), client=FakeClient({}))
        with self.assertRaises(UpdateUnavailable):
            upd.apply()


if __name__ == "__main__":
    unittest.main()
