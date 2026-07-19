"""Tests du déclencheur Docker des niveaux OP."""
from __future__ import annotations

import unittest

from adapters.op_levels import DockerOpLevelsApply
from domain.errors import OpLevelsUnavailable, UnauthorizedContainer


class FakeContainer:
    def __init__(self, name="mc-op-levels", status="exited", exit_code=0):
        self.name = name
        self.status = status
        self.attrs = {"State": {"ExitCode": exit_code}}
        self.started = False

    def start(self):
        self.started = True


class FakeContainers:
    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, name):
        return self.mapping[name]


class FakeClient:
    def __init__(self, mapping):
        self.containers = FakeContainers(mapping)


class TestDockerOpLevelsApply(unittest.TestCase):
    def test_starts_configured_worker(self):
        container = FakeContainer()
        adapter = DockerOpLevelsApply(
            "mc-op-levels",
            client=FakeClient({"mc-op-levels": container}),
        )
        adapter.apply()
        self.assertTrue(container.started)

    def test_rejects_already_running_worker(self):
        container = FakeContainer(status="running")
        adapter = DockerOpLevelsApply(
            "mc-op-levels",
            client=FakeClient({"mc-op-levels": container}),
        )
        with self.assertRaises(OpLevelsUnavailable):
            adapter.apply()
        self.assertFalse(container.started)

    def test_rejects_resolved_container_with_wrong_name(self):
        container = FakeContainer(name="other")
        adapter = DockerOpLevelsApply(
            "mc-op-levels",
            client=FakeClient({"mc-op-levels": container}),
        )
        with self.assertRaises(UnauthorizedContainer):
            adapter.apply()

    def test_reports_running_state_without_exit_code(self):
        container = FakeContainer(status="running", exit_code=7)
        adapter = DockerOpLevelsApply(
            "mc-op-levels",
            client=FakeClient({"mc-op-levels": container}),
        )
        self.assertTrue(adapter.is_running())
        self.assertIsNone(adapter.exit_code())

    def test_reports_worker_exit_code(self):
        container = FakeContainer(status="exited", exit_code=7)
        adapter = DockerOpLevelsApply(
            "mc-op-levels",
            client=FakeClient({"mc-op-levels": container}),
        )
        self.assertFalse(adapter.is_running())
        self.assertEqual(adapter.exit_code(), 7)


if __name__ == "__main__":
    unittest.main()
