"""Tests de config.Settings.from_env et config.load_rbac (pyyaml disponible)."""
from __future__ import annotations

import os
import tempfile
import unittest

from config import Settings, load_rbac, migrate_yaml_users


class TestSettings(unittest.TestCase):
    def test_defaults(self):
        s = Settings.from_env(env={})
        self.assertEqual(s.rcon_host, "minecraft")
        self.assertEqual(s.rcon_port, 25575)
        self.assertEqual(s.minecraft_container, "")  # pas de fantôme sur install fraîche
        self.assertEqual(s.mc_restore_safety_backup_container, "")
        self.assertEqual(s.audit_log, "/data/audit.jsonl")
        self.assertEqual(
            s.pending_op_levels_file,
            "/data/pending_op_levels.json",
        )
        self.assertEqual(s.mc_op_levels_container, "")
        self.assertEqual(s.login_trusted_proxy_cidrs, "")
        self.assertFalse(s.notify_restore_events)
        self.assertTrue(s.cookie_secure)  # défaut sécurisé

    def test_overrides_and_flag_parsing(self):
        s = Settings.from_env(env={
            "RCON_PORT": "12345",
            "MINECRAFT_CONTAINER": "mc-test",
            "MC_RESTORE_SAFETY_BACKUP_CONTAINER": "mc-backup-safety",
            "SESSION_COOKIE_SECURE": "false",
            "NOTIFY_RESTORE_EVENTS": "true",
            "PENDING_OP_LEVELS_FILE": "/state/op-levels.json",
            "MC_OP_LEVELS_CONTAINER": "mc-op-levels",
            "LOGIN_TRUSTED_PROXY_CIDRS": "172.21.0.1/32, 10.0.0.0/8",
        })
        self.assertEqual(s.rcon_port, 12345)
        self.assertEqual(s.minecraft_container, "mc-test")
        self.assertEqual(s.mc_restore_safety_backup_container, "mc-backup-safety")
        self.assertFalse(s.cookie_secure)
        self.assertTrue(s.notify_restore_events)
        self.assertEqual(s.pending_op_levels_file, "/state/op-levels.json")
        self.assertEqual(s.mc_op_levels_container, "mc-op-levels")
        self.assertEqual(
            s.login_trusted_proxy_cidrs,
            "172.21.0.1/32, 10.0.0.0/8",
        )


class TestLoadRbac(unittest.TestCase):
    def _write(self, text):
        fd, path = tempfile.mkstemp(suffix=".yml")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.addCleanup(os.remove, path)
        return path

    def _stores(self):
        from adapters.credentials_store import JsonCredentials
        from adapters.users_store import JsonUsers
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return JsonUsers(os.path.join(d, "users.json")), JsonCredentials(os.path.join(d, "passwords.json"))

    YAML_WITH_USERS = """
roles:
  owner: ["*"]
  friend: [STATUS, LOGS_VIEW]
users:
  jeremy:
    role: owner
    password_hash: "$argon2id$abc"
  sam:
    role: friend
"""

    def test_yaml_users_ignored_without_migration(self):
        # V6.5 : load_rbac ne lit plus la section users: — seule la migration le fait.
        path = self._write(self.YAML_WITH_USERS)
        users, roles = load_rbac(path)
        self.assertEqual(users, {})
        self.assertIn("owner", roles)

    def test_migration_imports_once_then_yaml_is_ignored(self):
        path = self._write(self.YAML_WITH_USERS)
        users_store, password_store = self._stores()
        imported = migrate_yaml_users(path, users_store, password_store)
        self.assertEqual(imported, ["jeremy", "sam"])
        self.assertEqual(password_store.get("jeremy"), "$argon2id$abc")  # hash copié tel quel
        self.assertIsNone(password_store.get("sam"))                     # pas de hash -> pas de login
        users, _ = load_rbac(path, users_store._path)
        self.assertEqual(users["jeremy"].role.name, "owner")
        self.assertEqual(users["sam"].role.name, "friend")
        # Un compte supprimé dans l'app ne ressuscite pas au redémarrage suivant.
        users_store.remove("sam")
        self.assertEqual(migrate_yaml_users(path, users_store, password_store), [])
        users, _ = load_rbac(path, users_store._path)
        self.assertNotIn("sam", users)

    def test_migration_preserves_app_data(self):
        path = self._write(self.YAML_WITH_USERS)
        users_store, password_store = self._stores()
        users_store.add("jeremy", "friend")                  # rôle décidé dans l'app
        password_store.set("jeremy", "$argon2id$app")        # mot de passe changé dans l'app
        migrate_yaml_users(path, users_store, password_store)
        self.assertEqual(users_store.all()["jeremy"], "friend")          # pas écrasé
        self.assertEqual(password_store.get("jeremy"), "$argon2id$app")  # pas écrasé

    def test_migration_skips_v64_tombstones(self):
        path = self._write(self.YAML_WITH_USERS)
        users_store, password_store = self._stores()
        with open(users_store._path, "w", encoding="utf-8") as fh:
            fh.write('{"users": {}, "removed": ["sam"]}')    # sam supprimé en V6.4
        self.assertEqual(migrate_yaml_users(path, users_store, password_store), ["jeremy"])

    def test_empty_file_yields_empty(self):
        path = self._write("")
        users, roles = load_rbac(path)
        self.assertEqual(users, {})
        self.assertIn("owner", roles)                        # rôles par défaut


