"""Tests JsonNotificationConfig V2 + StoreBackedNotifier par canal."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from adapters.atomic_json import CorruptJsonError
from adapters.notification_config import JsonNotificationConfig
from adapters.notifications import StoreBackedNotifier
from tests.fakes import FakeNotificationConfig


class TestJsonNotificationConfigV2(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)
        self.store = JsonNotificationConfig(os.path.join(self.dir, "notifications.json"))

    def test_crud_and_slug_collision(self):
        cid = self.store.add_channel("discord", "Copains", {"webhook_url": "https://d/1"},
                                     {"player": True})
        cid2 = self.store.add_channel("discord", "Copains", {"webhook_url": "https://d/2"},
                                      {"player": False})
        self.assertEqual((cid, cid2), ("copains", "copains-2"))
        self.assertTrue(self.store.update_channel(cid, enabled=False))
        self.assertFalse(self.store.get(cid)["enabled"])
        self.assertTrue(self.store.remove_channel(cid2))
        self.assertEqual([c["id"] for c in self.store.list_channels()], ["copains"])

    def test_v1_file_migrates_to_channel_profiles(self):
        with open(self.store._path, "w", encoding="utf-8") as fh:
            json.dump({"channels": {"discord_webhook_url": "https://d/hook",
                                    "telegram_bot_token": "", "telegram_chat_id": ""},
                       "events": {"player": True, "health": True, "restore": False},
                       "env_import_done": True}, fh)
        channels = self.store.list_channels()
        self.assertEqual(len(channels), 1)                          # telegram incomplet : ignoré
        ch = channels[0]
        self.assertEqual((ch["type"], ch["label"]), ("discord", "Discord (importé)"))
        self.assertEqual(ch["config"]["webhook_url"], "https://d/hook")
        self.assertTrue(ch["events"]["player"] and ch["events"]["health"])
        self.assertFalse(ch["events"]["restore"])
        self.assertTrue(ch["events"]["update"])                     # toujours envoyé avant -> préservé
        self.assertFalse(ch["events"]["backup"])                    # nouveaux filtres opt-in
        self.assertFalse(ch["events"]["new_player"])

    def test_env_import_creates_profiles_once(self):
        self.assertTrue(self.store.import_from_env(
            {"discord_webhook_url": "https://d/h", "telegram_bot_token": "t",
             "telegram_chat_id": "c"},
            {"player": True, "health": False, "restore": False}))
        self.assertEqual([c["type"] for c in self.store.list_channels()],
                         ["discord", "telegram"])
        self.store.remove_channel("discord-importe")                # décision UI
        self.assertFalse(self.store.import_from_env(               # ne ressuscite pas
            {"discord_webhook_url": "https://d/h"}, {}))
        self.assertEqual([c["type"] for c in self.store.list_channels()], ["telegram"])

    def test_fresh_install_without_env_writes_nothing(self):
        self.assertFalse(self.store.import_from_env({}, {}))
        self.assertFalse(os.path.exists(self.store._path))

    def test_duplicate_ids_are_readable_but_not_silently_rewritten(self):
        channel = {
            "id": "discord",
            "type": "discord",
            "label": "Discord",
            "config": {"webhook_url": "https://d/hook"},
            "events": {},
            "enabled": True,
        }
        with open(self.store._path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "channels": [channel, channel],
                    "env_import_done": True,
                },
                fh,
            )
        self.assertEqual(
            [item["id"] for item in self.store.list_channels()],
            ["discord", "discord-2"],
        )
        with self.assertRaises(CorruptJsonError):
            self.store.remove_channel("discord")


class RecordingChannel:
    def __init__(self):
        self.sent = []

    def notify(self, title, message, level="info", event=""):
        self.sent.append(title)


class TestStoreBackedNotifierPerChannel(unittest.TestCase):
    """Le cas exact de Jeremy : joueurs sur Telegram mais PAS sur Discord."""

    def _notifier(self, channels):
        import adapters.notifications as mod
        store = FakeNotificationConfig(channels=channels)
        notifier = StoreBackedNotifier(store)
        self.by_id = {}
        original = mod.build_channel
        def fake_build(ctype, config):
            # le refresh RECONSTRUIT les canaux (sans état, comme les vrais) :
            # on partage la liste d'envois par référence pour compter au travers.
            ref = config["ref"]
            ch = RecordingChannel()
            if ref in self.by_id:
                ch.sent = self.by_id[ref].sent
            self.by_id[ref] = ch
            return ch
        mod.build_channel = fake_build
        self.addCleanup(lambda: setattr(mod, "build_channel", original))
        return notifier, store

    CHANNELS = [
        {"id": "tg", "type": "telegram", "label": "TG",
         "config": {"ref": "tg"}, "events": {"player": True, "health": True}, "enabled": True},
        {"id": "dc", "type": "discord", "label": "DC",
         "config": {"ref": "dc"}, "events": {"player": False, "health": True}, "enabled": True},
    ]

    def test_event_filtered_per_channel(self):
        notifier, _ = self._notifier(self.CHANNELS)
        notifier.notify("Joueur connecté", "x", "info", event="player")
        self.assertEqual(self.by_id["tg"].sent, ["Joueur connecté"])   # TG : oui
        self.assertEqual(self.by_id["dc"].sent, [])                    # Discord : non

    def test_untagged_goes_to_all_enabled_channels(self):
        notifier, _ = self._notifier(self.CHANNELS)
        notifier.notify("Échec", "boum", "error")
        self.assertEqual(self.by_id["tg"].sent, ["Échec"])
        self.assertEqual(self.by_id["dc"].sent, ["Échec"])

    def test_disabled_channel_receives_nothing_live(self):
        notifier, store = self._notifier(self.CHANNELS)
        notifier.notify("Échec", "x", "error")                         # amorce le cache
        store.update_channel("dc", enabled=False)                      # suspension UI
        notifier.notify("Échec 2", "x", "error")
        self.assertEqual(self.by_id["dc"].sent, ["Échec"])             # plus rien après
        self.assertEqual(self.by_id["tg"].sent, ["Échec", "Échec 2"])


if __name__ == "__main__":
    unittest.main()
