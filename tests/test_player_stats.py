"""Tests de FilePlayerStats (fichiers temporaires réels, pas de serveur)."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from adapters.player_stats import FilePlayerStats

UUID_ALICE = "6e5fc5ec-afcd-42e4-8ecf-9325a0f94670"
UUID_BOB = "58c77268-829c-4aa4-97e8-ddf8a78acf26"


def _stats_payload(play_time_ticks=1209490):
    return {
        "stats": {
            "minecraft:custom": {
                "minecraft:play_time": play_time_ticks,
                "minecraft:deaths": 14,
                "minecraft:mob_kills": 91,
                "minecraft:walk_one_cm": 3_000_000,
                "minecraft:sprint_one_cm": 454_828,
                "minecraft:jump": 6694,
            },
            "minecraft:mined": {"minecraft:stone": 1000, "minecraft:dirt": 234},
            "minecraft:crafted": {"minecraft:stick": 200, "minecraft:torch": 10},
        },
        "DataVersion": 4440,
    }


class TestFilePlayerStats(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.usercache = os.path.join(self.dir, "usercache.json")
        with open(self.usercache, "w", encoding="utf-8") as fh:
            json.dump([{"uuid": UUID_ALICE, "name": "alice", "expiresOn": "2026-08-12 23:53:07 +0200"}], fh)
        self.stats_dir = os.path.join(self.dir, "stats")
        os.mkdir(self.stats_dir)
        self._write(UUID_ALICE, _stats_payload())
        self.adapter = FilePlayerStats(self.stats_dir, self.usercache)

    def _write(self, uuid, payload):
        with open(os.path.join(self.stats_dir, f"{uuid}.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def test_parses_and_converts_units(self):
        (st,) = self.adapter.all_stats()
        self.assertEqual(st.player, "alice")
        self.assertAlmostEqual(st.play_time_seconds, 1209490 / 20)  # ticks -> s
        self.assertEqual(st.deaths, 14)
        self.assertEqual(st.mob_kills, 91)
        self.assertEqual(st.player_kills, 0)              # clé absente -> 0
        self.assertEqual(st.distance_cm, 3_454_828)       # somme des *_one_cm
        self.assertEqual(st.blocks_mined, 1234)
        self.assertEqual(st.items_crafted, 210)

    def test_unknown_uuid_falls_back_to_short_uuid(self):
        self._write(UUID_BOB, _stats_payload())
        names = {st.player for st in self.adapter.all_stats()}
        self.assertIn("alice", names)
        self.assertIn(UUID_BOB[:8], names)                # absent du usercache

    def test_corrupt_file_is_skipped_not_fatal(self):
        with open(os.path.join(self.stats_dir, f"{UUID_BOB}.json"), "w") as fh:
            fh.write("{pas du json")
        self.assertEqual(len(self.adapter.all_stats()), 1)  # alice seulement

    def test_missing_dir_and_usercache_degrade(self):
        adapter = FilePlayerStats("/nonexistent/stats", "/nonexistent/usercache.json")
        self.assertEqual(adapter.all_stats(), [])
        # usercache absent mais stats présentes : repli uuid, pas d'erreur
        adapter = FilePlayerStats(self.stats_dir, "/nonexistent/usercache.json")
        (st,) = adapter.all_stats()
        self.assertEqual(st.player, UUID_ALICE[:8])




class TestAliases(unittest.TestCase):
    """Résolution via le fichier d'alias d'époque (import Nitroserv)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.stats_dir = os.path.join(self.dir.name, "stats")
        os.makedirs(self.stats_dir)
        self.usercache = os.path.join(self.dir.name, "usercache.json")
        with open(self.usercache, "w") as fh:
            json.dump([{"uuid": UUID_ALICE, "name": "alice"}], fh)
        self.aliases = os.path.join(self.dir.name, "legacy-player-names.json")
        with open(self.aliases, "w") as fh:
            json.dump({"_source": "test", "aliases": {UUID_BOB: "FrenchEmperor02",
                                                      UUID_ALICE: "vieux-nom"}}, fh)
        self._write(UUID_ALICE, _stats_payload())
        self._write(UUID_BOB, _stats_payload())

    def _write(self, uuid, payload):
        with open(os.path.join(self.stats_dir, f"{uuid}.json"), "w") as fh:
            json.dump(payload, fh)

    def test_alias_resolves_expired_uuid_and_usercache_wins(self):
        adapter = FilePlayerStats(self.stats_dir, self.usercache, self.aliases)
        by_uuid = {st.uuid: st for st in adapter.all_stats()}
        # Bob a expiré du usercache : l'alias d'époque donne son pseudo.
        self.assertEqual(by_uuid[UUID_BOB].player, "FrenchEmperor02")
        # Alice est dans le usercache (plus frais) : il PRIME sur l'alias.
        self.assertEqual(by_uuid[UUID_ALICE].player, "alice")

    def test_uuid_and_last_played_are_exposed(self):
        adapter = FilePlayerStats(self.stats_dir, self.usercache, self.aliases)
        (st, _) = sorted(adapter.all_stats(), key=lambda s: s.uuid != UUID_ALICE)
        self.assertEqual(st.uuid, UUID_ALICE)
        self.assertIsNotNone(st.last_played)   # mtime du fichier de stats

    def test_corrupt_aliases_degrade_to_usercache(self):
        with open(self.aliases, "w") as fh:
            fh.write("{pas du json")
        adapter = FilePlayerStats(self.stats_dir, self.usercache, self.aliases)
        by_uuid = {st.uuid: st for st in adapter.all_stats()}
        self.assertEqual(by_uuid[UUID_ALICE].player, "alice")
        self.assertEqual(by_uuid[UUID_BOB].player, UUID_BOB[:8])  # repli tronqué


if __name__ == "__main__":
    unittest.main()
