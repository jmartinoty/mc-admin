"""Tests des adapters NotificationPort (Discord, Telegram, composite).

`urllib.request.urlopen` est monkeypatché (pas de réseau) pour capturer la
requête envoyée sans dépendre d'un vrai webhook/bot.
"""
from __future__ import annotations

import json
import unittest
import urllib.error
from unittest.mock import patch

from adapters.notifications import (
    CompositeNotifier,
    DiscordWebhookNotifier,
    TelegramNotifier,
)


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestDiscordWebhookNotifier(unittest.TestCase):
    def test_posts_embed_with_color_by_level(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data)
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            DiscordWebhookNotifier("https://discord.com/api/webhooks/x/y").notify(
                "Sauvegarde échouée", "conteneur absent", "error"
            )

        self.assertEqual(captured["url"], "https://discord.com/api/webhooks/x/y")
        embed = captured["body"]["embeds"][0]
        self.assertEqual(embed["title"], "Sauvegarde échouée")
        self.assertEqual(embed["description"], "conteneur absent")
        self.assertEqual(embed["color"], 0x8A2B2B)  # rouge pour "error"

    def test_default_level_uses_info_color(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data)
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            DiscordWebhookNotifier("https://discord.com/api/webhooks/x/y").notify("Titre", "msg")

        self.assertEqual(captured["body"]["embeds"][0]["color"], 0x2F6FEB)


class TestTelegramNotifier(unittest.TestCase):
    def test_posts_to_bot_api_with_chat_id(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data)
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            TelegramNotifier("123:ABC", "-100999").notify("Mise à jour échouée", "boom", "error")

        self.assertEqual(captured["url"], "https://api.telegram.org/bot123:ABC/sendMessage")
        self.assertEqual(captured["body"]["chat_id"], "-100999")
        self.assertIn("Mise à jour échouée", captured["body"]["text"])
        self.assertIn("boom", captured["body"]["text"])


class TestCompositeNotifier(unittest.TestCase):
    def test_empty_list_is_noop(self):
        CompositeNotifier([]).notify("x", "y")  # ne doit pas lever

    def test_calls_all_channels(self):
        calls = []

        class Recorder:
            def notify(self, title, message, level="info"):
                calls.append((title, message, level))

        CompositeNotifier([Recorder(), Recorder()]).notify("t", "m", "warning")
        self.assertEqual(calls, [("t", "m", "warning"), ("t", "m", "warning")])

    def test_one_channel_failing_does_not_block_the_other_or_raise(self):
        calls = []

        class Failing:
            def notify(self, title, message, level="info"):
                raise urllib.error.URLError("down")

        class Working:
            def notify(self, title, message, level="info"):
                calls.append((title, message, level))

        # Ordre volontairement "failing d'abord" : vérifie l'isolation.
        CompositeNotifier([Failing(), Working()]).notify("t", "m")
        self.assertEqual(calls, [("t", "m", "info")])


if __name__ == "__main__":
    unittest.main()
