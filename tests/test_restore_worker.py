"""Tests du worker mc-restore transactionnel, sans Docker réel."""
from __future__ import annotations

import json
import os
import tarfile
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import restore_worker
from restore_worker import (
    DockerContainerController,
    RestoreWorkerError,
    restore_transaction,
)


class FakeController:
    def __init__(self, start_failures=0):
        self.running = True
        self.start_failures = start_failures
        self.events: list[str] = []

    def stop(self):
        self.events.append("stop")
        self.running = False

    def start(self):
        self.events.append("start")
        if self.start_failures:
            self.start_failures -= 1
            raise RuntimeError("démarrage Docker refusé")
        self.running = True


class FakeDockerContainer:
    def __init__(self, states, *, healthcheck=True):
        self.name = "minecraft"
        self.status = "exited"
        self.starts = 0
        self._states = list(states)
        self.attrs = {
            "Config": {
                "Healthcheck": {"Test": ["CMD", "check"]}
                if healthcheck
                else {}
            },
            "State": {"Status": "exited"},
        }

    def reload(self):
        if self.starts and self._states:
            status, health = self._states.pop(0)
            self.status = status
            state = {"Status": status}
            if health is not None:
                state["Health"] = {"Status": health}
            self.attrs["State"] = state

    def start(self):
        self.starts += 1


class FakeMonotonic:
    def __init__(self):
        self.now = 0.0
        self.sleeps = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += seconds


