"""Tests de l'adapter DockerProxyContainer (client docker-py factice)."""
from __future__ import annotations

import unittest

from adapters.docker_proxy import DockerProxyContainer, ensure_authorized
from domain.errors import ServerUnavailable, UnauthorizedContainer


class FakeContainerObj:
    def __init__(self, name="minecraft", status="running", health=None):
        self.name = name
        self.status = status
        self.attrs = {"State": {"Health": {"Status": health}} if health else {}}
        self.restarted = self.started = self.stopped = False

    def restart(self):
        self.restarted = True

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class FakeContainers:
    def __init__(self, mapping):
        self._m = mapping

    def get(self, name):
        if name not in self._m:
            raise KeyError(name)  # simule docker.errors.NotFound
        return self._m[name]


class FakeClient:
    def __init__(self, mapping):
        self.containers = FakeContainers(mapping)


def _adapter(container=None, name="minecraft"):
    container = container or FakeContainerObj(name=name)
    return DockerProxyContainer(name, client=FakeClient({name: container})), container


class TestEnsureAuthorized(unittest.TestCase):
    def test_allows_matching_target(self):
        ensure_authorized("minecraft", "minecraft")  # ne lève pas

    def test_rejects_other_target(self):
        with self.assertRaises(UnauthorizedContainer):
            ensure_authorized("adguardhome", "minecraft")


class TestStatus(unittest.TestCase):
    def test_running(self):
        adapter, _ = _adapter(FakeContainerObj(status="running"))
        state = adapter.status()
        self.assertTrue(state.running)
        self.assertEqual(state.status, "running")
        self.assertEqual(state.name, "minecraft")

    def test_stopped(self):
        adapter, _ = _adapter(FakeContainerObj(status="exited"))
        self.assertFalse(adapter.status().running)

    def test_health_extracted(self):
        adapter, _ = _adapter(FakeContainerObj(health="healthy"))
        self.assertEqual(adapter.status().health, "healthy")

    def test_missing_container_degrades_to_unavailable(self):
        adapter = DockerProxyContainer("minecraft", client=FakeClient({}))
        with self.assertRaises(ServerUnavailable):
            adapter.status()


class TestActions(unittest.TestCase):
    def test_restart(self):
        adapter, c = _adapter()
        adapter.restart()
        self.assertTrue(c.restarted)

    def test_start(self):
        adapter, c = _adapter()
        adapter.start()
        self.assertTrue(c.started)

    def test_stop(self):
        adapter, c = _adapter()
        adapter.stop()
        self.assertTrue(c.stopped)


if __name__ == "__main__":
    unittest.main()
