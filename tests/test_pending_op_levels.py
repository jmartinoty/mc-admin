"""Tests du store transactionnel des niveaux OP en attente."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest

from adapters.atomic_json import CorruptJsonError
from adapters.pending_op_levels import JsonPendingOpLevels
from domain.errors import OpLevelsUnavailable
from domain.model import PendingOpLevel


class TestJsonPendingOpLevels(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, "pending_op_levels.json")
        self.store = JsonPendingOpLevels(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_set_is_case_insensitive_and_persistent(self):
        self.store.set_level("Trappeur", 2)
        self.store.set_level("trappeur", 3)
        self.assertEqual(
            JsonPendingOpLevels(self.path).list_pending(),
            [PendingOpLevel("trappeur", 3)],
        )
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_remove_cancels_pending_level(self):
        self.store.set_level("Trappeur", 2)
        self.store.remove("TRAPPEUR")
        self.assertEqual(self.store.list_pending(), [])

    def test_claim_marks_entries_as_applying(self):
        self.store.set_level("Trappeur", 2)
        claimed = self.store.claim()
        self.assertEqual(claimed, [PendingOpLevel("Trappeur", 2)])
        self.assertEqual(
            self.store.list_pending(),
            [PendingOpLevel("Trappeur", 2, applying=True)],
        )

    def test_complete_preserves_newer_requests_and_requeues_unresolved(self):
        self.store.set_level("Trappeur", 2)
        self.store.set_level("Missing", 3)
        claimed = self.store.claim()
        self.store.set_level("Trappeur", 1)
        unresolved = [item for item in claimed if item.name == "Missing"]
        self.store.complete_claim(unresolved)
        self.assertEqual(
            self.store.list_pending(),
            [
                PendingOpLevel("Missing", 3),
                PendingOpLevel("Trappeur", 1),
            ],
        )

    def test_restore_claim_keeps_newer_request(self):
        self.store.set_level("Trappeur", 2)
        self.store.claim()
        self.store.set_level("Trappeur", 1)
        self.store.restore_claim()
        self.assertEqual(
            self.store.list_pending(),
            [PendingOpLevel("Trappeur", 1)],
        )

    def test_restore_claim_can_rebuild_an_already_cleared_applying_file(self):
        self.store.set_level("Trappeur", 2)
        claimed = self.store.claim()
        self.store.complete_claim()
        self.store.restore_claim(claimed)
        self.assertEqual(
            self.store.list_pending(),
            [PendingOpLevel("Trappeur", 2)],
        )

    def test_mutation_refuses_to_overwrite_corrupt_state(self):
        with open(self.path, "w", encoding="utf-8") as output:
            output.write("{broken")
        with self.assertRaises(CorruptJsonError):
            self.store.set_level("Trappeur", 2)
        with open(self.path, encoding="utf-8") as input_file:
            self.assertEqual(input_file.read(), "{broken")

    def test_invalid_entry_blocks_read_instead_of_hiding_pending_state(self):
        with open(self.path, "w", encoding="utf-8") as output:
            json.dump(
                {
                    "version": 1,
                    "levels": {
                        "trappeur": {"name": "Trappeur", "level": 9},
                    },
                },
                output,
            )
        with self.assertRaises(OpLevelsUnavailable):
            self.store.list_pending()


if __name__ == "__main__":
    unittest.main()