if __name__ == "__main__":
    unittest.main()


class TestFactories(unittest.TestCase):
    """Smoke tests des factories réelles (composition root) : les tests API
    injectent des fakes et n'exercent JAMAIS build_* — un import cassé dans
    une factory ne se voyait qu'au démarrage du conteneur (vécu)."""

    def _settings(self, **overrides):
        from config import Settings
        base = dict(
            rcon_host="minecraft", rcon_port=25575, rcon_password="x",
            session_secret="s", minecraft_container="minecraft",
            mc_log_file="/tmp/latest.log",
            rbac_config="/tmp/roles.yml", audit_log="/tmp/audit.jsonl",
            cookie_secure=False, prometheus_url="http://prom:9090",
            metrics_config="/nonexistent/metrics.yml",
            mc_updater_container="mc-updater", ops_file="/tmp/ops.json",
        )
        base.update(overrides)
        return Settings(**base)

    def test_build_notifications_all_channel_combos(self):
        import tempfile as tf

        from config import build_notifications
        for extra in (
            {},  # aucun canal -> no-op silencieux
            {"discord_webhook_url": "https://discord.example/hook"},
            {"telegram_bot_token": "t", "telegram_chat_id": "c"},
            {"discord_webhook_url": "https://d", "telegram_bot_token": "t", "telegram_chat_id": "c"},
        ):
            with self.subTest(channels=list(extra)), tf.TemporaryDirectory() as tmp:
                notifier = build_notifications(self._settings(
                    notification_config_file=f"{tmp}/notifications.json", **extra))
                self.assertTrue(hasattr(notifier, "notify"))

    def test_build_health_watcher_always_on(self):
        # Depuis l'A2.3 le thread tourne toujours : l'interrupteur « health »
        # (notifications.json) filtre les envois, pas l'existence du watcher.
        import tempfile as tf

        from config import build_health_watcher
        with tf.TemporaryDirectory() as tmp:
            watcher = build_health_watcher(self._settings(
                watched_containers_file=f"{tmp}/watched.json",
                notification_config_file=f"{tmp}/notifications.json",
                servers_file=f"{tmp}/servers.json"))
            self.assertIsNotNone(watcher)

    def test_build_service_assembles_all_adapters(self):
        import tempfile
        from config import build_service
        with tempfile.TemporaryDirectory() as tmp:
            service = build_service(self._settings(
                player_history_db=f"{tmp}/players.db",
                watched_containers_file=f"{tmp}/watched_containers.json",
                temp_bans_file=f"{tmp}/pending_unbans.json",
                recurring_restart_file=f"{tmp}/recurring.json",
                servers_file=f"{tmp}/servers.json",
                pending_op_levels_file=f"{tmp}/pending_op_levels.json",
                mc_op_levels_container="mc-op-levels",
            ))  # ne doit pas lever
            self.assertTrue(hasattr(service, "get_status"))
            self.assertEqual(
                service._op_levels_apply._name,
                "mc-op-levels",
            )


class TestLoadMetrics(unittest.TestCase):
    def test_absent_file_yields_defaults_with_container_injected(self):
        from config import DEFAULT_METRICS, load_metrics
        specs = load_metrics("/nonexistent/metrics.yml", container="mc-prod")
        self.assertEqual(len(specs), len(DEFAULT_METRICS))
        by_key = {s["key"]: s for s in specs}
        self.assertIn('name="mc-prod"', by_key["cpu"]["query"])
        self.assertIn('server_host="mc-prod"', by_key["players"]["query"])
        self.assertNotIn("{container}", str(specs))          # placeholder toujours résolu
        self.assertTrue(by_key["tps"]["spark"])              # sparklines des défauts

    def test_defaults_are_structurally_valid(self):
        from config import DEFAULT_METRICS
        for spec in DEFAULT_METRICS:
            self.assertEqual({"key", "label", "query"} - set(spec), set(), spec)

    def test_present_file_replaces_defaults_entirely(self):
        from config import load_metrics
        fd, path = tempfile.mkstemp(suffix=".yml")
        self.addCleanup(os.remove, path)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write('metrics:\n  - key: custom\n    label: "Ma métrique"\n    query: "up"\n')
        specs = load_metrics(path, container="minecraft")
        self.assertEqual([s["key"] for s in specs], ["custom"])  # pas de fusion

    def test_present_but_empty_file_means_no_panel(self):
        from config import load_metrics
        fd, path = tempfile.mkstemp(suffix=".yml")
        os.close(fd)
        self.addCleanup(os.remove, path)
        self.assertEqual(load_metrics(path, container="minecraft"), [])

    def test_malformed_entry_still_rejected(self):
        from config import load_metrics
        fd, path = tempfile.mkstemp(suffix=".yml")
        self.addCleanup(os.remove, path)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write('metrics:\n  - key: broken\n')
        with self.assertRaises(ValueError):
            load_metrics(path)


class TestLegacyFriendRole(unittest.TestCase):
    def test_users_json_friend_maps_to_viewer_on_default_roles(self):
        from adapters.users_store import JsonUsers
        from config import load_rbac
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "users.json")
            JsonUsers(path).add("copain", "friend")      # créé avant le renommage
            users, roles = load_rbac("/nonexistent/roles.yml", path)
            self.assertNotIn("friend", roles)            # défauts : owner/admin/viewer
            self.assertEqual(users["copain"].role.name, "viewer")
