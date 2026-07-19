"""Tests des adapters BackupPort (client docker-py factice)."""
from __future__ import annotations

import unittest

from adapters.backup import DockerBackupTrigger
from domain.errors import BackupUnavailable, UnauthorizedContainer


class FakeBackupContainer:
    def __init__(self, name="mc-backup", status="exited", exit_code=0):
        self.name = name
        self.status = status
        self.attrs = {"State": {"ExitCode": exit_code}}
        self.started = False

    def start(self):
        self.started = True


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


def _trigger(container=None, name="mc-backup"):
    container = container or FakeBackupContainer(name=name)
    adapter = DockerBackupTrigger(name, client=FakeClient({name: container}))
    return adapter, container


class TestDockerBackupTrigger(unittest.TestCase):
    def test_starts_stopped_container(self):
        adapter, container = _trigger(FakeBackupContainer(status="exited"))
        result = adapter.trigger()
        self.assertTrue(result.started)
        self.assertTrue(container.started)

    def test_already_running_does_not_restart(self):
        adapter, container = _trigger(FakeBackupContainer(status="running"))
        result = adapter.trigger()
        self.assertFalse(result.started)
        self.assertFalse(container.started)
        self.assertIn("en cours", result.detail)

    def test_missing_container_raises_backup_unavailable(self):
        adapter = DockerBackupTrigger("mc-backup", client=FakeClient({}))
        with self.assertRaises(BackupUnavailable):
            adapter.trigger()

    def test_wrong_target_name_is_rejected(self):
        # Le conteneur renvoyé porte un autre nom que celui configuré -> refus.
        rogue = FakeBackupContainer(name="evil")
        adapter = DockerBackupTrigger("mc-backup", client=FakeClient({"mc-backup": rogue}))
        with self.assertRaises(UnauthorizedContainer):
            adapter.trigger()
        self.assertFalse(rogue.started)

    def test_exit_code_distinguishes_success_and_failure(self):
        successful, _ = _trigger(FakeBackupContainer(exit_code=0))
        failed, _ = _trigger(FakeBackupContainer(exit_code=7))

        self.assertEqual(successful.exit_code(), 0)
        self.assertEqual(failed.exit_code(), 7)

    def test_exit_code_is_unknown_while_running(self):
        adapter, _ = _trigger(FakeBackupContainer(status="running", exit_code=0))

        self.assertIsNone(adapter.exit_code())


if __name__ == "__main__":
    unittest.main()