class TestRestoreWorker(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.backups = self.root / "backups"
        self.target = self.root / "restore-target"
        self.data.mkdir()
        (self.backups / "manual").mkdir(parents=True)
        self._seed_current_world()
        self.archive = self._make_archive()
        self.target.write_text("manual/world.tar.gz", encoding="utf-8")

    def _seed_current_world(self):
        (self.data / "old_world_1").mkdir()
        (self.data / "old_world_1" / "level.dat").write_bytes(b"old-level")
        (self.data / "old_world_1" / "stale-region.mca").write_bytes(b"stale")
        (self.data / "server.properties").write_text(
            "level-name=old_world_1\n", encoding="utf-8"
        )
        (self.data / "ops.json").write_text("old-ops", encoding="utf-8")
        (self.data / "stale.txt").write_text("must disappear", encoding="utf-8")
        (self.data / "logs").mkdir()
        (self.data / "logs" / "latest.log").write_text("old-log", encoding="utf-8")

    def _make_archive(self, name="world.tar.gz", members=None):
        path = self.backups / "manual" / name
        contents = members or {
            "server.properties": b"level-name=old_world_1\n",
            "old_world_1/level.dat": b"new-level",
            "ops.json": b"new-ops",
        }
        with tarfile.open(path, "w:gz") as archive:
            for member_name, content in contents.items():
                member = tarfile.TarInfo(member_name)
                member.size = len(content)
                member.mode = 0o640
                member.uid = os.getuid()
                member.gid = os.getgid()
                archive.addfile(member, BytesIO(content))
        return path

    def test_success_replaces_selected_tree_and_preserves_bound_inodes(self):
        controller = FakeController()
        ops_inode = (self.data / "ops.json").stat().st_ino
        logs_inode = (self.data / "logs").stat().st_ino
        world_inode = (self.data / "old_world_1").stat().st_ino

        restored = restore_transaction(
            self.target, self.backups, self.data, controller
        )

        self.assertEqual(restored, "manual/world.tar.gz")
        self.assertEqual(controller.events, ["stop", "start"])
        self.assertEqual((self.data / "old_world_1" / "level.dat").read_bytes(), b"new-level")
        self.assertEqual((self.data / "ops.json").read_text(encoding="utf-8"), "new-ops")
        self.assertEqual((self.data / "ops.json").stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.data / "ops.json").stat().st_uid, os.getuid())
        self.assertEqual((self.data / "logs" / "latest.log").read_text(encoding="utf-8"), "old-log")
        self.assertFalse((self.data / "old_world_1" / "stale-region.mca").exists())
        self.assertTrue((self.data / "stale.txt").exists())
        self.assertEqual((self.data / "ops.json").stat().st_ino, ops_inode)
        self.assertEqual((self.data / "logs").stat().st_ino, logs_inode)
        self.assertEqual((self.data / "old_world_1").stat().st_ino, world_inode)
        self._assert_transaction_clean()

    def test_corrupt_archive_never_stops_minecraft(self):
        self.archive.write_bytes(b"not a tar archive")
        controller = FakeController()

        with self.assertRaises(RestoreWorkerError):
            restore_transaction(self.target, self.backups, self.data, controller)

        self.assertEqual(controller.events, [])
        self.assertEqual((self.data / "old_world_1" / "level.dat").read_bytes(), b"old-level")
        self._assert_transaction_clean()

    def test_target_path_traversal_is_rejected_before_stop(self):
        self.target.write_text("../outside.tar.gz", encoding="utf-8")
        controller = FakeController()

        with self.assertRaises(RestoreWorkerError):
            restore_transaction(self.target, self.backups, self.data, controller)

        self.assertEqual(controller.events, [])

    def test_apply_failure_restores_old_tree_and_restarts(self):
        controller = FakeController()
        real_sync = restore_worker._sync_tree

        def fail_during_apply(
            source,
            destination,
            protected=frozenset(),
            *,
            remove_absent=True,
        ):
            if source.name == ".mc-restore-stage":
                (destination / "old_world_1" / "level.dat").write_bytes(b"partial")
                (destination / "ops.json").unlink()
                raise OSError("disque indisponible")
            return real_sync(
                source,
                destination,
                protected,
                remove_absent=remove_absent,
            )

        with self.assertRaisesRegex(RestoreWorkerError, "rollback appliqué"):
            restore_transaction(
                self.target,
                self.backups,
                self.data,
                controller,
                sync_tree=fail_during_apply,
            )

        self.assertTrue(controller.running)
        self.assertEqual((self.data / "old_world_1" / "level.dat").read_bytes(), b"old-level")
        self.assertEqual((self.data / "ops.json").read_text(encoding="utf-8"), "old-ops")
        self.assertEqual((self.data / "ops.json").stat().st_uid, os.getuid())
        self.assertTrue((self.data / "stale.txt").exists())
        self._assert_transaction_clean()

    def test_start_failure_rolls_back_then_restarts_old_world(self):
        controller = FakeController(start_failures=1)

        with self.assertRaisesRegex(RestoreWorkerError, "rollback appliqué"):
            restore_transaction(self.target, self.backups, self.data, controller)

        self.assertTrue(controller.running)
        self.assertEqual((self.data / "old_world_1" / "level.dat").read_bytes(), b"old-level")
        self.assertEqual(controller.events, ["stop", "start", "stop", "start"])
        self._assert_transaction_clean()

    def test_interrupted_apply_is_recovered_on_next_start(self):
        rollback = self.data / ".mc-restore-rollback"
        rollback.mkdir()
        (rollback / "old_world_1").mkdir()
        (rollback / "old_world_1" / "level.dat").write_bytes(b"old-level")
        (rollback / "server.properties").write_text(
            "level-name=old_world_1\n", encoding="utf-8"
        )
        (self.data / "old_world_1" / "level.dat").write_bytes(b"partial")
        (self.data / ".mc-restore-transaction.json").write_text(
            json.dumps({
                "version": 1,
                "phase": "applying",
                "target": "manual/world.tar.gz",
            }),
            encoding="utf-8",
        )
        controller = FakeController()

        with self.assertRaisesRegex(RestoreWorkerError, "transaction interrompue"):
            restore_transaction(self.target, self.backups, self.data, controller)

        self.assertTrue(controller.running)
        self.assertEqual((self.data / "old_world_1" / "level.dat").read_bytes(), b"old-level")
        self._assert_transaction_clean()

    def test_interrupted_stop_restarts_untouched_server(self):
        (self.data / ".mc-restore-stage").mkdir()
        (self.data / ".mc-restore-transaction.json").write_text(
            json.dumps({
                "version": 1,
                "phase": "stopping",
                "target": "manual/world.tar.gz",
            }),
            encoding="utf-8",
        )
        controller = FakeController()
        controller.running = False

        with self.assertRaisesRegex(RestoreWorkerError, "avant application"):
            restore_transaction(self.target, self.backups, self.data, controller)

        self.assertTrue(controller.running)
        self.assertEqual(controller.events, ["start"])
        self.assertEqual((self.data / "old_world_1" / "level.dat").read_bytes(), b"old-level")
        self._assert_transaction_clean()

    def test_interrupted_apply_without_rollback_fails_closed(self):
        state = self.data / ".mc-restore-transaction.json"
        state.write_text(
            json.dumps({
                "version": 1,
                "phase": "applying",
                "target": "manual/world.tar.gz",
            }),
            encoding="utf-8",
        )
        (self.data / "old_world_1" / "level.dat").write_bytes(b"partial")
        controller = FakeController()
        controller.running = False

        with self.assertRaisesRegex(RestoreWorkerError, "intervention manuelle"):
            restore_transaction(self.target, self.backups, self.data, controller)

        self.assertEqual(controller.events, [])
        self.assertTrue(state.exists())

    def test_crash_after_start_is_treated_as_completed(self):
        rollback = self.data / ".mc-restore-rollback"
        rollback.mkdir()
        (rollback / "old.txt").write_text("old", encoding="utf-8")
        (self.data / "old_world_1" / "level.dat").write_bytes(b"new-level")
        (self.data / ".mc-restore-transaction.json").write_text(
            json.dumps({
                "version": 1,
                "phase": "started",
                "target": "manual/world.tar.gz",
            }),
            encoding="utf-8",
        )
        controller = FakeController()

        restored = restore_transaction(
            self.target, self.backups, self.data, controller
        )

        self.assertEqual(restored, "manual/world.tar.gz")
        self.assertEqual(controller.events, [])
        self.assertEqual((self.data / "old_world_1" / "level.dat").read_bytes(), b"new-level")
        self._assert_transaction_clean()

    def _assert_transaction_clean(self):
        for name in (
            ".mc-restore-stage",
            ".mc-restore-rollback",
            ".mc-restore-transaction.json",
            ".mc-restore-transaction.json.tmp",
        ):
            self.assertFalse((self.data / name).exists(), name)


