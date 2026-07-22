"""Tests du store de jetons d'API (hashé, révocable)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from api.api_tokens import ApiTokenStore


class TestApiTokenStore(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def test_create_then_resolve(self):
        store = ApiTokenStore(self.path)
        token_id, raw = store.create("dashboard", "viewer")
        info = store.resolve(raw)
        self.assertIsNotNone(info)
        self.assertEqual(info.token_id, token_id)
        self.assertEqual(info.label, "dashboard")
        self.assertEqual(info.role, "viewer")

    def test_unknown_token_resolves_to_none(self):
        store = ApiTokenStore(self.path)
        store.create("dashboard", "viewer")
        self.assertIsNone(store.resolve("pas-le-bon"))
        self.assertIsNone(store.resolve(""))

    def test_secret_is_not_stored_in_clear(self):
        store = ApiTokenStore(self.path)
        _id, raw = store.create("dashboard", "viewer")
        on_disk = open(self.path, encoding="utf-8").read()
        self.assertNotIn(raw, on_disk)  # seul le hash est écrit

    def test_revoke_invalidates(self):
        store = ApiTokenStore(self.path)
        token_id, raw = store.create("dashboard", "viewer")
        self.assertTrue(store.revoke(token_id))
        self.assertIsNone(store.resolve(raw))
        self.assertFalse(store.revoke(token_id))

    def test_list_orders_recent_first(self):
        store = ApiTokenStore(self.path)
        store.create("un", "viewer")
        store.create("deux", "admin")
        labels = [i.label for i in store.list()]
        self.assertCountEqual(labels, ["un", "deux"])
        self.assertEqual(len(labels), 2)

    def test_file_is_0600(self):
        store = ApiTokenStore(self.path)
        store.create("dashboard", "viewer")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_tolerates_corrupt_file_on_read(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("not json")
        store = ApiTokenStore(self.path)
        self.assertEqual(store.list(), [])
        self.assertIsNone(store.resolve("x"))

    def test_ignores_malformed_entries_on_resolve(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"bad": "nope", "empty": {}}, fh)
        store = ApiTokenStore(self.path)
        self.assertIsNone(store.resolve("x"))


if __name__ == "__main__":
    unittest.main()
