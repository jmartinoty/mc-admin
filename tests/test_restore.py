"""Tests de l'adapter RestorePort (client docker-py factice)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adapters.restore import DockerRestoreTrigger, NotConfiguredRestore
from domain.errors import RestoreUnavailable, UnauthorizedContainer


class FakeRestoreContainer:
    def __init__(self, name="mc-restore", status="exited", exit_code=0):
        self.name = name
        self.status = status
        self.attrs = {"State": {"ExitCode": exit_code}}
        self.started = False

    def start(self):
        self.started = True


class FakeContainers:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, name):
        if name not in self._mapping:
            raise KeyError(name)
        return self._mapping[name]


class FakeClient:
    def __init__(self, mapping):
        self.containers = FakeContainers(mapping)


class TestDockerRestoreTrigger(unittest.TestCase):
    def test_restore_writes_target_before_start(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "restore-target"
            container = FakeRestoreContainer()
            adapter = DockerRestoreTrigger(
                "mc-restore",
                str(target),
                client=FakeClient({"mc-restore": container}),
            )

            adapter.restore("manual/world.tar.gz")

            self.assertEqual(target.read_text(encoding="utf-8"), "manual/world.tar.gz")
            self.assertTrue(container.started)

    def test_running_restore_is_rejected_without_rewriting_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "restore-target"
            target.write_text("existing.tar.gz", encoding="utf-8")
            container = FakeRestoreContainer(status="running")
            adapter = DockerRestoreTrigger(
                "mc-restore",
                str(target),
                client=FakeClient({"mc-restore": container}),
            )

            with self.assertRaises(RestoreUnavailable):
                adapter.restore("other.tar.gz")

            self.assertEqual(target.read_text(encoding="utf-8"), "existing.tar.gz")
            self.assertFalse(container.started)

    def test_exit_code_distinguishes_success_failure_and_running(self):
        successful = DockerRestoreTrigger(
            "mc-restore",
            "/unused",
            client=FakeClient({"mc-restore": FakeRestoreContainer(exit_code=0)}),
        )
        failed = DockerRestoreTrigger(
            "mc-restore",
            "/unused",
            client=FakeClient({"mc-restore": FakeRestoreContainer(exit_code=8)}),
        )
        running = DockerRestoreTrigger(
            "mc-restore",
            "/unused",
            client=FakeClient({"mc-restore": FakeRestoreContainer(status="running")}),
        )

        self.assertEqual(successful.exit_code(), 0)
        self.assertEqual(failed.exit_code(), 8)
        self.assertIsNone(running.exit_code())

    def test_wrong_target_name_is_rejected(self):
        rogue = FakeRestoreContainer(name="other")
        adapter = DockerRestoreTrigger(
            "mc-restore",
            "/unused",
            client=FakeClient({"mc-restore": rogue}),
        )

        with self.assertRaises(UnauthorizedContainer):
            adapter.exit_code()

    def test_not_configured_never_reports_success(self):
        adapter = NotConfiguredRestore()

        self.assertFalse(adapter.is_running())
        self.assertIsNone(adapter.exit_code())
        with self.assertRaises(RestoreUnavailable):
            adapter.restore("manual/world.tar.gz")


if __name__ == "__main__":
    unittest.main()