class TestDockerContainerController(unittest.TestCase):
    def _controller(self, container, clock, *, timeout=10):
        client = SimpleNamespace(
            containers=SimpleNamespace(get=lambda name: container)
        )
        return DockerContainerController(
            "minecraft",
            client,
            startup_timeout=timeout,
            poll_interval=1,
            sleep=clock.sleep,
            monotonic=clock,
        )

    def test_start_waits_for_healthy(self):
        container = FakeDockerContainer([
            ("running", "starting"),
            ("running", "healthy"),
        ])
        clock = FakeMonotonic()

        self._controller(container, clock).start()

        self.assertEqual(container.starts, 1)
        self.assertEqual(clock.sleeps, 1)

    def test_unhealthy_start_fails_before_transaction_cleanup(self):
        container = FakeDockerContainer([("running", "unhealthy")])
        clock = FakeMonotonic()

        with self.assertRaisesRegex(RestoreWorkerError, "unhealthy"):
            self._controller(container, clock).start()

    def test_exited_start_is_rejected(self):
        container = FakeDockerContainer([("exited", None)])
        clock = FakeMonotonic()

        with self.assertRaisesRegex(RestoreWorkerError, "arrêté"):
            self._controller(container, clock).start()

    def test_container_without_healthcheck_must_stay_running(self):
        container = FakeDockerContainer(
            [("running", None)],
            healthcheck=False,
        )
        clock = FakeMonotonic()

        self._controller(container, clock).start()

        self.assertEqual(clock.now, 2)

    def test_starting_container_times_out(self):
        container = FakeDockerContainer([("running", "starting")])
        clock = FakeMonotonic()

        with self.assertRaisesRegex(RestoreWorkerError, "délai"):
            self._controller(container, clock, timeout=2).start()


if __name__ == "__main__":
    unittest.main()
