"""Tests de la transaction hors ligne appliquée à ops.json."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from adapters.atomic_json import atomic_write_json
from adapters.pending_op_levels import JsonPendingOpLevels
from domain.model import PendingOpLevel
from op_levels_worker import (
    OpLevelsWorkerError,
    _ROLLBACK_NAME,
    _STATE_NAME,
    _recover_interrupted,
    _write_state,
    apply_pending_levels,
)


class FakeController:
    def __init__(self, fail_first_start=False):
        self.fail_first_start = fail_first_start
        self.stops = 0
        self.starts = 0

    def stop(self):
        self.stops += 1

    def start(self):
        self.starts += 1
        if self.fail_first_start and self.starts == 1:
            raise RuntimeError("Minecraft unhealthy")


class TestOpLevelsWorker(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.data = root / "worlddata"
        self.state = root / "state"
        self.data.mkdir()
        self.state.mkdir()
        self.ops = self.data / "ops.json"
        self.ops.write_text(
            json.dumps(
                [
                    {
                        "uuid": "u1",
                        "name": "Trappeur",
                        "level": 4,
                        "bypassesPlayerLimit": False,
                    },
                    {"uuid": "u2", "name": "Rovandil", "level": 3},
                ]
            ),
            encoding="utf-8",
        )
        os.chmod(self.ops, 0o640)
        self.store = JsonPendingOpLevels(
            str(self.state / "pending_op_levels.json")
        )

    def tearDown(self):
        self.directory.cleanup()

    def _payload(self):
        return json.loads(self.ops.read_text(encoding="utf-8"))

    def test_applies_level_and_preserves_other_fields(self):
        self.store.set_level("Trappeur", 2)
        controller = FakeController()
        changed, unresolved = apply_pending_levels(
            self.store,
            self.data,
            controller,
        )
        self.assertEqual((changed, unresolved), (1, 0))
        self.assertEqual(self._payload()[0]["level"], 2)
        self.assertEqual(self._payload()[0]["uuid"], "u1")
        self.assertEqual(self._payload()[1]["level"], 3)
        self.assertEqual(stat.S_IMODE(self.ops.stat().st_mode), 0o640)
        self.assertEqual(self.store.list_pending(), [])
        self.assertEqual((controller.stops, controller.starts), (1, 1))
        self.assertFalse((self.data / _ROLLBACK_NAME).exists())
        self.assertFalse((self.data / _STATE_NAME).exists())

    def test_missing_operator_remains_pending_without_blocking_restart(self):
        self.store.set_level("Absent", 2)
        controller = FakeController()
        changed, unresolved = apply_pending_levels(
            self.store,
            self.data,
            controller,
        )
        self.assertEqual((changed, unresolved), (0, 1))
        self.assertEqual(self.store.list_pending()[0].name, "Absent")
        self.assertEqual((controller.stops, controller.starts), (1, 1))

    def test_unhealthy_start_restores_original_and_requeues_request(self):
        original = self.ops.read_bytes()
        self.store.set_level("Trappeur", 2)
        controller = FakeController(fail_first_start=True)
        with self.assertRaises(OpLevelsWorkerError):
            apply_pending_levels(self.store, self.data, controller)
        self.assertEqual(self.ops.read_bytes(), original)
        self.assertEqual(self.store.list_pending()[0].level, 2)
        self.assertEqual((controller.stops, controller.starts), (2, 2))

    def test_malformed_ops_starts_server_and_requeues_without_overwrite(self):
        self.ops.write_text("{broken", encoding="utf-8")
        self.store.set_level("Trappeur", 2)
        controller = FakeController()
        with self.assertRaises(OpLevelsWorkerError):
            apply_pending_levels(self.store, self.data, controller)
        self.assertEqual(self.ops.read_text(encoding="utf-8"), "{broken")
        self.assertEqual(self.store.list_pending()[0].level, 2)
        self.assertEqual((controller.stops, controller.starts), (1, 1))

    def test_interrupted_applied_transaction_is_rolled_back(self):
        original = self.ops.read_bytes()
        self.store.set_level("Trappeur", 2)
        self.store.claim()
        modified = self._payload()
        modified[0]["level"] = 2
        self.ops.write_text(json.dumps(modified), encoding="utf-8")
        (self.data / _ROLLBACK_NAME).write_bytes(original)
        os.chmod(self.data / _ROLLBACK_NAME, 0o640)
        atomic_write_json(
            str(self.data / _STATE_NAME),
            {"version": 1, "phase": "applied"},
            mode=0o600,
        )
        controller = FakeController()
        with self.assertRaises(OpLevelsWorkerError):
            apply_pending_levels(self.store, self.data, controller)
        self.assertEqual(self.ops.read_bytes(), original)
        self.assertEqual(self.store.list_pending()[0].level, 2)
        self.assertEqual((controller.stops, controller.starts), (1, 1))

    def test_interrupted_commit_preserves_unresolved_requests(self):
        original = self.ops.read_bytes()
        self.store.set_level("Trappeur", 2)
        self.store.set_level("Missing", 3)
        requested = self.store.claim()
        modified = self._payload()
        modified[0]["level"] = 2
        self.ops.write_text(json.dumps(modified), encoding="utf-8")
        rollback = self.data / _ROLLBACK_NAME
        rollback.write_bytes(original)
        os.chmod(rollback, 0o640)
        state_path = self.data / _STATE_NAME
        unresolved = [item for item in requested if item.name == "Missing"]
        _write_state(state_path, "committed", requested, unresolved)
        controller = FakeController()

        _recover_interrupted(
            self.store,
            self.ops,
            rollback,
            state_path,
            controller,
        )

        self.assertEqual(self._payload()[0]["level"], 2)
        self.assertEqual(
            self.store.list_pending(),
            [PendingOpLevel("Missing", 3)],
        )
        self.assertEqual((controller.stops, controller.starts), (0, 0))
        self.assertFalse(rollback.exists())
        self.assertFalse(state_path.exists())

    def test_empty_claim_still_honors_requested_restart(self):
        controller = FakeController()
        self.assertEqual(
            apply_pending_levels(self.store, self.data, controller),
            (0, 0),
        )
        self.assertEqual((controller.stops, controller.starts), (1, 1))


if __name__ == "__main__":
    unittest.main()
