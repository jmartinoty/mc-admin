"""Tests du registre de sessions (voir/révoquer les appareils)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from api.sessions import SessionRegistry


class _Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


class TestSessionRegistry(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)  # le registre crée le fichier à la demande
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))
        self.clock = _Clock()

    def _registry(self, **kwargs):
        kwargs.setdefault("clock", self.clock)
        return SessionRegistry(self.path, **kwargs)

    def test_register_then_valid(self):
        reg = self._registry()
        sid = reg.register("jeremy", "10.0.0.1", "Mozilla/5.0")
        self.assertTrue(reg.is_valid(sid, "jeremy"))
        self.assertFalse(reg.is_valid(sid, "paul"))  # pas la session d'un autre
        self.assertFalse(reg.is_valid("inconnu", "jeremy"))

    def test_revoke_invalidates(self):
        reg = self._registry()
        sid = reg.register("jeremy", "10.0.0.1", "ua")
        self.assertTrue(reg.revoke(sid, "jeremy"))
        self.assertFalse(reg.is_valid(sid, "jeremy"))
        self.assertFalse(reg.revoke(sid, "jeremy"))  # déjà parti

    def test_cannot_revoke_someone_elses_session(self):
        reg = self._registry()
        sid = reg.register("jeremy", "10.0.0.1", "ua")
        self.assertFalse(reg.revoke(sid, "paul"))
        self.assertTrue(reg.is_valid(sid, "jeremy"))

    def test_revoke_others_keeps_current(self):
        reg = self._registry()
        keep = reg.register("jeremy", "10.0.0.1", "ua")
        other1 = reg.register("jeremy", "10.0.0.2", "ua")
        other2 = reg.register("jeremy", "10.0.0.3", "ua")
        elsewhere = reg.register("paul", "10.0.0.9", "ua")

        self.assertEqual(reg.revoke_others(keep, "jeremy"), 2)
        self.assertTrue(reg.is_valid(keep, "jeremy"))
        self.assertFalse(reg.is_valid(other1, "jeremy"))
        self.assertFalse(reg.is_valid(other2, "jeremy"))
        self.assertTrue(reg.is_valid(elsewhere, "paul"))  # autre compte intact

    def test_list_for_is_scoped_and_sorted_by_last_seen(self):
        reg = self._registry()
        first = reg.register("jeremy", "10.0.0.1", "ua")
        self.clock.now += 10
        second = reg.register("jeremy", "10.0.0.2", "ua")
        reg.register("paul", "10.0.0.9", "ua")
        self.clock.now += 5
        reg.touch(first)  # first redevient le plus récent

        infos = reg.list_for("jeremy")
        self.assertEqual([i.sid for i in infos], [first, second])
        self.assertEqual({i.username for i in infos}, {"jeremy"})

    def test_expired_session_is_purged(self):
        reg = self._registry(ttl_seconds=100)
        sid = reg.register("jeremy", "10.0.0.1", "ua")
        self.clock.now += 101
        self.assertFalse(reg.is_valid(sid, "jeremy"))
        self.assertEqual(reg.list_for("jeremy"), [])

    def test_cap_evicts_oldest(self):
        reg = self._registry(max_per_user=2)
        s1 = reg.register("jeremy", "1", "ua")
        self.clock.now += 1
        s2 = reg.register("jeremy", "2", "ua")
        self.clock.now += 1
        s3 = reg.register("jeremy", "3", "ua")
        self.assertFalse(reg.is_valid(s1, "jeremy"))  # la plus ancienne tombe
        self.assertTrue(reg.is_valid(s2, "jeremy"))
        self.assertTrue(reg.is_valid(s3, "jeremy"))

    def test_persistence_survives_reload(self):
        # La révocation DOIT survivre à un redémarrage de mc-admin (le point
        # même de persister plutôt que de tout garder en mémoire).
        reg = self._registry()
        keep = reg.register("jeremy", "10.0.0.1", "ua")
        gone = reg.register("jeremy", "10.0.0.2", "ua")
        reg.revoke(gone, "jeremy")

        reloaded = self._registry()  # nouvelle instance, même fichier
        self.assertTrue(reloaded.is_valid(keep, "jeremy"))
        self.assertFalse(reloaded.is_valid(gone, "jeremy"))

    def test_file_is_0600(self):
        reg = self._registry()
        reg.register("jeremy", "10.0.0.1", "ua")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_tolerates_corrupt_file_on_load(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ pas du json")
        reg = self._registry()  # ne lève pas
        self.assertEqual(reg.list_for("jeremy"), [])

    def test_ignores_malformed_records(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({
                "good": {"username": "jeremy", "created_at": 1000.0},
                "no-user": {"created_at": 1000.0},
                "bad-type": "nope",
            }, fh)
        reg = self._registry()
        self.assertTrue(reg.is_valid("good", "jeremy"))
        self.assertEqual(len(reg.list_for("jeremy")), 1)


if __name__ == "__main__":
    unittest.main()
