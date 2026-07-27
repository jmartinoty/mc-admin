"""Tests de la couche HTTP (routes, sessions, CSRF, RBAC de bout en bout).

Nécessitent fastapi + httpx (TestClient) — absents du sandbox de dev, présents
dans l'image Docker : exécuter via
  docker compose run --rm --no-deps -v $PWD/tests:/app/tests mc-admin \
    sh -c "pip install -q httpx && python -m unittest discover -s tests -t /app"
Hors conteneur, ces tests sont sautés (skip), le reste de la suite tourne.

Le service injecté est le VRAI AdminService branché sur les fakes de ports :
on teste donc transport + RBAC + audit ensemble, sans Docker/RCON réels.
"""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

try:
    from fastapi.testclient import TestClient
    HAS_HTTP_STACK = True
except Exception:  # fastapi ou httpx manquant
    HAS_HTTP_STACK = False

from adapters.restart_schedule import InMemoryRestartSchedule
from adapters.pending_restore import InMemoryPendingRestore
from domain.model import ArchiveCheck, BackupArchive, PendingOpLevel, Player
from domain.services import AdminService
from tests.fakes import (
    FakeBackup,
    FakeArchiveChecks,
    FakeArchiveValidator,
    FakeContainer,
    FakeGame,
    FakeLogs,
    FakeMetrics,
    FakeBackgroundTask,
    FakeNotifier,
    FakeOpLevelsApply,
    FakeBackupArchives,
    FakeBans,
    FakeTempBans,
    FakeRestore,
    FakePlayerHistory,
    FakePlayerStats,
    FakeOps,
    FakePendingOpLevels,
    FakeUpdater,
    FixedClock,
    RecordingAudit,
)

ROLES_YAML = """
roles:
  owner: ["*"]
  admin: [STATUS, LOGS_VIEW, RESTART, START, BACKUP_TRIGGER, BACKUP_DOWNLOAD, KICK]
  friend: [STATUS, LOGS_VIEW]
users:
  jeremy: { role: owner,  password_hash: "plain:owner-pw" }
  paul:   { role: admin,  password_hash: "plain:admin-pw" }
  sam:    { role: friend, password_hash: "plain:friend-pw" }
"""

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


class PlainHasher:
    """Hasher factice : le 'hash' stocké est 'plain:<mot de passe>'."""

    def verify(self, stored_hash: str, password: str) -> bool:
        return stored_hash == f"plain:{password}"

    def hash(self, password: str) -> str:
        return f"plain:{password}"


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis (exécuter dans le conteneur)")
class ApiTestBase(unittest.TestCase):
    def setUp(self):
        from api.app import create_app
        from config import Settings

        fd, self.roles_path = tempfile.mkstemp(suffix=".yml")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(ROLES_YAML)
        self.addCleanup(os.remove, self.roles_path)

        from adapters.credentials_store import JsonCredentials
        from adapters.display_names import JsonDisplayNames
        dn_fd, self.display_names_path = tempfile.mkstemp(suffix=".json")
        os.close(dn_fd)
        os.remove(self.display_names_path)  # créé à la demande par l'adapter
        self.addCleanup(lambda: os.path.exists(self.display_names_path) and os.remove(self.display_names_path))
        self.display_names = JsonDisplayNames(self.display_names_path)
        pw_fd, self.password_store_path = tempfile.mkstemp(suffix=".json")
        os.close(pw_fd)
        os.remove(self.password_store_path)
        self.addCleanup(lambda: os.path.exists(self.password_store_path) and os.remove(self.password_store_path))
        self.password_overrides = JsonCredentials(self.password_store_path)
        us_fd, self.users_store_path = tempfile.mkstemp(suffix=".json")
        os.close(us_fd)
        os.remove(self.users_store_path)  # créé à la demande par l'adapter
        self.addCleanup(lambda: os.path.exists(self.users_store_path) and os.remove(self.users_store_path))

        settings = Settings(
            rcon_host="minecraft",
            rcon_port=25575,
            rcon_password="x",
            session_secret="test-secret",
            minecraft_container="minecraft",
            mc_log_file="/nonexistent/latest.log",
            rbac_config=self.roles_path,
            audit_log="/nonexistent/audit.jsonl",
            cookie_secure=False,  # TestClient parle en http://
            prometheus_url="http://prom:9090",
            metrics_config="/nonexistent/metrics.yml",
            mc_updater_container="mc-updater",
            ops_file="/nonexistent/ops.json",
            users_file=self.users_store_path,
        )
        self.game = FakeGame(players=[Player("alice")], whitelist=["alice", "bob"])
        self.container = FakeContainer(running=True)
        self.backup = FakeBackup()
        self.audit = RecordingAudit()
        self.metrics = FakeMetrics()
        self.updater = FakeUpdater()
        self.ops = FakeOps(["Trappeur"])
        self.pending_op_levels = FakePendingOpLevels()
        self.op_levels_apply = FakeOpLevelsApply()
        self.notifier = FakeNotifier()
        self.player_history = FakePlayerHistory()
        self.backup_archives = FakeBackupArchives()
        target_archive = self.backup_archives.list_archives()[0]
        self.archive_checks = FakeArchiveChecks({
            target_archive.filename: ArchiveCheck(
                filename=target_archive.filename,
                ok=True,
                checked_at=FixedClock().now(),
                size_bytes=target_archive.size_bytes,
                restorable=True,
                world_name="old_world_1",
                archive_created_at=target_archive.created_at,
            ),
        })
        self.archive_validator = FakeArchiveValidator(self.backup_archives)

        def create_safety_archive():
            self.backup_archives._archives.append(BackupArchive(
                filename=(
                    "restore-safety/"
                    f"safety-test-{self.backup.triggers}.tar.gz"
                ),
                size_bytes=770_000_000,
                created_at=FixedClock().now() + timedelta(seconds=self.backup.triggers),
            ))

        self.backup.on_trigger = create_safety_archive
        self.bans = FakeBans()
        self.temp_bans = FakeTempBans()
        self.restart_schedule = InMemoryRestartSchedule()
        self.restore = FakeRestore()
        self.pending_restore = InMemoryPendingRestore()
        self.player_stats = FakePlayerStats()
        from domain.model import BackupProfile
        from tests.fakes import FakeBackupProfiles, FakeProfileBackup
        self.backup_profiles = FakeBackupProfiles([
            BackupProfile(id="manual", name="Manuelles", include=("all",), dest="manual"),
            BackupProfile(id="auto", name="Automatiques", include=("all",), dest="scheduled",
                          daily_at="04:00", retention_daily_days=30, retention_monthly_months=12),
        ])
        self.profile_backup = FakeProfileBackup()
        from tests.fakes import FakeMods
        self.mods = FakeMods()
        from domain.model import DetectedServer, ServerEntry
        from tests.fakes import FakeDiscovery, FakeServers
        self.servers = FakeServers([ServerEntry(
            id="principal", name="Serveur principal", container="minecraft",
            rcon_host="minecraft", rcon_port=25575)])
        self.discovery = FakeDiscovery([
            DetectedServer(name="minecraft", image="itzg/minecraft-server:latest", state="running"),
            DetectedServer(name="mc-creatif", image="itzg/minecraft-server:latest", state="exited"),
        ])
        from tests.fakes import FakeNotificationConfig, FakeWatched
        from tests.fakes import FakeModChecks
        self.mod_checks = FakeModChecks(
            checked=datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc))
        self.notify_config = FakeNotificationConfig(channels=[
            {"id": "discord-importe", "type": "discord", "label": "Discord (importé)",
             "config": {"webhook_url": "https://discord.example/hook"},
             "events": {"player": True, "health": False, "restore": False},
             "enabled": True}])
        self.watched = FakeWatched(["playit"])
        from tests.fakes import FakeSpark
        self.spark = FakeSpark()
        self.companions = {"playit": FakeContainer(running=True, name="playit")}
        from tests.fakes import FakeMapProbe
        self.map_probe = FakeMapProbe()
        self.map_requests: list[tuple[str, str, str]] = []

        def fake_map_open(base, path, query="", **kwargs):
            self.map_requests.append((base, path, query))
            self.map_conditional = {k: v for k, v in kwargs.items() if v}
            headers = {"Content-Type": "text/html", "ETag": "fake-etag",
                       "Cache-Control": "max-age=86400"}
            return 200, headers, iter((b"<html>FAKE MAP</html>",))

        self.fake_map_open = fake_map_open
        from tests.fakes import FakeAppUpdater, FakeAppUpdateSnooze, FakeAppUpdateState, FakeSelfImage
        self.app_update_state = FakeAppUpdateState()
        self.app_update_snooze = FakeAppUpdateSnooze()
        self.app_updater = FakeAppUpdater()
        self.self_image = FakeSelfImage()
        self.service = AdminService(
            game=self.game,
            container=self.container,
            restore_safety_backup=self.backup,
            logs=FakeLogs(["[INFO] ok"]),
            audit=self.audit,
            clock=FixedClock(),
            container_name="minecraft",
            metrics=self.metrics,
            updater=self.updater,
            ops=self.ops,
            pending_op_levels=self.pending_op_levels,
            op_levels_apply=self.op_levels_apply,
            notifications=self.notifier,
            player_history=self.player_history,
            backup_archives=self.backup_archives,
            bans=self.bans,
            temp_bans=self.temp_bans,
            restart_schedule=self.restart_schedule,
            restore=self.restore,
            pending_restore=self.pending_restore,
            player_stats=self.player_stats,
            archive_checks=self.archive_checks,
            archive_validator=self.archive_validator,
            backup_profiles=self.backup_profiles,
            profile_backup=self.profile_backup,
            world_dir="old_world_1",
            mods=self.mods,
            servers=self.servers,
            server_discovery=self.discovery,
            watched=self.watched,
            companion_port_factory=lambda name: self.companions[name],
            notification_config=self.notify_config,
            spark=self.spark,
            mod_checks=self.mod_checks,
            map_probe=self.map_probe,
            app_update_state=self.app_update_state,
            app_update_snooze=self.app_update_snooze,
            app_updater=self.app_updater,
            self_image=self.self_image,
        )
        app = create_app(
            settings,
            service=self.service,
            hasher=PlainHasher(),
            scheduler=FakeBackgroundTask(),
            player_watcher=FakeBackgroundTask(),
            tempban_scheduler=FakeBackgroundTask(),
            restart_scheduler=FakeBackgroundTask(),
            restore_coordinator=FakeBackgroundTask(),
            perf_watcher=FakeBackgroundTask(),
            mod_update_checker=FakeBackgroundTask(),
            display_names=self.display_names,
            password_overrides=self.password_overrides,
            map_open=self.fake_map_open,
            app_update_checker=FakeBackgroundTask(),
        )
        self.client = TestClient(app, follow_redirects=False)

    # -- helpers --
    def _csrf_from(self, html: str) -> str:
        match = CSRF_RE.search(html)
        self.assertIsNotNone(match, "jeton CSRF absent du HTML")
        return match.group(1)

    def login(self, username: str, password: str, *, headers=None):
        token = self._csrf_from(self.client.get("/login").text)
        return self.client.post(
            "/login",
            data={"username": username, "password": password, "csrf_token": token},
            headers=headers,
        )

    def page_csrf(self) -> str:
        return self._csrf_from(self.client.get("/").text)


class TestAuthFlow(ApiTestBase):
    def test_healthz_open(self):
        res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200)

    def test_unauthenticated_index_redirects_to_login(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/login")

    def test_unauthenticated_fragment_is_401(self):
        self.assertEqual(self.client.get("/fragment/status").status_code, 401)

    def test_login_wrong_password_401(self):
        self.assertEqual(self.login("jeremy", "wrong").status_code, 401)

    def test_login_without_valid_csrf_400(self):
        res = self.client.post(
            "/login", data={"username": "jeremy", "password": "owner-pw", "csrf_token": "forged"}
        )
        self.assertEqual(res.status_code, 400)

    def test_login_bruteforce_locked_after_repeated_failures(self):
        for _ in range(5):
            self.assertEqual(self.login("jeremy", "wrong").status_code, 401)
        # 6e tentative : verrouillé, MÊME avec le bon mot de passe.
        res = self.login("jeremy", "owner-pw")
        self.assertEqual(res.status_code, 429)
        self.assertIn("Trop de tentatives", res.text)
        # Un autre compte reste accessible sous le plafond global de cette IP.
        self.assertEqual(self.login("paul", "admin-pw").status_code, 303)

    def test_untrusted_forwarded_header_cannot_bypass_lock(self):
        for index in range(5):
            res = self.login(
                "jeremy",
                "wrong",
                headers={"X-Forwarded-For": f"198.51.100.{index + 1}"},
            )
            self.assertEqual(res.status_code, 401)
        res = self.login(
            "jeremy",
            "owner-pw",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        self.assertEqual(res.status_code, 429)

    def test_login_success_resets_failure_counter(self):
        for _ in range(4):
            self.login("jeremy", "wrong")
        self.assertEqual(self.login("jeremy", "owner-pw").status_code, 303)  # sous le seuil
        self.client.post("/logout", data={"csrf_token": self.page_csrf()})
        for _ in range(4):
            self.login("jeremy", "wrong")  # compteur reparti de zéro : pas de verrou
        self.assertEqual(self.login("jeremy", "owner-pw").status_code, 303)

    def test_login_ok_then_status_page(self):
        self.assertEqual(self.login("paul", "admin-pw").status_code, 303)
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("paul", page.text)
        self.assertIn("alice", page.text)          # joueur listé
        self.assertIn("/actions/schedule-restart", page.text)  # bouton redémarrer visible (admin)

    def test_logout_clears_session(self):
        self.login("paul", "admin-pw")
        token = self.page_csrf()
        self.assertEqual(self.client.post("/logout", data={"csrf_token": token}).status_code, 303)
        self.assertEqual(self.client.get("/").status_code, 303)  # de nouveau anonyme


class TestActionsRbac(ApiTestBase):
    def test_admin_restart_happy_path(self):
        self.login("paul", "admin-pw")
        res = self.client.post("/actions/restart", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.container.restarts, 1)
        self.assertEqual(self.audit.entries[-1].outcome, "allowed")

    def test_friend_restart_403_and_untouched(self):
        self.login("sam", "friend-pw")
        page = self.client.get("/")
        self.assertNotIn("/actions/restart", page.text)  # bouton masqué (UX)
        # ...mais la vraie barrière est côté serveur :
        res = self.client.post("/actions/restart", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.container.restarts, 0)
        self.assertEqual(self.audit.entries[-1].outcome, "denied")

    def test_restart_without_csrf_400_and_untouched(self):
        self.login("paul", "admin-pw")
        res = self.client.post("/actions/restart", data={"csrf_token": "forged"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.container.restarts, 0)

    def test_admin_backup_happy_path(self):
        self.login("paul", "admin-pw")
        res = self.client.post("/actions/backup-profiles/manual/run", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.profile_backup.calls[0]["dest"], "manual")  # profil « Manuelles »

    def test_friend_backup_403(self):
        self.login("sam", "friend-pw")
        res = self.client.post("/actions/backup-profiles/manual/run", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.profile_backup.calls, [])


class TestStartStop(ApiTestBase):
    def test_stop_button_owner_only(self):
        self.login("paul", "admin-pw")
        self.assertNotIn("/actions/stop", self.client.get("/").text)
        self.client.post("/logout", data={"csrf_token": self.page_csrf()})
        self.login("jeremy", "owner-pw")
        self.assertIn("/actions/stop", self.client.get("/").text)

    def test_admin_stop_403(self):
        self.login("paul", "admin-pw")
        res = self.client.post("/actions/stop", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.container.stops, 0)

    def test_owner_stop_happy_path(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/stop", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.container.stops, 1)

    def test_buttons_greyed_by_state_not_hidden(self):
        # Serveur en marche : Démarrer grisé, Redémarrer actif.
        self.login("paul", "admin-pw")
        page = self.client.get("/").text
        self.assertIn("/actions/start", page)      # présent (permission) …
        self.assertRegex(page, r'<button class="ibtn play" disabled')   # … mais grisé (état)
        self.assertIn('<button type="button" class="ibtn restart" id="restartOpen">', page)  # actif

    def test_start_when_stopped_and_admin_can_start(self):
        self.container._running = False
        self.login("paul", "admin-pw")
        page = self.client.get("/").text
        self.assertIn('<button class="ibtn play">', page)                    # actif
        self.assertRegex(page, r'<button type="button" class="ibtn restart" id="restartOpen" disabled')  # grisé, pas caché
        res = self.client.post("/actions/start", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.container.starts, 1)

    def test_friend_start_403(self):
        self.container._running = False
        self.login("sam", "friend-pw")
        res = self.client.post("/actions/start", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.container.starts, 0)


class TestMetricsPanel(ApiTestBase):
    def test_metrics_cards_rendered_on_dashboard(self):
        self.login("sam", "friend-pw")
        page = self.client.get("/").text
        self.assertIn("Joueurs en ligne", page)
        self.assertIn("RAM serveur", page)
        self.assertIn("4500", page)

    def test_sparkline_rendered_when_history_available(self):
        # FakeMetrics : ram porte un history 3 points -> polyline ; players non.
        self.login("sam", "friend-pw")
        page = self.client.get("/").text
        self.assertEqual(page.count('class="spark"'), 1)
        self.assertIn("<polyline points=", page)

    def test_status_fragment_keeps_terminal_logs_only(self):
        self.login("sam", "friend-pw")
        frag = self.client.get("/fragment/status").text
        self.assertIn("term-body", frag)                 # la zone rafraîchie = les lignes seules
        self.assertIn("[INFO] ok", frag)
        self.assertNotIn("term-head", frag)              # l'enveloppe (en-tête, filtre) est statique
        self.assertNotIn("RAM serveur", frag)

    def test_empty_logs_show_explicit_message_not_silence(self):
        # latest.log fraîchement roté ne contient que du bruit RCON (filtré) :
        # le terminal doit l'expliquer, pas afficher un vide qui ressemble à
        # une panne (signalé deux fois en réel).
        self.login("jeremy", "owner-pw")
        self.client.app.state.service._logs._lines = []
        frag = self.client.get("/fragment/status").text
        self.assertIn("aucune activité depuis la rotation des logs", frag)

    def test_metrics_unavailable_degrades(self):
        self.metrics._available = False
        self.login("sam", "friend-pw")
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)  # la page vit sans Prometheus
        self.assertIn("Mesures indisponibles", page.text)
        self.assertIn("SUR-90", page.text)                         # état codifié + lien doc

    def test_missing_value_renders_dash(self):
        from domain.model import MetricReading
        self.metrics.readings = [MetricReading(key="x", label="CPU serveur", unit="%", value=None)]
        self.login("sam", "friend-pw")
        page = self.client.get("/").text
        self.assertIn("CPU serveur", page)
        self.assertIn("–", page)

    def test_viewer_dashboard_keeps_play_information_only(self):
        self.container.health = "healthy"
        self.login("sam", "friend-pw")

        page = self.client.get("/").text

        self.assertIn('data-dashboard-mode="overview"', page)
        self.assertIn("Serveur Minecraft", page)
        self.assertIn("2 mods installés", page)
        self.assertNotIn("<h2>Infrastructure</h2>", page)
        self.assertNotIn(">healthy<", page)

    def test_operator_dashboard_keeps_actions_without_infrastructure(self):
        self.login("paul", "admin-pw")

        page = self.client.get("/").text

        self.assertIn('data-dashboard-mode="operations"', page)
        self.assertIn("/actions/schedule-restart", page)
        self.assertNotIn("<h2>Infrastructure</h2>", page)


class TestUpdate(ApiTestBase):
    def test_card_owner_only_with_versions(self):
        self.login("paul", "admin-pw")
        self.assertNotIn("/actions/update", self.client.get("/").text)
        self.client.post("/logout", data={"csrf_token": self.page_csrf()})
        self.login("jeremy", "owner-pw")
        page = self.client.get("/").text
        self.assertIn("/actions/update", page)
        self.assertIn("26.1", page)
        self.assertIn("26.2", page)
        self.assertIn("màj dispo", page)
        self.assertIn("changelog", page)

    def test_admin_post_403(self):
        self.login("paul", "admin-pw")
        res = self.client.post("/actions/update", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.updater.applied, 0)

    def test_blocked_with_players_shows_flash(self):
        self.login("jeremy", "owner-pw")   # alice connectée dans le fake
        res = self.client.post("/actions/update", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.updater.applied, 0)
        self.assertIn("SRV-06", self.client.get("/").text)

    def test_force_applies(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/update", data={"csrf_token": self.page_csrf(), "force": "yes"}
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.updater.applied, 1)
        self.assertEqual(len(self.game.said), 1)  # avertissement in-game parti


class TestAuditPage(ApiTestBase):
    def test_owner_sees_entries_newest_first(self):
        # produit des entrées : restart admin (allowed) puis tentative stop (denied)
        self.login("paul", "admin-pw")
        self.client.post("/actions/restart", data={"csrf_token": self.page_csrf()})
        self.client.post("/actions/stop", data={"csrf_token": self.page_csrf()})
        self.client.post("/logout", data={"csrf_token": self.page_csrf()})
        self.login("jeremy", "owner-pw")
        page = self.client.get("/audit")
        self.assertEqual(page.status_code, 200)
        self.assertIn("RESTART", page.text)
        self.assertIn("denied", page.text)
        # plus récent (STOP denied) avant le plus ancien (RESTART allowed)
        self.assertLess(page.text.index('title="STOP"'), page.text.index('title="RESTART"'))

    def test_friend_403(self):
        self.login("sam", "friend-pw")
        self.assertEqual(self.client.get("/audit").status_code, 403)

    def test_anonymous_redirected(self):
        res = self.client.get("/audit")
        self.assertEqual(res.status_code, 303)

    def test_topbar_link_owner_only(self):
        self.login("sam", "friend-pw")
        self.assertNotIn('href="/audit"', self.client.get("/").text)
        self.client.post("/logout", data={"csrf_token": self.page_csrf()})
        self.login("jeremy", "owner-pw")
        self.assertIn('href="/audit"', self.client.get("/").text)


class TestWhitelist(ApiTestBase):
    def test_page_owner_only(self):
        self.login("paul", "admin-pw")
        self.assertNotIn('href="/whitelist"', self.client.get("/").text)  # pas dans la sidebar
        self.assertEqual(self.client.get("/whitelist").status_code, 403)
        self.client.post("/logout", data={"csrf_token": self.page_csrf()})
        self.login("jeremy", "owner-pw")
        page = self.client.get("/whitelist")
        self.assertEqual(page.status_code, 200)
        self.assertIn("/actions/whitelist/add", page.text)
        self.assertIn("bob", page.text)  # noms whitelistés rendus

    def test_owner_add_happy_path(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/whitelist/add",
            data={"player": "toto_64", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/whitelist")
        self.assertEqual(self.game.whitelist_calls, [("add", "toto_64")])

    def test_owner_remove_happy_path(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/whitelist/remove",
            data={"player": "bob", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.game.whitelist_calls, [("remove", "bob")])

    def test_invalid_name_rejected_with_flash(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/whitelist/add",
            data={"player": "bad name!", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)  # redirect avec flash d'échec
        self.assertEqual(self.game.whitelist_calls, [])  # RCON jamais appelé
        self.assertIn("JOU-04", self.client.get("/whitelist").text)

    def test_admin_add_403(self):
        self.login("paul", "admin-pw")
        res = self.client.post(
            "/actions/whitelist/add",
            data={"player": "toto", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.game.whitelist_calls, [])


class TestOps(ApiTestBase):
    def test_page_owner_only(self):
        self.login("paul", "admin-pw")
        self.assertNotIn('href="/op"', self.client.get("/").text)
        self.assertEqual(self.client.get("/op").status_code, 403)
        self.client.post("/logout", data={"csrf_token": self.page_csrf()})
        self.login("jeremy", "owner-pw")
        page = self.client.get("/op")
        self.assertEqual(page.status_code, 200)
        self.assertIn("/actions/op/add", page.text)
        self.assertIn("Trappeur", page.text)  # op existant rendu

    def test_owner_add_happy_path(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/op/add",
            data={"player": "toto_64", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/op")
        self.assertEqual(self.game.op_calls, [("op", "toto_64")])
        self.assertEqual(self.pending_op_levels.set_calls, [])

    def test_owner_add_with_custom_level(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/op/add",
            data={"player": "toto_64", "level": "2", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.pending_op_levels.set_calls, [("toto_64", 2)])
        self.assertIn("prochain redémarrage", "".join(e.detail for e in self.audit.entries))
        self.assertIn("Niveau 2 prévu", self.client.get("/op").text)

    def test_players_show_active_and_pending_levels_separately(self):
        self.pending_op_levels.entries["trappeur"] = PendingOpLevel("Trappeur", 2)
        self.login("jeremy", "owner-pw")
        page = self.client.get("/players").text
        self.assertIn("OP 4", page)
        self.assertIn("niveau 2 en attente", page)

    def test_audit_distinguishes_immediate_and_pending_level(self):
        self.login("jeremy", "owner-pw")
        csrf = self.page_csrf()
        self.client.post(
            "/actions/op/add",
            data={"player": "toto_64", "level": "4", "csrf_token": csrf},
        )
        self.client.post(
            "/actions/op/add",
            data={"player": "Trappeur", "level": "2", "csrf_token": csrf},
        )
        page = self.client.get("/audit").text
        self.assertIn("toto_64 opérateur niveau 4", page)
        self.assertIn(
            "Trappeur opérateur niveau 2 (niveau au prochain redémarrage)",
            page,
        )

    def test_invalid_level_rejected_with_flash(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/op/add",
            data={"player": "toto_64", "level": "9", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.pending_op_levels.set_calls, [])
        self.assertIn("JOU-06", self.client.get("/op").text)

    def test_owner_remove_happy_path(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/op/remove",
            data={"player": "Trappeur", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.game.op_calls, [("deop", "Trappeur")])

    def test_invalid_name_rejected_with_flash(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/op/add",
            data={"player": "bad name!", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.game.op_calls, [])
        self.assertIn("JOU-06", self.client.get("/op").text)

    def test_admin_add_403(self):
        self.login("paul", "admin-pw")
        res = self.client.post(
            "/actions/op/add",
            data={"player": "toto", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.game.op_calls, [])


class TestBansPageApi(ApiTestBase):
    def test_page_owner_only(self):
        self.login("paul", "admin-pw")
        self.assertNotIn('href="/bans"', self.client.get("/").text)
        self.assertEqual(self.client.get("/bans").status_code, 403)
        self.client.post("/logout", data={"csrf_token": self.page_csrf()})
        self.login("jeremy", "owner-pw")
        page = self.client.get("/bans")
        self.assertEqual(page.status_code, 200)
        self.assertIn("/actions/ban", page.text)
        self.assertIn("griefer", page.text)  # ban existant rendu

    def test_owner_ban_happy_path(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/ban",
            data={"player": "toto_64", "reason": "Triche", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/bans")
        self.assertEqual(self.game.ban_calls, [("ban", "toto_64", "Triche")])

    def test_owner_pardon_happy_path(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/pardon",
            data={"player": "griefer", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.game.ban_calls, [("pardon", "griefer", "")])

    def test_invalid_name_rejected_with_flash(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/ban",
            data={"player": "bad name!", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.game.ban_calls, [])
        self.assertIn("JOU-01", self.client.get("/bans").text)

    def test_admin_ban_403(self):
        self.login("paul", "admin-pw")
        res = self.client.post(
            "/actions/ban",
            data={"player": "toto", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.game.ban_calls, [])

    def test_owner_temporary_ban_happy_path(self):
        # FakeBans est un fixture statique indépendant de FakeGame.ban_calls
        # (même découplage qu'en prod : banned-players.json vs canal RCON) ->
        # on rebannit "griefer", déjà présent dans le fixture, pour vérifier
        # l'annotation "temporaire" sur une ligne réellement affichée.
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/ban",
            data={"player": "griefer", "reason": "Récidive", "hours": "24", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/bans")
        self.assertEqual(self.game.ban_calls, [("ban", "griefer", "Récidive")])
        page = self.client.get("/bans").text
        self.assertIn("temporaire — expire le", page)

    def test_blank_hours_is_a_permanent_ban(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/ban",
            data={"player": "griefer", "hours": "", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        page = self.client.get("/bans").text
        self.assertNotIn("temporaire", page)

    def test_invalid_hours_rejected_with_flash(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/ban",
            data={"player": "toto_64", "hours": "not-a-number", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.game.ban_calls, [])
        self.assertIn("JOU-01", self.client.get("/bans").text)


class TestKickAction(ApiTestBase):
    def test_admin_can_kick(self):
        self.login("paul", "admin-pw")
        res = self.client.post(
            "/actions/kick",
            data={"player": "alice", "reason": "AFK", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/")
        self.assertEqual(self.game.kick_calls, [("alice", "AFK")])

    def test_friend_cannot_kick_and_button_hidden(self):
        self.login("sam", "friend-pw")
        self.assertNotIn("/actions/kick", self.client.get("/").text)
        res = self.client.post(
            "/actions/kick",
            data={"player": "alice", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.game.kick_calls, [])


class TestScheduleRestartAction(ApiTestBase):
    def test_owner_can_schedule_and_page_shows_it(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/schedule-restart",
            data={"minutes": "30", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/")
        page = self.client.get("/").text
        self.assertIn("Redémarrage programmé", page)
        self.assertIn("/actions/cancel-restart", page)  # bouton "annuler" présent

    def test_owner_can_cancel(self):
        self.login("jeremy", "owner-pw")
        self.client.post(
            "/actions/schedule-restart",
            data={"minutes": "30", "csrf_token": self.page_csrf()},
        )
        res = self.client.post("/actions/cancel-restart", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        page = self.client.get("/").text
        self.assertNotIn("/actions/cancel-restart", page)  # bannière annulée disparue

    def test_zero_minutes_restarts_immediately(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/schedule-restart",
            data={"minutes": "0", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.container.restarts, 1)
        self.assertIsNone(self.restart_schedule.status())

    def test_friend_cannot_schedule_and_form_hidden(self):
        self.login("sam", "friend-pw")
        self.assertNotIn("/actions/schedule-restart", self.client.get("/").text)
        res = self.client.post(
            "/actions/schedule-restart",
            data={"minutes": "30", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIsNone(self.restart_schedule.status())


class TestSidebar(ApiTestBase):
    def test_owner_sees_all_sections(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/").text
        for href in ('href="/"', 'href="/players"', 'href="/backups"', 'href="/audit"'):
            self.assertIn(href, page)
        # Whitelist/Admin/Bannis fusionnés dans la page Joueurs -> plus dans la sidebar.
        for href in ('href="/whitelist"', 'href="/op"', 'href="/bans"', 'href="/console"'):
            self.assertNotIn(href, page)

    def test_friend_sees_status_and_players_only(self):
        self.login("sam", "friend-pw")
        page = self.client.get("/").text
        self.assertIn('href="/"', page)
        self.assertIn('href="/players"', page)  # friend a STATUS -> voit Joueurs
        for href in ('href="/whitelist"', 'href="/op"', 'href="/bans"',
                     'href="/backups"', 'href="/console"', 'href="/audit"'):
            self.assertNotIn(href, page)

    def test_active_section_highlighted(self):
        self.login("jeremy", "owner-pw")
        self.assertIn('navitem active" href="/"', self.client.get("/").text)
        self.assertIn('navitem active" href="/audit"', self.client.get("/audit").text)
        self.assertIn('navitem active" href="/players"', self.client.get("/players").text)

    def test_players_entry_single_and_subpages_absent_from_sidebar(self):
        # Whitelist/Admin/Bannis ne sont plus des entrées de sidebar : tout est
        # dans la page Joueurs unique (les routes restent accessibles par URL).
        self.login("jeremy", "owner-pw")
        page = self.client.get("/").text
        self.assertIn('href="/players"', page)
        self.assertNotIn(">Whitelist<", page)
        self.assertNotIn(">Admin<", page)
        self.assertNotIn(">Bannis<", page)


class TestAccessibility(ApiTestBase):
    def test_login_has_skip_link_single_main_and_specific_title(self):
        page = self.client.get("/login").text

        self.assertEqual(page.count("<main"), 1)
        self.assertIn('class="skip-link" href="#main-content"', page)
        self.assertIn("<title>Connexion · mc-admin</title>", page)
        self.assertIn('id="main-content"', page)

    def test_authenticated_shell_has_single_main_and_current_page(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/players").text

        self.assertEqual(page.count("<main"), 1)
        self.assertIn("<title>Joueurs · mc-admin</title>", page)
        self.assertIn('href="/players" aria-current="page"', page)
        self.assertIn('role="group" aria-label="Filtrer les joueurs"', page)
        self.assertIn('data-filter="all" aria-pressed="true"', page)

    def test_dialogs_are_named_and_icon_actions_have_labels(self):
        self.login("jeremy", "owner-pw")
        players = self.client.get("/players").text
        backups = self.client.get("/backups").text

        self.assertIn('aria-labelledby="playerDialogTitle"', players)
        self.assertIn('aria-describedby="playerDialogText"', players)
        self.assertIn('aria-label="Retirer alice de la whitelist"', players)
        self.assertIn('aria-labelledby="restoreDialogTitle"', backups)
        self.assertIn('aria-describedby="restoreDialogDescription"', backups)

    def test_styles_include_visible_focus_and_reduced_motion(self):
        css = self.client.get("/static/style.css").text

        self.assertIn(":focus-visible", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("animation: none !important", css)


class TestDisplayName(ApiTestBase):
    def test_default_is_username(self):
        self.login("jeremy", "owner-pw")
        self.assertIn(">jeremy<", self.client.get("/").text)  # nom affiché = pseudo par défaut

    def test_set_shows_in_sidebar_and_persists(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/display-name",
            data={"display_name": "Jerem le boss", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertIn("Jerem le boss", self.client.get("/").text)
        self.assertEqual(self.display_names.get("jeremy"), "Jerem le boss")

    def test_set_redirects_back_to_current_page(self):
        self.login("jeremy", "owner-pw")
        token = self._csrf_from(self.client.get("/players").text)
        res = self.client.post(
            "/actions/display-name",
            data={"display_name": "Jerem", "csrf_token": token, "redirect_to": "/players"},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/players")

    def test_empty_resets_to_username(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/display-name", data={"display_name": "X", "csrf_token": self.page_csrf()})
        self.client.post("/actions/display-name", data={"display_name": "  ", "csrf_token": self.page_csrf()})
        self.assertIsNone(self.display_names.get("jeremy"))  # retour au pseudo par défaut

    def test_requires_login(self):
        res = self.client.post("/actions/display-name", data={"display_name": "x", "csrf_token": "t"})
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/login")


class TestPassword(ApiTestBase):
    def _change(self, current, new):
        return self.client.post(
            "/actions/password",
            data={"current_password": current, "new_password": new, "csrf_token": self.page_csrf()},
        )

    def test_change_then_login_with_new_and_old_rejected(self):
        self.login("jeremy", "owner-pw")
        res = self._change("owner-pw", "newpass123")
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.password_overrides.get("jeremy"), "plain:newpass123")
        self.client.post("/logout", data={"csrf_token": self.page_csrf()})
        self.assertEqual(self.login("jeremy", "newpass123").status_code, 303)  # nouveau OK
        self.client.post("/logout", data={"csrf_token": self.page_csrf()})
        self.assertEqual(self.login("jeremy", "owner-pw").status_code, 401)     # ancien refusé

    def test_change_redirects_back_to_current_page(self):
        self.login("jeremy", "owner-pw")
        token = self._csrf_from(self.client.get("/players").text)
        res = self.client.post(
            "/actions/password",
            data={
                "current_password": "owner-pw",
                "new_password": "newpass123",
                "csrf_token": token,
                "redirect_to": "/players",
            },
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/players")

    def test_wrong_current_rejected(self):
        self.login("jeremy", "owner-pw")
        self._change("WRONG", "newpass123")
        self.assertEqual(self.password_overrides.get("jeremy"), "plain:owner-pw")  # inchangé
        self.assertIn("actuel incorrect", self.client.get("/").text)

    def test_too_short_rejected(self):
        self.login("jeremy", "owner-pw")
        self._change("owner-pw", "short")
        self.assertEqual(self.password_overrides.get("jeremy"), "plain:owner-pw")  # inchangé
        self.assertIn("8", self.client.get("/").text)

    def test_requires_login(self):
        res = self.client.post(
            "/actions/password",
            data={"current_password": "x", "new_password": "yyyyyyyy", "csrf_token": "t"},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/login")


class TestTimelineTab(ApiTestBase):
    # L'onglet Timeline a fusionné dans le Journal v2 (lignes-opérations).
    def test_events_url_redirects_to_journal(self):
        self.login("jeremy", "owner-pw")
        res = self.client.get("/events")
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/audit")

    def test_admin_403(self):
        self.login("paul", "admin-pw")
        self.assertEqual(self.client.get("/audit").status_code, 403)

    def test_display_name_resolved_in_journal(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/restart", data={"csrf_token": self.page_csrf()})
        self.client.post("/actions/display-name",
                         data={"display_name": "Jerem le boss", "csrf_token": self.page_csrf()})
        journal = self.client.get("/audit").text
        self.assertIn("Jerem le boss", journal)      # nom affiché rendu


class TestAuditFilters(ApiTestBase):
    def _seed(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/restart", data={"csrf_token": self.page_csrf()})
        self.client.post("/actions/kick",
                         data={"player": "alice", "reason": "AFK", "csrf_token": self.page_csrf()})

    def test_filter_by_action(self):
        self._seed()
        page = self.client.get("/audit?action=KICK").text
        self.assertIn("KICK", page)
        self.assertNotIn("<td>RESTART</td>", page)

    def test_filter_by_text_in_detail(self):
        self._seed()
        page = self.client.get("/audit?q=afk").text  # insensible à la casse
        self.assertIn("AFK", page)
        self.assertIn("réinitialiser", page)  # lien de reset visible quand filtré

    def test_restore_details_are_human_readable(self):
        self.login("jeremy", "owner-pw")
        self._inject(
            'phase=restore_started archive=manual/manual-20260713-031200.tar.gz requested_by=jeremy',
            action="BACKUP_RESTORE",
        )
        page = self.client.get("/audit").text
        self.assertIn('op-line">Restauration lancée', page)      # phase traduite en clair
        self.assertIn("manual/manual-20260713-031200.tar.gz", page)
        self.assertIn("<code>phase=restore_started", page)       # brut réservé au dépliage

    def test_restore_events_are_grouped(self):
        self.login("jeremy", "owner-pw")
        self._inject(
            "phase=restore_requested archive=manual/a.tar.gz requested_by=jeremy operation_id=op-1",
            action="BACKUP_RESTORE",
        )
        self._inject(
            "phase=security_backup_done archive=manual/a.tar.gz requested_by=jeremy operation_id=op-1",
            action="BACKUP_RESTORE",
        )
        self._inject(
            "phase=restore_completed archive=manual/a.tar.gz requested_by=jeremy operation_id=op-1",
            action="BACKUP_RESTORE",
        )
        page = self.client.get("/audit").text
        self.assertNotIn("Restaurations récentes", page)          # section dédiée supprimée
        self.assertIn("Restauration du serveur", page)            # ligne-opération unique
        self.assertEqual(page.count('<tr class="op-detail"'), 1)  # 3 événements -> 1 dépliage
        self.assertIn("Sauvegarde de sécurité terminée", page)    # étape dans le dépliage
        self.assertIn(">réussi<", page)                           # pastille de statut standard
        self.assertIn("manual/a.tar.gz", page)

    def test_op_level_restart_is_grouped_with_human_labels(self):
        self.login("jeremy", "owner-pw")
        operation_id = "op-levels::2026-07-18T12:00:00+00:00::jeremy"
        self._inject(
            f"phase=op_levels_started count=1 requested_by=jeremy operation_id={operation_id}",
        )
        self._inject(
            f"phase=op_levels_applied requested_by=jeremy operation_id={operation_id}",
        )
        page = self.client.get("/audit").text
        self.assertIn("Redémarrage avec niveaux OP", page)
        self.assertIn("Application des niveaux OP lancée", page)
        self.assertIn("Niveaux OP appliqués, Minecraft en ligne", page)
        self.assertIn(">réussi<", page)

    def test_filter_no_match_shows_empty_message(self):
        self._seed()
        page = self.client.get("/audit?q=zzz-introuvable").text
        self.assertIn("ne correspond aux filtres", page)

    def _inject(self, detail, age=None, action="RESTART"):
        from datetime import timedelta
        from domain.model import AuditEntry
        ts = datetime.now(timezone.utc) - (age or timedelta())
        self.audit.entries.append(AuditEntry(
            timestamp=ts, username="jeremy", role="owner",
            action=action, target="minecraft", outcome="allowed", detail=detail,
        ))

    def test_period_filter_excludes_old_entries(self):
        from datetime import timedelta
        self.login("jeremy", "owner-pw")
        self._inject("evenement-recent")
        self._inject("evenement-ancien", age=timedelta(days=3))
        page = self.client.get("/audit?period=24h").text
        self.assertIn("evenement-recent", page)
        self.assertNotIn("evenement-ancien", page)
        self.assertIn("réinitialiser", page)                     # période = filtre actif
        self.assertIn("evenement-ancien", self.client.get("/audit").text)

    def test_family_chips_filter_and_labels(self):
        self._seed()   # un RESTART + un KICK
        page = self.client.get("/audit").text
        self.assertIn("family=server", page)                  # chips de familles
        self.assertIn("Redémarrage", page)                    # action en français
        self.assertIn("Expulsion", page)
        filtered = self.client.get("/audit?family=players").text
        self.assertIn('title="KICK"', filtered)               # ligne Expulsion présente
        self.assertNotIn('title="RESTART"', filtered)         # ligne Redémarrage filtrée
        self.assertIn("réinitialiser", filtered)              # famille = filtre actif

    def test_denied_entries_are_security_family(self):
        self.login("sam", "friend-pw")                        # friend : refusé sur restart
        self.client.post("/actions/restart", data={"csrf_token": self.page_csrf()})
        self.client.post("/logout", data={"csrf_token": self.page_csrf()})
        self.login("jeremy", "owner-pw")
        page = self.client.get("/audit?family=security").text
        self.assertIn("fam-security", page)

    def test_day_separator_and_local_time(self):
        self._seed()
        page = self.client.get("/audit").text
        self.assertIn("day-sep", page)
        self.assertIn("1 juillet 2026", page)   # date de FixedClock, en toutes lettres
        self.assertIn("14:00:00", page)         # 12:00 UTC -> 14:00 Europe/Paris

    def test_system_users_get_auto_badge(self):
        from restore_coordinator import AUTO_RESTORE_USER
        self.login("jeremy", "owner-pw")
        # une action système auditée (refus impossible : il a la permission)
        self.service.tick_pending_restore(AUTO_RESTORE_USER)  # no-op mais pas d'entrée…
        from domain.model import AuditEntry
        self.audit.entries.append(AuditEntry(
            timestamp=datetime.now(timezone.utc), username="auto-restore", role="automation",
            action="BACKUP_RESTORE", target="minecraft", outcome="allowed", detail="x"))
        page = self.client.get("/audit").text
        self.assertIn('auto-chip">auto</span>', page)

    def test_pagination_slices_and_preserves_filters(self):
        self.login("jeremy", "owner-pw")
        for i in range(60):
            self._inject(f"pagetest-{i:02d}")
        page1 = self.client.get("/audit").text
        self.assertIn("pagetest-59", page1)                      # plus récent en premier
        self.assertNotIn("pagetest-05", page1)                   # au-delà des 50 premiers
        self.assertIn("page 1/2", page1)
        page2 = self.client.get("/audit?page=2").text
        self.assertIn("pagetest-05", page2)
        # les liens de pagination conservent les filtres actifs
        filtered = self.client.get("/audit?action=RESTART").text
        self.assertIn("action=RESTART", filtered)
        # page hors bornes : ramenée dans la plage, pas d'erreur
        self.assertEqual(self.client.get("/audit?page=999").status_code, 200)
        self.assertEqual(self.client.get("/audit?page=abc").status_code, 200)


class TestGameSettingsCard(ApiTestBase):
    def test_owner_sees_card_and_toggles_gamerule(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/").text
        self.assertIn("game-card", page)
        self.assertIn("/actions/game/difficulty", page)
        res = self.client.post(
            "/actions/game/gamerule",
            data={"name": "keep_inventory", "value": "true", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.game.gamerules["keep_inventory"], True)

    def test_home_card_uses_switches_and_tooltips(self):
        self.login("jeremy", "owner-pw")
        card = self.client.get("/").text.split("game-card")[1].split("</section>")[0]
        self.assertIn('class="switch"', card)                        # V6.4 : mêmes interrupteurs que /game
        self.assertIn("info-tip", card)
        self.assertIn("gardent inventaire et expérience", card)      # info-bulle FR de keep_inventory
        self.assertNotIn("rule-toggle", card)                        # ancien bouton retiré

    def test_full_settings_page_lists_all_rules(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/game")
        self.assertEqual(page.status_code, 200)
        self.assertIn("random_tick_speed", page.text)   # règle numérique listée
        self.assertIn("keep_inventory", page.text)
        self.assertIn('href="/game"', self.client.get("/").text)  # lien sidebar + carte

    def test_full_settings_page_has_switches_and_help_tooltips(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/game").text
        self.assertIn('class="switch"', page)                        # booléens en interrupteur
        self.assertIn("info-tip", page)                              # info-bulle présente
        self.assertIn("gardent inventaire et expérience", page)      # description FR de keep_inventory

    def test_numeric_gamerule_via_full_page(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/game/gamerule",
            data={"name": "random_tick_speed", "value": "6", "redirect_to": "/game",
                  "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/game")
        self.assertEqual(self.game.gamerules["random_tick_speed"], 6)

    def test_unknown_rule_and_bad_value_flash(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/game/gamerule",
                         data={"name": "pas_une_regle", "value": "true", "csrf_token": self.page_csrf()})
        self.assertIn("JEU-01", self.client.get("/").text)
        self.client.post("/actions/game/gamerule",
                         data={"name": "keep_inventory", "value": "peut-être", "csrf_token": self.page_csrf()})
        self.assertIn("JEU-01", self.client.get("/").text)

    def test_friend_has_no_card_and_403(self):
        self.login("sam", "friend-pw")
        self.assertNotIn("/actions/game/", self.client.get("/").text)
        res = self.client.post(
            "/actions/game/weather",
            data={"kind": "clear", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.game.game_calls, [])

    def test_invalid_value_flashes_error(self):
        self.login("jeremy", "owner-pw")
        self.client.post(
            "/actions/game/difficulty",
            data={"level": "extreme", "csrf_token": self.page_csrf()},
        )
        self.assertIn("JEU-01", self.client.get("/").text)

    def test_card_degrades_when_rcon_down(self):
        self.game._available = False
        self.login("jeremy", "owner-pw")
        page = self.client.get("/").text
        self.assertIn("Le jeu ne répond pas", page)
        self.assertIn("JEU-90", page)


class TestPlayerDetailPage(ApiTestBase):
    def test_friend_can_view_detail_with_sessions(self):
        self.login("sam", "friend-pw")
        page = self.client.get("/players/alice")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Temps de jeu", page.text)
        self.assertIn("Sessions", page.text)

    def test_detail_shows_vanilla_stats(self):
        # FakePlayerStats : alice = 60480 s de jeu, 14 morts, 34,5 km.
        self.login("sam", "friend-pw")
        page = self.client.get("/players/alice").text
        self.assertIn("total serveur", page)             # temps vanilla prioritaire
        self.assertIn("Créatures tuées", page)
        self.assertIn("34.5", page)                      # 3 454 828 cm -> km
        self.assertIn("depuis la création du monde", page)

    def test_list_uses_vanilla_playtime(self):
        # 60480 s = 16 h 48 — affiché même si l'historique observé diffère.
        self.login("sam", "friend-pw")
        self.assertIn("16 h 48", self.client.get("/players").text)

    def test_stats_unavailable_page_still_works(self):
        self.client.app.state.service._player_stats = None
        self.login("sam", "friend-pw")
        page = self.client.get("/players/alice")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("total serveur", page.text)     # repli : temps observé

    def test_list_links_to_detail(self):
        self.login("sam", "friend-pw")
        self.assertIn('href="/players/alice"', self.client.get("/players").text)

    def test_invalid_name_404(self):
        self.login("sam", "friend-pw")
        self.assertEqual(self.client.get("/players/bad%20name!").status_code, 404)


class TestServersPage(ApiTestBase):
    def test_owner_sees_checks_and_detection(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/servers")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Serveur principal", page.text)
        self.assertIn("piloté", page.text)
        self.assertIn("6/6 opérationnels", page.text)              # tous les fakes répondent
        self.assertIn("Console RCON", page.text)
        self.assertIn("mc-creatif", page.text)                     # candidat détecté
        self.assertIn("déjà administré", page.text)                # minecraft reconnu
        self.assertIn('href="/servers"', self.client.get("/").text)  # sidebar owner

    def test_checks_explain_failures(self):
        from domain.errors import ServerUnavailable
        self.container.status = lambda: (_ for _ in ()).throw(ServerUnavailable("proxy down"))
        self.game._available = False
        self.login("jeremy", "owner-pw")
        page = self.client.get("/servers").text
        self.assertIn("injoignable via le proxy Docker", page)
        self.assertIn("RCON_HOST", page)                           # quoi corriger

    def test_add_server_records_second_entry(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/servers/add", data={
            "name": "Serveur créatif", "container": "mc-creatif",
            "rcon_host": "mc-creatif", "rcon_port": "25575",
            "csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.servers.servers[1].id, "serveur-cr-atif".replace("--", "-"))
        self.assertIn("multi-serveur", self.client.get("/servers").text)  # flash + statut

    def test_added_server_on_fresh_install_shows_restart_hint(self):
        # install fraîche : aucun serveur -> ajout -> « prendra effet au redémarrage »
        self.servers.servers.clear()
        self.login("jeremy", "owner-pw")
        page = self.client.get("/servers").text
        self.assertIn("Aucun serveur administré", page)             # état vide guidé
        self.client.post("/actions/servers/add", data={
            "name": "Mon serveur", "container": "mon-mc",
            "rcon_host": "mon-mc", "csrf_token": self.page_csrf()})
        page = self.client.get("/servers").text
        self.assertIn("prendra effet au redémarrage", page)
        self.assertNotIn("6/6", page)                               # pas de checks mensongers

    def test_admin_cannot_access(self):
        self.login("paul", "admin-pw")
        self.assertEqual(self.client.get("/servers").status_code, 403)
        self.assertNotIn('href="/servers"', self.client.get("/").text)


class TestModsPage(ApiTestBase):
    def test_friend_sees_mods_with_metadata(self):
        self.login("sam", "friend-pw")
        page = self.client.get("/mods")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Lithium", page.text)
        self.assertIn("0.22.1", page.text)
        self.assertIn("Optimisations générales", page.text)
        self.assertIn('href="/mods"', self.client.get("/").text)   # entrée sidebar

    def test_anonymous_redirected(self):
        self.assertEqual(self.client.get("/mods").status_code, 303)


class TestConsolePage(ApiTestBase):
    def test_owner_can_view(self):
        self.login("jeremy", "owner-pw")
        self.assertEqual(self.client.get("/console").status_code, 200)

    def test_admin_403_and_hidden_from_sidebar(self):
        self.login("paul", "admin-pw")
        self.assertNotIn('href="/console"', self.client.get("/").text)
        self.assertEqual(self.client.get("/console").status_code, 403)

    def test_owner_sees_prompt_below_logs_on_status(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/").text
        self.assertIn('action="/actions/rcon"', page)
        self.assertIn('name="command"', page)
        self.assertIn("/static/app.js?v=", page)
        self.assertNotIn("Exécuter cette commande RCON", page)

    def test_admin_does_not_see_prompt_on_status(self):
        self.login("paul", "admin-pw")
        page = self.client.get("/").text
        self.assertNotIn('action="/actions/rcon"', page)

    def test_owner_sees_terminal_pause_search_and_favorites_controls(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/").text
        self.assertIn('id="termPauseToggle"', page)          # pause du défilement
        self.assertIn('id="termSearchCount"', page)           # compteur de résultats
        self.assertIn('id="termFavStar"', page)                # favoris (RCON)
        self.assertIn('id="termFavOpen"', page)
        self.assertIn('id="termFavList"', page)

    def test_viewer_without_console_does_not_see_favorites_controls(self):
        # Les favoris vivent dans le formulaire RCON — absent sans la console.
        self.login("sam", "friend-pw")
        page = self.client.get("/").text
        self.assertNotIn('id="termFavStar"', page)
        self.assertNotIn('id="termFavOpen"', page)

    def test_log_lines_carry_a_copy_button_and_text_span(self):
        self.login("sam", "friend-pw")
        frag = self.client.get("/fragment/status").text
        self.assertIn('class="ln-text"', frag)
        self.assertIn('class="ln-copy"', frag)

    def test_terminal_log_area_has_fixed_height(self):
        css_path = os.path.join(os.path.dirname(__file__), "..", "app", "api", "static", "style.css")
        with open(css_path, encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn(".term-body { height: 300px;", css)
        self.assertIn(".term-result {", css)
        self.assertIn("height: 120px", css)

    def test_owner_runs_command_and_sees_output(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/rcon",
            data={"command": "whitelist add toto", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/#terminal")  # ancre : reste sur le terminal
        page = self.client.get("/").text
        self.assertIn("whitelist add toto", page)
        self.assertIn("ran: whitelist add toto", page)  # sortie de FakeGame.raw()

    def test_admin_post_403_and_command_never_run(self):
        self.login("paul", "admin-pw")
        res = self.client.post(
            "/actions/rcon",
            data={"command": "op paul", "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.game.raw_calls, [])


class TestBackupsPage(ApiTestBase):
    def test_admin_can_view_with_archives_listed(self):
        self.login("paul", "admin-pw")
        page = self.client.get("/backups")
        self.assertEqual(page.status_code, 200)
        self.assertIn("world-20260701-0000.tar.gz", page.text)
        self.assertIn("/backups/world-20260701-0000.tar.gz/download", page.text)
        self.assertNotIn("Rétention", page.text)

    def test_owner_sees_profile_cards_with_retention_policy(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Manuelles", page.text)                      # carte du profil migré
        self.assertIn("conservées sans limite", page.text)         # politique du profil manuel
        self.assertIn("30 j + 12 mensuelles", page.text)           # politique du profil auto
        self.assertIn("tous les jours à 04:00", page.text)

    def test_backups_page_uses_in_app_restore_assistant(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertIn('id="restoreDialog"', page)
        self.assertIn("Restaurer une sauvegarde", page)
        self.assertNotIn("1. Archive", page)
        self.assertIn('class="js-restore-form"', page)
        self.assertIn('type="button" class="danger small js-restore-open"', page)
        self.assertIn("Demande envoyée...", page)
        self.assertIn("/activity-status", page)
        self.assertIn("activityProgressBar", page)
        self.assertNotIn("confirm(", page)

    def test_banner_is_server_rendered_without_local_storage(self):
        # Le serveur est la seule source de vérité du bandeau : pas de
        # mécanique localStorage, reload sur transition d'état uniquement.
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertNotIn("mcActivityCompletion", page)   # l'ancienne mécanique localStorage a disparu
        self.assertNotIn("mcRestoreSuccess", page)
        self.assertIn("window.location.reload()", page)
        self.assertIn('data-phase="idle"', page)

    def test_restore_status_endpoint_reports_idle(self):
        self.login("jeremy", "owner-pw")
        res = self.client.get("/backups/restore-status")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["pending"], False)
        self.assertEqual(res.json()["phase"], "idle")
        self.assertEqual(res.json()["active"], False)

    def test_activity_status_reports_manual_backup(self):
        self.profile_backup.running = True
        self.service._backup_started_at["manual"] = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        self.backup_archives._archives = [
            BackupArchive("manual/previous.tar.gz", 1_000, datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc)),
            BackupArchive("manual/current.tar.gz", 500, datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)),
        ]
        self.login("jeremy", "owner-pw")

        res = self.client.get("/backups/activity-status")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["active"], True)
        self.assertEqual(res.json()["kind"], "backup")
        self.assertEqual(res.json()["progress_percent"], 50)
        self.assertEqual(res.json()["archive"], "manual/current.tar.gz")

    def test_backups_page_shows_backup_activity_banner(self):
        self.profile_backup.running = True
        self.service._backup_started_at["manual"] = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertIn("data-activity-banner", page)
        self.assertIn("Sauvegarde « Manuelles » en cours", page)
        self.assertIn("/activity-status", page)

    def test_activity_banner_is_global_across_authorized_pages(self):
        self.profile_backup.running = True
        self.service._backup_started_at["manual"] = datetime(
            2026, 7, 1, 12, 0, tzinfo=timezone.utc
        )
        self.login("jeremy", "owner-pw")

        dashboard = self.client.get("/").text
        players = self.client.get("/players").text

        self.assertIn("Sauvegarde « Manuelles » en cours", dashboard)
        self.assertIn("Sauvegarde « Manuelles » en cours", players)
        self.assertIn('id="globalActivity"', dashboard)

    def test_scheduled_restart_is_visible_outside_dashboard(self):
        self.login("jeremy", "owner-pw")
        self.client.post(
            "/actions/schedule-restart",
            data={"seconds": "60", "csrf_token": self.page_csrf()},
        )

        page = self.client.get("/players").text

        self.assertIn("Redémarrage programmé", page)
        self.assertIn("activity-waiting", page)

    def test_activity_result_dismiss_returns_to_current_page(self):
        self.login("jeremy", "owner-pw")
        self.client.post(
            "/actions/backup-profiles/manual/run",
            data={"csrf_token": self.page_csrf()},
        )
        self.profile_backup.running = False
        response = self.client.post(
            "/actions/activity-result/dismiss",
            data={"csrf_token": self.page_csrf(), "redirect_to": "/players"},
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/players")

    def test_archive_types_are_visible(self):
        self.backup_archives._archives = [
            BackupArchive("manual/mc-manual-20260713-015422.tar.gz", 10, datetime(2026, 7, 12, 23, 54, tzinfo=timezone.utc)),
            BackupArchive("scheduled/mc-auto-20260713-010000.tar.gz", 10, datetime(2026, 7, 12, 23, 0, tzinfo=timezone.utc)),
            BackupArchive("restore-safety/safety-20260713-014000.tar.gz", 10, datetime(2026, 7, 12, 22, 40, tzinfo=timezone.utc)),
            BackupArchive("legacy/world-20260703-223843.tar.gz", 10, datetime(2026, 7, 3, tzinfo=timezone.utc)),
        ]
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertIn("manuelle", page)
        self.assertIn("auto", page)
        self.assertIn("sécurité", page)
        self.assertIn("historique", page)

    def test_archive_list_uses_human_titles_and_filters(self):
        self.backup_archives._archives = [
            BackupArchive("manual/manual-20260713-015422.tar.gz", 10, datetime(2026, 7, 12, 23, 54, tzinfo=timezone.utc)),
            BackupArchive("scheduled/auto-20260713-010000.tar.gz", 10, datetime(2026, 7, 12, 23, 0, tzinfo=timezone.utc)),
            BackupArchive("restore-safety/safety-20260713-014000.tar.gz", 10, datetime(2026, 7, 12, 22, 40, tzinfo=timezone.utc)),
        ]
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertIn("Toutes · 3", page)
        self.assertIn("Manuelles · 1", page)
        self.assertIn("Manuelles · 13/07/2026 01:54", page)      # titre = nom du profil
        self.assertIn("manual/manual-20260713-015422.tar.gz", page)

        filtered = self.client.get("/backups?type=auto").text    # onglet = id du profil
        self.assertIn("Automatiques · 13/07/2026 01:00", filtered)
        self.assertIn("scheduled/auto-20260713-010000.tar.gz", filtered)
        self.assertNotIn("manual/manual-20260713-015422.tar.gz", filtered)

    def test_archive_timeline_groups_days_and_sorts_newest_first(self):
        self.backup_archives._archives = [
            BackupArchive(
                "manual/older.tar.gz",
                10,
                datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc),
            ),
            BackupArchive(
                "manual/newest.tar.gz",
                10,
                datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc),
            ),
            BackupArchive(
                "scheduled/same-day.tar.gz",
                10,
                datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc),
            ),
        ]
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text

        self.assertIn('class="archive-timeline"', page)
        self.assertIn('data-archive-day="2026-07-13"', page)
        self.assertIn('data-archive-day="2026-07-12"', page)
        self.assertIn("13 juillet 2026", page)
        self.assertLess(page.index("manual/newest.tar.gz"), page.index("scheduled/same-day.tar.gz"))
        self.assertLess(page.index("scheduled/same-day.tar.gz"), page.index("manual/older.tar.gz"))

    def test_storage_line_and_integrity_badges(self):
        from datetime import datetime as dt, timezone as tz
        from domain.model import ArchiveCheck
        self.service._archive_checks = __import__("tests.fakes", fromlist=["FakeArchiveChecks"]).FakeArchiveChecks({
            "world-20260701-0000.tar.gz": ArchiveCheck(
                filename="world-20260701-0000.tar.gz", ok=True,
                checked_at=dt(2026, 7, 13, 8, 0, tzinfo=tz.utc),
                size_bytes=772000000, restorable=True,
                archive_created_at=self.backup_archives.list_archives()[0].created_at),
        })
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertIn("Go libres sur le volume", page)     # ligne d'espace disque
        self.assertIn("✓ vérifiée", page)                  # badge d'intégrité

    def test_backup_result_banner_appears_and_dismisses(self):
        from datetime import datetime as dt, timedelta, timezone as tz
        from domain.model import BackupArchive
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/backup-profiles/manual/run", data={"csrf_token": self.page_csrf()})
        # le conteneur a fini et une archive manual/ postérieure au départ existe
        self.profile_backup.running = False
        self.backup_archives._archives.append(BackupArchive(
            "manual/mc-manual-20990101-000000.tar.gz", 700_000_000,
            dt.now(tz.utc) + timedelta(seconds=5)))
        page = self.client.get("/backups").text
        self.assertIn("Sauvegarde « Manuelles » terminée", page)
        self.assertIn("/actions/activity-result/dismiss", page)
        self.assertIn('activity-success', page)
        self.client.post("/actions/activity-result/dismiss", data={"csrf_token": self.page_csrf()})
        self.assertNotIn("Sauvegarde « Manuelles » terminée", self.client.get("/backups").text)

    def test_backup_without_archive_reports_failure(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/backup-profiles/manual/run", data={"csrf_token": self.page_csrf()})
        self.profile_backup.running = False              # fini, mais aucune archive manual/
        page = self.client.get("/backups").text
        self.assertIn("Sauvegarde « Manuelles » échouée", page)
        self.assertIn("Aucune archive", page)   # apostrophe échappée par Jinja

    def test_active_banner_carries_kind_color_class(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/backup-profiles/manual/run", data={"csrf_token": self.page_csrf()})
        self.profile_backup.running = True               # sauvegarde active -> bleu
        self.assertIn("activity-backup", self.client.get("/backups").text)

    def test_legacy_tab_removed_but_archives_still_listed(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertNotIn('href="/backups?type=legacy"', page)     # plus d'onglet dédié
        self.assertIn("world-20260701-0000.tar.gz", page)         # visible dans « Toutes »
        self.assertIn('<span class="chip neutral">historique</span>', page)

    def test_restore_result_banner_persists_until_dismissed(self):
        from restore_coordinator import AUTO_RESTORE_USER

        self.login("jeremy", "owner-pw")
        self.client.post("/actions/restore",
                         data={"filename": "world-20260701-0000.tar.gz",
                               "csrf_token": self.page_csrf()})
        result = None
        for _ in range(4):                                  # restore -> waiting -> success
            result = self.service.tick_pending_restore(AUTO_RESTORE_USER)
            if result == "success":
                break
        self.assertEqual(result, "success")
        page = self.client.get("/backups").text
        self.assertIn("Restauration réussie", page)             # résultat rendu côté serveur
        self.assertIn("/actions/activity-result/dismiss", page) # croix de fermeture
        # le résultat SURVIT aux rechargements (plus de TTL)
        self.assertIn("Restauration réussie", self.client.get("/backups").text)
        # la croix le purge pour de bon
        res = self.client.post("/actions/activity-result/dismiss",
                               data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertNotIn("Restauration réussie", self.client.get("/backups").text)

    def test_destructive_profile_actions_use_in_app_dialog(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertIn('id="appConfirm"', page)                     # dialogue de confirmation GLOBAL (UI-2)
        self.assertIn("js-confirm-open", page)
        self.assertIn("/actions/backup-profiles/manual/delete", page)
        self.assertNotIn("/retention", page)                       # la rétention s'applique toute seule
        self.assertNotIn("confirm(", page)                         # jamais de confirm() natif ici

    def test_profile_dialog_asks_five_questions(self):
        # Refonte (retour Jeremy 20/07) : chaque étape POSE une question.
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertIn('id="profileDialog"', page)
        self.assertIn("Comment veux-tu l'appeler", page)
        self.assertIn("Où veux-tu la ranger", page)
        self.assertIn("Que veux-tu protéger", page)
        self.assertIn("À quelle fréquence", page)
        self.assertIn("Combien de temps garder", page)
        self.assertIn('name="include"', page)
        self.assertIn("Tout le serveur", page)

    def test_profile_dialog_is_a_step_by_step_wizard(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertIn('id="profileNext"', page)
        self.assertIn('id="profilePrev"', page)
        self.assertIn('id="profileDots"', page)
        # Seule la première étape est visible au chargement, cinq au total.
        self.assertIn('data-step="0">', page)
        self.assertIn('data-step="1" hidden', page)
        self.assertIn('data-step="2" hidden', page)
        self.assertIn('data-step="3" hidden', page)
        self.assertIn('data-step="4" hidden', page)

    def test_profile_dialog_destination_step_only_local_is_selectable(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertIn('<input type="radio" name="destination" value="local" checked>', page)
        self.assertIn('<input type="radio" name="destination" value="remote" disabled>', page)
        self.assertIn("bientôt", page)

    def test_profile_dialog_destination_uses_drawn_icons_not_emoji(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertIn('class="dest-icon"', page)
        self.assertNotIn("🖥", page)
        self.assertNotIn("☁", page)

    def test_profile_dialog_shows_destination_path_preview(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertIn('id="profileDestPath"', page)

    def test_edit_button_carries_the_real_destination_path(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertIn('data-dest="manual"', page)

    def test_admin_can_download_archive(self):
        fd, archive_path = tempfile.mkstemp(suffix=".tar.gz")
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"backup-bytes")
        self.addCleanup(os.remove, archive_path)
        self.backup_archives._paths["world-20260701-0000.tar.gz"] = archive_path

        self.login("paul", "admin-pw")
        res = self.client.get("/backups/world-20260701-0000.tar.gz/download")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b"backup-bytes")
        self.assertIn("world-20260701-0000.tar.gz", res.headers["content-disposition"])

    def test_friend_cannot_download_archive(self):
        self.login("sam", "friend-pw")
        res = self.client.get("/backups/world-20260701-0000.tar.gz/download")
        self.assertEqual(res.status_code, 403)

    def test_missing_download_returns_404(self):
        self.login("paul", "admin-pw")
        res = self.client.get("/backups/missing.tar.gz/download")
        self.assertEqual(res.status_code, 404)

    def test_friend_403(self):
        self.login("sam", "friend-pw")
        self.assertEqual(self.client.get("/backups").status_code, 403)

    def test_trigger_redirects_to_backups_page(self):
        self.login("paul", "admin-pw")
        res = self.client.post("/actions/backup-profiles/manual/run", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/backups")
        self.assertEqual(len(self.profile_backup.calls), 1)

    def test_empty_archives_shows_placeholder(self):
        self.backup_archives._archives = []
        self.login("paul", "admin-pw")
        self.assertIn("Aucune archive", self.client.get("/backups").text)

    def test_status_page_no_longer_has_backup_button(self):
        self.login("paul", "admin-pw")
        self.assertNotIn("/actions/backup", self.client.get("/").text)

    def test_no_storage_history_shows_honest_placeholder(self):
        self.login("jeremy", "owner-pw")
        self.assertIn("pas encore assez de jours suivis", self.client.get("/backups").text)

    def test_storage_history_shows_families_and_saturation_projection(self):
        from tests.fakes import FakeStorageHistory
        from domain.model import StorageSnapshot
        self.client.app.state.service._storage_history = FakeStorageHistory(snapshots=[
            StorageSnapshot(date="2026-07-01", free_bytes=10_737_418_240,
                            families={"manual": 1_073_741_824, "scheduled": 2_147_483_648}),
            StorageSnapshot(date="2026-07-05", free_bytes=6_442_450_944,
                            families={"manual": 1_073_741_824, "scheduled": 6_442_450_944}),
        ])
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertIn("storage-spark", page)
        self.assertIn("Manuelles ·", page)
        self.assertIn("Automatiques ·", page)
        self.assertIn("le volume serait plein vers le", page)

    def test_flat_storage_trend_says_no_saturation_predictable(self):
        from tests.fakes import FakeStorageHistory
        from domain.model import StorageSnapshot
        self.client.app.state.service._storage_history = FakeStorageHistory(snapshots=[
            StorageSnapshot(date="2026-07-01", free_bytes=1000, families={"manual": 100}),
            StorageSnapshot(date="2026-07-05", free_bytes=1000, families={"manual": 100}),
        ])
        self.login("jeremy", "owner-pw")
        self.assertIn("pas de saturation prévisible", self.client.get("/backups").text)


class TestRetentionApplyAction(ApiTestBase):
    def _seed_old_scheduled(self):
        old = BackupArchive(filename="scheduled/mc-auto-20250101-010000.tar.gz",
                            size_bytes=1048576,
                            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        self.backup_archives._archives.append(old)

    def test_owner_applies_and_archives_are_gone(self):
        self._seed_old_scheduled()
        self.login("jeremy", "owner-pw")
        res = self.client.post(
            "/actions/backup-profiles/auto/retention",
            data={"csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.backup_archives.deleted, ["scheduled/mc-auto-20250101-010000.tar.gz"])

    def test_button_hidden_when_nothing_to_delete(self):
        self.login("jeremy", "owner-pw")
        self.assertNotIn("/actions/retention/apply", self.client.get("/backups").text)

    def test_admin_403(self):
        self._seed_old_scheduled()
        self.login("paul", "admin-pw")
        res = self.client.post(
            "/actions/retention/apply",
            data={"csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.backup_archives.deleted, [])


class TestBackupProfilesUI(ApiTestBase):
    def test_owner_creates_profile_from_modal(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/backup-profiles", data={
            "name": "Monde seul", "include": ["world", "admin"], "hours": "12",
            "retention_daily_days": "7", "retention_monthly_months": "3",
            "csrf_token": self.page_csrf(),
        })
        self.assertEqual(res.status_code, 303)
        created = self.backup_profiles.get("monde-seul")
        self.assertEqual(created.include, ("world", "admin"))
        self.assertEqual(created.hours, 12)
        self.assertIn("Monde seul", self.client.get("/backups").text)  # nouvelle carte

    def test_admin_cannot_manage_profiles(self):
        self.login("paul", "admin-pw")
        res = self.client.post("/actions/backup-profiles", data={
            "name": "X", "include": ["world"], "csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 403)
        page = self.client.get("/backups").text
        self.assertNotIn('id="profileDialog"', page)               # pas de modale sans BACKUP_MANAGE
        self.assertIn("Sauvegarder maintenant", page)              # mais il peut déclencher

    def test_owner_updates_schedule_via_profile(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/backup-profiles/auto/update", data={
            "name": "Automatiques", "include": ["all"], "hours": "daily",
            "time_hhmm": "01:00", "retention_daily_days": "30",
            "retention_monthly_months": "12", "csrf_token": self.page_csrf(),
        })
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.backup_profiles.get("auto").daily_at, "01:00")
        self.assertIn("tous les jours à 01:00", self.client.get("/backups").text)

    def test_run_and_toggle_profile(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/backup-profiles/auto/run", data={"csrf_token": self.page_csrf()})
        self.assertEqual(self.profile_backup.calls[0]["dest"], "scheduled")
        self.client.post("/actions/backup-profiles/auto/toggle",
                         data={"enabled": "0", "csrf_token": self.page_csrf()})
        self.assertFalse(self.backup_profiles.get("auto").enabled)
        self.assertIn("suspendue", self.client.get("/backups").text)

    def test_empty_state_invites_creation(self):
        self.backup_profiles.profiles.clear()
        self.login("jeremy", "owner-pw")
        page = self.client.get("/backups").text
        self.assertIn("Aucune sauvegarde configurée", page)
        self.assertIn("Créer une configuration", page)


class TestRestoreAction(ApiTestBase):
    ARCHIVE = "world-20260701-0000.tar.gz"

    def test_restore_preflight_endpoint_reports_issues(self):
        self.login("jeremy", "owner-pw")
        ok = self.client.get(f"/backups/restore-preflight?filename={self.ARCHIVE}").json()
        self.assertEqual(ok["ok"], True)

        self.backup.running = True
        blocked = self.client.get(f"/backups/restore-preflight?filename={self.ARCHIVE}").json()
        self.assertEqual(blocked["ok"], False)
        self.assertIn("sauvegarde de sécurité déjà en cours", blocked["issues"])  # port V6.5

    def test_owner_can_restore_and_backs_up_first(self):
        from restore_coordinator import AUTO_RESTORE_USER

        self.login("jeremy", "owner-pw")
        self.assertIn("/actions/restore", self.client.get("/backups").text)  # bouton visible
        res = self.client.post(
            "/actions/restore",
            data={"filename": self.ARCHIVE, "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/backups")
        self.assertEqual(self.backup.triggers, 1)  # sauvegarde de sécurité avant écrasement
        self.assertEqual(self.restore.calls, [])   # jamais en parallèle avec la sauvegarde

        self.assertEqual(self.service.tick_pending_restore(AUTO_RESTORE_USER), "restored")
        self.assertEqual(self.restore.calls, [self.ARCHIVE])

    def test_activity_status_follows_restore_until_success(self):
        from restore_coordinator import AUTO_RESTORE_USER

        self.login("jeremy", "owner-pw")
        self.client.post(
            "/actions/restore",
            data={"filename": self.ARCHIVE, "csrf_token": self.page_csrf()},
        )
        self.assertEqual(self.service.tick_pending_restore(AUTO_RESTORE_USER), "restored")

        running = self.client.get("/backups/activity-status").json()
        self.assertEqual(running["phase"], "restore_running")
        self.assertEqual(running["kind"], "restore")

        self.container.health = "healthy"
        self.assertEqual(self.service.tick_pending_restore(AUTO_RESTORE_USER), "success")
        done = self.client.get("/backups/activity-status").json()
        self.assertEqual(done["phase"], "success")
        self.assertEqual(done["level"], "success")
        self.assertIn("operation_id", done)

    def test_activity_status_explains_minecraft_restart(self):
        from restore_coordinator import AUTO_RESTORE_USER

        self.login("jeremy", "owner-pw")
        self.client.post(
            "/actions/restore",
            data={"filename": self.ARCHIVE, "csrf_token": self.page_csrf()},
        )
        self.container.health = "starting"
        self.assertEqual(self.service.tick_pending_restore(AUTO_RESTORE_USER), "restored")
        self.assertIsNone(self.service.tick_pending_restore(AUTO_RESTORE_USER))

        waiting = self.client.get("/backups/activity-status").json()

        self.assertEqual(waiting["phase"], "waiting_minecraft")
        self.assertEqual(waiting["title"], "Restauration en cours")   # titre stable
        self.assertIn("Étape 3/3", waiting["message"])                # l'étape porte le détail

    def test_restore_safety_archives_have_security_type(self):
        self.login("jeremy", "owner-pw")
        self.client.post(
            "/actions/restore",
            data={"filename": self.ARCHIVE, "csrf_token": self.page_csrf()},
        )
        self.backup_archives._archives.insert(
            0,
            BackupArchive("restore-safety/safety-20260713-015422.tar.gz", 10, datetime.now(timezone.utc)),
        )
        page = self.client.get("/backups").text
        self.assertIn("sécurité", page)

    def test_restore_owner_only_hidden_and_403_for_admin(self):
        self.login("paul", "admin-pw")
        self.assertNotIn("/actions/restore", self.client.get("/backups").text)
        res = self.client.post(
            "/actions/restore",
            data={"filename": self.ARCHIVE, "csrf_token": self.page_csrf()},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.restore.calls, [])


class TestPlayersPage(ApiTestBase):
    def test_friend_can_view(self):
        self.login("sam", "friend-pw")
        page = self.client.get("/players")
        self.assertEqual(page.status_code, 200)
        self.assertIn("alice", page.text)
        self.assertIn("en ligne", page.text)
        # Aucune action joueur (le popover profil garde /actions/display-name + /logout).
        for action in ("/actions/kick", "/actions/ban", "/actions/op", "/actions/whitelist", "/actions/pardon"):
            self.assertNotIn(action, page.text)

    def test_player_names_open_an_accessible_detail_drawer(self):
        self.login("sam", "friend-pw")
        page = self.client.get("/players").text

        self.assertIn('id="playerDrawer"', page)
        self.assertIn('aria-labelledby="playerDrawerTitle"', page)
        self.assertIn('data-player-panel-url="/players/alice/panel"', page)
        self.assertIn('href="/players/alice"', page)

    def test_friend_player_panel_contains_stats_without_management_actions(self):
        self.login("sam", "friend-pw")
        panel = self.client.get("/players/alice/panel")

        self.assertEqual(panel.status_code, 200)
        self.assertIn('data-player-panel', panel.text)
        self.assertIn("Statistiques", panel.text)
        self.assertIn("Ouvrir la fiche complète", panel.text)
        self.assertNotIn("<html", panel.text)
        self.assertNotIn("/actions/", panel.text)

    def test_owner_player_panel_exposes_only_authorized_existing_actions(self):
        self.login("jeremy", "owner-pw")
        panel = self.client.get("/players/alice/panel")

        self.assertEqual(panel.status_code, 200)
        self.assertIn("/actions/kick", panel.text)
        self.assertIn("/actions/ban", panel.text)
        self.assertIn("/actions/whitelist/remove", panel.text)
        self.assertIn("/actions/op/add", panel.text)
        self.assertIn('name="redirect_to" value="/players"', panel.text)

    def test_player_panel_uses_live_status_when_history_is_stale(self):
        self.login("sam", "friend-pw")
        panel = self.client.get("/players/alice/panel")

        self.assertEqual(panel.status_code, 200)
        self.assertIn("en ligne", panel.text)
        self.assertIn("maintenant", panel.text)

    def test_invalid_player_panel_returns_not_found(self):
        self.login("sam", "friend-pw")
        self.assertEqual(self.client.get("/players/bad-name!/panel").status_code, 404)

    def test_online_status_enables_kick_even_without_open_history_session(self):
        self.login("paul", "admin-pw")
        page = self.client.get("/players").text
        self.assertIn("alice", page)
        self.assertIn("en ligne", page)
        self.assertIn("/actions/kick", page)

    def test_admin_sees_kick_for_online_players_only(self):
        from domain.model import PlayerSummary

        self.player_history._summary = [
            PlayerSummary(player="alice", total_seconds=3600.0, last_seen=datetime.now(timezone.utc), online=True)
        ]
        self.login("paul", "admin-pw")
        page = self.client.get("/players").text
        self.assertIn("/actions/kick", page)
        self.assertNotIn("/actions/ban", page)
        self.assertNotIn("/actions/whitelist", page)
        self.assertNotIn("/actions/op", page)

    def test_online_status_keeps_history_when_name_case_differs(self):
        from domain.model import Player, PlayerSummary

        self.player_history._summary = [
            PlayerSummary(player="jeremy", total_seconds=3660.0, last_seen=datetime.now(timezone.utc), online=False)
        ]
        self.player_stats.stats = []   # isole le comportement testé (fusion de casse)
        self.game._players = [Player("Jeremy")]
        self.login("sam", "friend-pw")
        page = self.client.get("/players").text
        self.assertIn("jeremy", page)
        self.assertIn("en ligne", page)
        self.assertIn("1 h 01", page)
        self.assertNotIn("jamais vu", page)

    def test_owner_sees_player_management_actions(self):
        from domain.model import PlayerSummary

        self.player_history._summary = [
            PlayerSummary(player="alice", total_seconds=3600.0, last_seen=datetime.now(timezone.utc), online=True)
        ]
        self.login("jeremy", "owner-pw")
        page = self.client.get("/players").text
        self.assertIn("/actions/kick", page)
        self.assertIn("/actions/ban", page)
        self.assertIn("/actions/whitelist/remove", page)
        self.assertIn("/actions/op/add", page)
        self.assertIn('name="redirect_to" value="/players"', page)

    def test_player_actions_use_in_app_dialog_and_more_menu(self):
        from domain.model import PlayerSummary

        self.player_history._summary = [
            PlayerSummary(player="alice", total_seconds=3600.0, last_seen=datetime.now(timezone.utc), online=True)
        ]
        self.login("jeremy", "owner-pw")
        page = self.client.get("/players").text
        self.assertIn('id="playerActionDialog"', page)
        self.assertIn("js-player-dialog", page)
        self.assertIn('type="button" class="ract" title="Kick alice"', page)
        self.assertIn('type="button" class="ract danger" title="Bannir alice"', page)
        self.assertIn('data-go-filter="wl"', page)
        self.assertNotIn("confirm(", page)
        self.assertNotIn("prompt(", page)

    def test_whitelist_actions_are_grouped_in_whitelist_filter(self):
        from domain.model import PlayerSummary

        self.player_history._summary = [
            PlayerSummary(player="charlie", total_seconds=120.0, last_seen=datetime.now(timezone.utc), online=False)
        ]
        self.game.whitelist_names = []
        self.login("jeremy", "owner-pw")
        page = self.client.get("/players").text
        self.assertIn('class="inline addform wl-addform" hidden', page)
        self.assertIn('list="knownNotWhitelisted"', page)
        self.assertIn('value="charlie"', page)
        self.assertNotIn('title="Ajouter à la whitelist"', page)

    def test_whitelist_state_is_case_insensitive(self):
        from domain.model import PlayerSummary

        self.player_history._summary = [
            PlayerSummary(player="trappeur", total_seconds=120.0, last_seen=datetime.now(timezone.utc), online=False)
        ]
        self.game.whitelist_names = ["Trappeur"]
        self.login("jeremy", "owner-pw")
        page = self.client.get("/players").text
        self.assertIn("whitelist", page)
        self.assertIn("/actions/whitelist/remove", page)
        self.assertNotIn('title="Ajouter à la whitelist"', page)

    def test_already_whitelisted_response_updates_player_state(self):
        from domain.model import PlayerSummary

        self.player_history._summary = [
            PlayerSummary(player="Trappeur", total_seconds=120.0, last_seen=datetime.now(timezone.utc), online=False)
        ]
        self.game.whitelist_names = []

        def already_whitelisted(player_name):
            return "Player is already whitelisted"

        self.game.whitelist_add = already_whitelisted
        self.login("jeremy", "owner-pw")
        token = self._csrf_from(self.client.get("/players").text)
        self.client.post(
            "/actions/whitelist/add",
            data={"player": "Trappeur", "csrf_token": token, "redirect_to": "/players"},
        )
        page = self.client.get("/players").text
        self.assertIn("Trappeur", page)
        self.assertIn("whitelist", page)
        self.assertIn("/actions/whitelist/remove", page)

    def test_owner_imports_known_players_without_playtime(self):
        self.player_history._summary = []
        self.game._players = []
        self.login("jeremy", "owner-pw")
        page = self.client.get("/players").text
        self.assertIn("bob", page)       # whitelist
        self.assertIn("Trappeur", page)  # ops.json
        self.assertIn("griefer", page)   # banned-players.json
        self.assertIn("jamais vu", page)

    def test_kick_from_players_redirects_back_to_players(self):
        self.login("paul", "admin-pw")
        res = self.client.post(
            "/actions/kick",
            data={
                "csrf_token": self._csrf_from(self.client.get("/players").text),
                "player": "alice",
                "reason": "test",
                "redirect_to": "/players",
            },
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/players")

    def test_online_player_rendered_as_en_ligne(self):
        from domain.model import PlayerSummary

        self.player_history._summary = [
            PlayerSummary(player="bob", total_seconds=120.0, last_seen=datetime.now(timezone.utc), online=True)
        ]
        self.login("sam", "friend-pw")
        page = self.client.get("/players").text
        self.assertIn("bob", page)
        self.assertIn("en ligne", page)

    def test_empty_history_shows_placeholder(self):
        self.player_history._summary = []
        self.player_stats.stats = []   # sans stats vanilla non plus, sinon alice apparaît
        self.game._players = []
        self.login("sam", "friend-pw")
        self.assertIn("Aucune session", self.client.get("/players").text)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestFirstRunSetup(unittest.TestCase):
    """Premier lancement (V6.2) : aucun compte -> écran de création."""

    def setUp(self):
        from api.app import create_app
        from config import Settings
        from adapters.credentials_store import JsonCredentials
        from adapters.display_names import JsonDisplayNames

        self.dir = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.dir)
        settings = Settings(
            rcon_host="minecraft", rcon_port=25575, rcon_password="x",
            session_secret="test-secret", minecraft_container="minecraft", mc_log_file="/nonexistent/latest.log",
            rbac_config=os.path.join(self.dir, "absent-roles.yml"),   # AUCUN fichier
            users_file=os.path.join(self.dir, "users.json"),
            audit_log=os.path.join(self.dir, "audit.jsonl"),
            cookie_secure=False, prometheus_url="http://prom:9090",
            metrics_config="/nonexistent/metrics.yml",
            mc_updater_container="mc-updater", ops_file="/nonexistent/ops.json",
        )
        from domain.services import AdminService
        from adapters.restart_schedule import InMemoryRestartSchedule
        from adapters.audit_file import FileAuditLog
        service = AdminService(
            game=FakeGame(), container=FakeContainer(running=True),
            logs=FakeLogs(["[INFO] ok"]), audit=FileAuditLog(settings.audit_log),
            clock=FixedClock(), container_name="minecraft", metrics=FakeMetrics(),
            updater=FakeUpdater(), ops=FakeOps([]), notifications=FakeNotifier(),
            player_history=FakePlayerHistory(), backup_archives=FakeBackupArchives(),
            bans=FakeBans(), temp_bans=FakeTempBans(),
            restart_schedule=InMemoryRestartSchedule(), restore=FakeRestore(),
        )
        app = create_app(
            settings, service=service, hasher=PlainHasher(),
            scheduler=FakeBackgroundTask(), player_watcher=FakeBackgroundTask(),
            tempban_scheduler=FakeBackgroundTask(), restart_scheduler=FakeBackgroundTask(),
            restore_coordinator=FakeBackgroundTask(), archive_verifier=FakeBackgroundTask(),
            display_names=JsonDisplayNames(os.path.join(self.dir, "dn.json")),
            password_overrides=JsonCredentials(os.path.join(self.dir, "pw.json")),
        )
        self.client = TestClient(app, follow_redirects=False)

    def _setup_csrf(self):
        page = self.client.get("/setup").text
        return CSRF_RE.search(page).group(1)

    def test_everything_redirects_to_setup_when_no_account(self):
        self.assertEqual(self.client.get("/login").headers.get("location"), "/setup")
        self.assertEqual(self.client.get("/").headers.get("location"), "/login")  # puis /setup
        page = self.client.get("/setup")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Bienvenue", page.text)
        self.assertIn("Créer le compte", page.text)

    def test_weak_inputs_rejected(self):
        token = self._setup_csrf()
        res = self.client.post("/setup", data={"username": "a!", "password": "longmotdepasse",
                                               "password_confirm": "longmotdepasse", "csrf_token": token})
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/setup", data={"username": "jeremy", "password": "court",
                                               "password_confirm": "court", "csrf_token": token})
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/setup", data={"username": "jeremy", "password": "longmotdepasse",
                                               "password_confirm": "different", "csrf_token": token})
        self.assertEqual(res.status_code, 400)

    def test_full_first_run_journey(self):
        token = self._setup_csrf()
        res = self.client.post("/setup", data={"username": "nouvel-admin", "password": "longmotdepasse",
                                               "password_confirm": "longmotdepasse", "csrf_token": token})
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/servers")  # connecté DIRECT, sur l'assistant
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("nouvel-admin", home.text)
        self.assertIn(">Jeu</a>", home.text)                 # entrée owner-only visible (UI-1 : « Jeu »)
        # /setup est désormais verrouillé, dans les deux sens
        self.assertEqual(self.client.get("/setup").headers.get("location"), "/login")
        locked = self.client.post("/setup", data={"username": "intrus", "password": "longmotdepasse",
                                                  "password_confirm": "longmotdepasse", "csrf_token": token})
        self.assertEqual(locked.status_code, 303)            # redirigé, pas créé
        # et les identifiants créés fonctionnent pour un login classique
        token = CSRF_RE.search(self.client.get("/").text).group(1)
        self.client.post("/logout", data={"csrf_token": token})
        login_page = self.client.get("/login").text
        token = CSRF_RE.search(login_page).group(1)
        res = self.client.post("/login", data={"username": "nouvel-admin",
                                               "password": "longmotdepasse", "csrf_token": token})
        self.assertEqual(res.status_code, 303)

    def test_failed_user_write_keeps_setup_available_and_removes_password(self):
        token = self._setup_csrf()
        self.client.app.state.users_store.add = lambda *_: (_ for _ in ()).throw(
            OSError("disk full")
        )

        res = self.client.post("/setup", data={
            "username": "nouvel-admin",
            "password": "longmotdepasse",
            "password_confirm": "longmotdepasse",
            "csrf_token": token,
        })

        self.assertEqual(res.status_code, 503)
        self.assertIn("données existantes ont été conservées", res.text)
        self.assertEqual(self.client.get("/setup").status_code, 200)
        self.assertIsNone(
            self.client.app.state.password_overrides.get("nouvel-admin")
        )


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestDegradedDashboard(ApiTestBase):
    def test_unreachable_container_shows_message_not_500(self):
        from domain.errors import ServerUnavailable
        self.container.status = lambda: (_ for _ in ()).throw(
            ServerUnavailable("conteneur 'minecraft' injoignable via le proxy"))
        self.login("jeremy", "owner-pw")
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)              # la page VIT
        self.assertIn("arrive pas à joindre le serveur", page.text)
        self.assertIn("SRV-90", page.text)                    # état codifié
        # le code renvoie vers le guide public (docs/codes.md#srv-90) :
        self.assertIn("docs/codes.md#srv-90", page.text)

    def test_degraded_states_link_to_the_public_codes_guide(self):
        self.game._available = False                          # le jeu ne répond pas
        self.login("jeremy", "owner-pw")
        page = self.client.get("/").text
        self.assertIn("Le jeu ne répond pas", page)
        self.assertIn("docs/codes.md#jeu-90", page)


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestUsersPage(ApiTestBase):
    def test_owner_sees_accounts(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/users")
        self.assertEqual(page.status_code, 200)
        self.assertIn("jeremy", page.text)
        self.assertIn("sam", page.text)                            # compte migré du YAML listé
        self.assertNotIn("roles.yml", page.text)                   # plus de notion d'origine
        self.assertIn('href="/users"', self.client.get("/").text)  # sidebar owner

    def test_admin_403(self):
        self.login("paul", "admin-pw")
        self.assertEqual(self.client.get("/users").status_code, 403)
        self.assertEqual(self.audit.entries[-1].outcome, "denied") # refus audité

    def test_create_account_and_login_with_it(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/users/add", data={
            "username": "copain", "role": "friend", "password": "motdepasse-copain",
            "csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertIn("phase=account_created account=copain", self.audit.entries[-1].detail)
        # le nouveau compte fonctionne immédiatement
        token = CSRF_RE.search(self.client.get("/").text).group(1)
        self.client.post("/logout", data={"csrf_token": token})
        res = self.login("copain", "motdepasse-copain")
        self.assertEqual(res.status_code, 303)
        home = self.client.get("/").text
        self.assertIn("copain", home)
        self.assertNotIn('href="/users"', home)                    # friend : pas de page Comptes

    def test_create_rejects_duplicates_and_weak_password(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/users/add", data={
            "username": "jeremy", "role": "friend", "password": "motdepasse-x",
            "csrf_token": self.page_csrf()})
        self.assertIn("existe déjà", self.client.get("/users").text)
        self.client.post("/actions/users/add", data={
            "username": "fragile", "role": "friend", "password": "court",
            "csrf_token": self.page_csrf()})
        self.assertNotIn("fragile", self.client.get("/users").text.split("Créer un compte")[0])

    def test_failed_user_write_does_not_leave_password_or_memory_account(self):
        self.login("jeremy", "owner-pw")
        self.client.app.state.users_store.add = lambda *_: (_ for _ in ()).throw(
            OSError("disk full")
        )

        self.client.post("/actions/users/add", data={
            "username": "fragile",
            "role": "friend",
            "password": "motdepasse-fragile",
            "csrf_token": self.page_csrf(),
        })

        self.assertNotIn("fragile", self.client.app.state.users)
        self.assertIsNone(self.password_overrides.get("fragile"))
        self.assertIn("Aucun compte", self.client.get("/users").text)

    def test_reset_password_works_for_yaml_account(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/users/reset-password", data={
            "username": "sam", "password": "nouveau-mdp-sam", "csrf_token": self.page_csrf()})
        token = CSRF_RE.search(self.client.get("/").text).group(1)
        self.client.post("/logout", data={"csrf_token": token})
        self.assertEqual(self.login("sam", "friend-pw").status_code, 401)   # l'overlay remplace le yaml
        self.assertEqual(self.login("sam", "nouveau-mdp-sam").status_code, 303)  # nouveau mdp actif

    def test_delete_guards(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/users/delete",
                         data={"username": "jeremy", "csrf_token": self.page_csrf()})
        self.assertIn("son propre compte", self.client.get("/users").text)

    def test_delete_migrated_account_survives_restart(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/users/delete",
                         data={"username": "sam", "csrf_token": self.page_csrf()})
        self.assertIn("phase=account_deleted account=sam", self.audit.entries[-1].detail)
        self.assertIsNone(self.password_overrides.get("sam"))
        audit_page = self.client.get("/audit").text
        self.assertIn("Compte supprimé", audit_page)
        self.assertIn("ancien mot de passe supprimé", audit_page)
        self.assertIn("oui", audit_page)
        token = CSRF_RE.search(self.client.get("/").text).group(1)
        self.client.post("/logout", data={"csrf_token": token})
        self.assertEqual(self.login("sam", "friend-pw").status_code, 401)  # plus de login
        # « redémarrage » : la migration (drapeau posé) ne le ressuscite pas
        from adapters.users_store import JsonUsers
        from config import load_rbac, migrate_yaml_users
        migrate_yaml_users(self.roles_path, JsonUsers(self.users_store_path), self.password_overrides)
        users, _ = load_rbac(self.roles_path, self.users_store_path)
        self.assertNotIn("sam", users)

    def test_change_role_of_migrated_account_persists(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/users/role", data={
            "username": "sam", "role": "admin", "csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertIn("phase=role_changed account=sam role=admin", self.audit.entries[-1].detail)
        token = CSRF_RE.search(self.client.get("/").text).group(1)
        self.client.post("/logout", data={"csrf_token": token})
        self.login("sam", "friend-pw")
        self.assertEqual(self.client.post(                 # sam a les droits admin, effectifs de suite
            "/actions/restart", data={"csrf_token": self.page_csrf()}).status_code, 303)
        from config import load_rbac                       # et le rôle tient au redémarrage
        users, _ = load_rbac(self.roles_path, self.users_store_path)
        self.assertEqual(users["sam"].role.name, "admin")

    def test_change_role_guards(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/users/role", data={
            "username": "jeremy", "role": "friend", "csrf_token": self.page_csrf()})
        self.assertIn("son propre rôle", self.client.get("/users").text)  # anti-lockout
        self.client.post("/actions/users/role", data={
            "username": "sam", "role": "superadmin", "csrf_token": self.page_csrf()})
        self.assertIn("rôle inconnu", self.client.get("/users").text)


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestUptime(ApiTestBase):
    def test_uptime_shown_when_running(self):
        from datetime import datetime, timedelta, timezone
        self.container.started_at = datetime.now(timezone.utc) - timedelta(days=3, hours=14, minutes=9)
        self.login("jeremy", "owner-pw")
        self.assertIn("depuis 3 j 14 h", self.client.get("/").text)

    def test_uptime_absent_when_stopped_or_unknown(self):
        from datetime import datetime, timedelta, timezone
        self.login("jeremy", "owner-pw")
        self.assertNotIn("depuis ", self.client.get("/").text)      # StartedAt indisponible
        self.container._running = False
        self.container.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
        self.assertNotIn("depuis ", self.client.get("/").text)      # serveur arrêté


class TestUsersStoreFormat(unittest.TestCase):
    def test_legacy_flat_format_still_read(self):
        import tempfile as tf

        from adapters.users_store import JsonUsers
        with tf.TemporaryDirectory() as d:
            path = os.path.join(d, "users.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"demo": "owner"}')               # format plat V6.2
            store = JsonUsers(path)
            self.assertEqual(store.all(), {"demo": "owner"})
            self.assertTrue(store.remove("demo"))           # suppression réelle (V6.5)
            self.assertEqual(store.all(), {})
            self.assertFalse(store.remove("demo"))          # déjà absent

    def test_legacy_tombstones_read_but_dropped_on_write(self):
        import tempfile as tf

        from adapters.users_store import JsonUsers
        with tf.TemporaryDirectory() as d:
            path = os.path.join(d, "users.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"users": {"demo": "owner"}, "removed": ["sam"]}')  # V6.4
            store = JsonUsers(path)
            self.assertEqual(store.legacy_removed(), {"sam"})  # lisible pour la migration
            store.add("copain", "friend")                      # première écriture
            with open(path, encoding="utf-8") as fh:
                self.assertNotIn("removed", fh.read())         # tombstones abandonnés


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestPerformancePage(ApiTestBase):
    def test_page_shows_tiles_top_and_chart(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/performance")
        self.assertEqual(page.status_code, 200)
        self.assertIn("perf-ranges", page.text)                    # sélecteur 24h/3j/7j
        self.assertIn("perf-axis", page.text)                      # repères horaires
        self.assertIn("fenêtre de 15 min", page.text)              # légende honnête
        self.assertIn("seuil dépassé", page.text)                  # pic à 60 ms dans le fake
        self.assertIn("perf-threshold", page.text)                 # ligne des 50 ms
        self.assertIn("zombie", page.text)                         # top entités
        self.assertIn("Nether", page.text)                         # nom de monde traduit
        self.assertIn("289", page.text)                            # chunks Surface
        self.assertIn('href="/performance"', self.client.get("/").text)  # sidebar

    def test_range_selector_drives_history_window(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/performance?range=3j")
        self.assertEqual(self.metrics.last_history_hours, 72)      # transmis au port
        self.assertIn('href="/performance?range=3j"\n         aria-current="true"', page.text)
        self.assertIn("fenêtre de 30 min", page.text)              # légende suit la plage
        self.client.get("/performance?range=nimporte")
        self.assertEqual(self.metrics.last_history_hours, 24)      # plage inconnue -> 24 h

    def test_viewer_can_see_it(self):
        self.login("sam", "friend-pw")                             # rôle lecture seule
        self.assertEqual(self.client.get("/performance").status_code, 200)

    def test_degrades_when_prometheus_down(self):
        self.metrics._available = False
        self.login("jeremy", "owner-pw")
        page = self.client.get("/performance")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Mesures indisponibles", page.text)
        self.assertIn("SUR-90", page.text)

    def test_mspt_chart_shows_annotated_markers(self):
        from datetime import datetime, timezone
        from domain.model import AuditEntry
        self.audit.entries.append(AuditEntry(
            timestamp=datetime.now(timezone.utc), username="backup-watch", role="automation",
            action="BACKUP_TRIGGER", target="minecraft", outcome="allowed",
            detail="phase=backup_done kind=scheduled archive=x.tar.gz size_mio=5",
        ))
        self.login("jeremy", "owner-pw")
        page = self.client.get("/performance").text
        self.assertIn("perf-marker-backup", page)
        self.assertIn("Sauvegarde ·", page)                        # infobulle SVG <title>

    def test_mc_admin_self_update_is_not_annotated(self):
        from datetime import datetime, timezone
        from domain.model import AuditEntry
        self.audit.entries.append(AuditEntry(
            timestamp=datetime.now(timezone.utc), username="jeremy", role="owner",
            action="UPDATE", target="minecraft", outcome="allowed",
            detail="phase=app_update_applied version=0.10.0",
        ))
        self.login("jeremy", "owner-pw")
        page = self.client.get("/performance").text
        self.assertNotIn("perf-marker-update", page)

    def test_empty_top_has_honest_message(self):
        from domain.model import PerformanceSnapshot
        self.metrics.perf = PerformanceSnapshot(tps=20.0, mspt_mean=1.0)
        self.login("jeremy", "owner-pw")
        page = self.client.get("/performance").text
        self.assertIn("Aucune entité chargée", page)
        self.assertIn("pas encore assez d", page.lower())          # courbe sans historique

    def test_owner_sees_threshold_settings_form_with_current_values(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/performance").text
        self.assertIn('action="/actions/performance/thresholds"', page)
        self.assertIn('name="mspt_threshold_ms"', page)
        self.assertIn('value="50"', page)                          # défaut du domaine
        self.assertIn("seuil à 50 millisecondes", page)             # aria-label suit le réglage

    def test_admin_does_not_see_threshold_settings_form(self):
        self.login("paul", "admin-pw")
        self.assertNotIn('action="/actions/performance/thresholds"',
                         self.client.get("/performance").text)

    def test_owner_can_update_thresholds_and_it_reflects_on_the_page(self):
        from tests.fakes import FakeAlertThresholds
        self.client.app.state.service._alert_thresholds = FakeAlertThresholds()
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/performance/thresholds", data={
            "mspt_threshold_ms": "80", "disk_min_free_gib": "25",
            "mspt_sustained_minutes": "5", "csrf_token": self.page_csrf(),
        })
        self.assertEqual(res.status_code, 303)
        page = self.client.get("/performance").text
        self.assertIn('value="80"', page)
        self.assertIn("seuil à 80 millisecondes", page)
        self.assertIn("phase=alert_thresholds_updated", self.audit.entries[-1].detail)

    def test_invalid_threshold_shows_flash_error(self):
        from tests.fakes import FakeAlertThresholds
        self.client.app.state.service._alert_thresholds = FakeAlertThresholds()
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/performance/thresholds", data={
            "mspt_threshold_ms": "0", "disk_min_free_gib": "25",
            "mspt_sustained_minutes": "5", "csrf_token": self.page_csrf(),
        }, follow_redirects=True)
        self.assertIn("PRF-03", res.text)


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestWatchPage(ApiTestBase):
    def test_owner_sees_watched_and_candidates(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/watch")
        self.assertEqual(page.status_code, 200)
        self.assertIn("playit", page.text)                          # compagnon surveillé (migré)
        self.assertIn("toujours surveillé", page.text)              # ligne serveur
        self.assertIn("watchtower", page.text)                      # candidat proposé
        self.assertNotIn("Canaux de notification", page.text)       # déménagé page Notifications
        self.assertNotIn('option value="minecraft"', page.text)     # serveur piloté exclu des candidats
        self.assertIn('href="/watch"', self.client.get("/").text)   # sidebar

    def test_add_and_remove_are_audited(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/watch/add",
                         data={"name": "watchtower", "csrf_token": self.page_csrf()})
        self.assertIn("phase=watch_added container=watchtower", self.audit.entries[-1].detail)
        self.assertIn("watchtower", self.watched.names)
        self.client.post("/actions/watch/remove",
                         data={"name": "watchtower", "csrf_token": self.page_csrf()})
        self.assertIn("phase=watch_removed container=watchtower", self.audit.entries[-1].detail)
        self.assertNotIn("watchtower", self.watched.names)

    def test_duplicate_and_invalid_rejected_with_flash(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/watch/add",
                         data={"name": "playit", "csrf_token": self.page_csrf()})
        self.assertIn("déjà surveillé", self.client.get("/watch").text)
        self.client.post("/actions/watch/add",
                         data={"name": "haxor; rm -rf /", "csrf_token": self.page_csrf()})
        self.assertIn("invalide", self.client.get("/watch").text)

    def test_viewer_403_and_no_sidebar_entry(self):
        self.login("sam", "friend-pw")
        self.assertEqual(self.client.get("/watch").status_code, 403)
        self.assertNotIn('href="/watch"', self.client.get("/").text)

    def test_infra_card_on_dashboard_shows_companion_down(self):
        self.companions["playit"]._running = False
        self.login("jeremy", "owner-pw")
        page = self.client.get("/").text
        self.assertIn("Infrastructure", page)
        self.assertIn("playit", page)
        self.assertIn("exited", page)                               # état honnête du compagnon

    def test_infra_card_hidden_without_companions(self):
        self.watched.names.clear()
        self.login("jeremy", "owner-pw")
        self.assertNotIn("infra-card", self.client.get("/").text)  # la tuile serveur suffit


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestNotificationChannelsUI(ApiTestBase):
    def setUp(self):
        super().setUp()
        # Le bouton « Tester » construit un VRAI canal — on le stubbe pour
        # rester hermétique (zéro réseau en test).
        import adapters.notifications as mod
        self.test_sends = []
        outer = self

        class _StubChannel:
            def __init__(self, ref):
                self.ref = ref

            def notify(self, *args, **kwargs):
                outer.test_sends.append(self.ref)

        original = mod.build_channel
        mod.build_channel = lambda ctype, config: _StubChannel(ctype)
        self.addCleanup(lambda: setattr(mod, "build_channel", original))

    def test_page_lists_channel_cards_and_assistant(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/notifications")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Discord (importé)", page.text)               # canal migré en carte
        self.assertIn("Joueurs (arrivées/départs)", page.text)      # switch d'événement inline
        self.assertIn("Ajouter un canal", page.text)
        self.assertIn('id="channelDialog"', page.text)              # assistant présent
        self.assertIn('href="/notifications"', self.client.get("/").text)  # sidebar

    def test_create_channel_with_per_channel_events(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/notify-channels", data={
            "type": "telegram", "label": "Mon Telegram",
            "bot_token": "tok-secret", "chat_id": "42",
            "player": "on",                                          # joueurs OUI ici…
            "csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        created = next(c for c in self.notify_config.list_channels() if c["type"] == "telegram")
        self.assertTrue(created["events"]["player"])
        self.assertFalse(any(v for k, v in created["events"].items() if k != "player"))
        importe = self.notify_config.get("discord-importe")
        self.assertTrue(importe["events"]["player"])                 # …indépendant de l'autre canal
        self.assertEqual(self.test_sends, ["telegram"])              # « et tester » a envoyé le test
        detail = self.audit.entries[-2].detail                       # -1 = notify_test du « et tester »
        self.assertIn("phase=notify_channel_added", detail)
        self.assertNotIn("tok-secret", detail)                       # jamais de secret au journal

    def test_missing_required_field_rejected(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/notify-channels", data={
            "type": "telegram", "label": "X", "bot_token": "t",      # chat_id manquant
            "csrf_token": self.page_csrf()})
        self.assertIn("champ requis manquant", self.client.get("/notifications").text)

    def test_toggle_and_delete_channel(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/notify-channels/discord-importe/toggle",
                         data={"csrf_token": self.page_csrf()})
        self.assertFalse(self.notify_config.get("discord-importe")["enabled"])
        self.assertIn("suspendu", self.client.get("/notifications").text)
        self.client.post("/actions/notify-channels/discord-importe/delete",
                         data={"csrf_token": self.page_csrf()})
        self.assertIsNone(self.notify_config.get("discord-importe"))
        self.assertIn("phase=notify_channel_removed", self.audit.entries[-1].detail)

    def test_unknown_channel_test_flashes_failure(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/notify-channels/fantome/test",
                         data={"csrf_token": self.page_csrf()})
        self.assertIn("canal inconnu", self.client.get("/notifications").text)

    def test_viewer_403(self):
        self.login("sam", "friend-pw")
        self.assertEqual(self.client.get("/notifications").status_code, 403)
        self.assertNotIn('href="/notifications"', self.client.get("/").text)


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestServerAddress(ApiTestBase):
    def test_owner_sets_address_and_dashboard_shows_copy_chip(self):
        self.login("jeremy", "owner-pw")
        self.assertIn("Adresse de connexion", self.client.get("/servers").text)
        res = self.client.post("/actions/servers/principal/address", data={
            "address": "exemple.playit.gg", "csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertIn("phase=server_address_updated", self.audit.entries[-1].detail)
        home = self.client.get("/").text
        self.assertIn("exemple.playit.gg", home)
        self.assertIn('id="copyAddress"', home)                    # bouton copier

    def test_viewer_sees_address_but_cannot_edit(self):
        self.servers.set_address("principal", "exemple.playit.gg")
        self.login("sam", "friend-pw")
        self.assertIn("exemple.playit.gg", self.client.get("/").text)  # visible par tous
        res = self.client.post("/actions/servers/principal/address", data={
            "address": "pirate.example", "csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 403)

    def test_invalid_address_rejected(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/servers/principal/address", data={
            "address": "pas une adresse !", "csrf_token": self.page_csrf()})
        self.assertIn("invalide", self.client.get("/servers").text)
        self.assertNotIn("pas une adresse", self.client.get("/").text)

    def test_nav_groups_present(self):
        self.login("jeremy", "owner-pw")
        home = self.client.get("/").text
        self.assertIn("Protéger", home)
        self.assertIn("Administrer", home)


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestServerMap(ApiTestBase):
    def test_unconfigured_owner_sees_assistant_viewer_redirected(self):
        self.login("jeremy", "owner-pw")
        self.assertIn('href="/map"', self.client.get("/").text)    # owner : entrée assistant
        page = self.client.get("/map").text
        self.assertIn("Activer la carte du monde", page)           # état assistant
        self.assertIn("http://minecraft:8100", page)               # suggestion interne
        self.client.post("/logout", data={"csrf_token": self.page_csrf()})
        self.login("sam", "friend-pw")
        self.assertNotIn('href="/map"', self.client.get("/").text)  # viewer : rien
        self.assertEqual(self.client.get("/map", follow_redirects=False).status_code, 303)

    def test_assistant_test_then_activate(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/map/test", data={
            "map_url": "http://minecraft:8100", "csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertEqual(self.map_probe.calls, ["http://minecraft:8100"])
        self.assertIn("phase=map_test", self.audit.entries[-1].detail)
        followed = self.client.get(res.headers["location"]).text
        self.assertIn('value="http://minecraft:8100"', followed)   # brouillon conservé
        res = self.client.post("/actions/map", data={
            "server_id": "principal", "map_url": "http://minecraft:8100",
            "csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertIn("phase=server_map_updated", self.audit.entries[-1].detail)
        page = self.client.get("/map").text
        self.assertIn('src="/map/embed/"', page)                   # iframe MÊME ORIGINE
        self.assertNotIn("http://minecraft:8100</iframe>", page)

    def test_embed_streams_through_bounded_relay(self):
        self.servers.set_map_url("principal", "http://minecraft:8100")
        self.login("sam", "friend-pw")                             # viewer aussi
        self.assertIn('href="/map"', self.client.get("/").text)
        res = self.client.get("/map/embed/assets/index.js?v=1")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"FAKE MAP", res.content)
        self.assertEqual(self.map_requests[-1],
                         ("http://minecraft:8100", "assets/index.js", "v=1"))
        self.assertEqual(res.headers.get("etag"), "fake-etag")     # cache navigateur préservé
        self.assertEqual(res.headers.get("cache-control"), "max-age=86400")

    def test_embed_forwards_conditional_headers(self):
        self.servers.set_map_url("principal", "http://minecraft:8100")
        self.login("sam", "friend-pw")
        self.client.get("/map/embed/tile.png", headers={
            "If-None-Match": "fake-etag",
            "If-Modified-Since": "Sun, 19 Jul 2026 05:04:16 GMT"})
        self.assertEqual(self.map_conditional["if_none_match"], "fake-etag")
        self.assertEqual(self.map_conditional["if_modified_since"],
                         "Sun, 19 Jul 2026 05:04:16 GMT")
        # L'Accept-Encoding du navigateur suit aussi (compression de bout en
        # bout — les tuiles hires BlueMap pèsent x15 sans gzip) :
        self.assertIn("gzip", self.map_conditional["accept_encoding"])

    def test_embed_requires_configured_map_and_login(self):
        self.login("jeremy", "owner-pw")
        self.assertEqual(self.client.get("/map/embed/").status_code, 404)  # rien configuré
        self.client.post("/logout", data={"csrf_token": self.page_csrf()})
        self.servers.set_map_url("principal", "http://minecraft:8100")
        res = self.client.get("/map/embed/", follow_redirects=False)
        self.assertEqual(res.status_code, 303)                     # non connecté -> login

    def test_viewer_cannot_edit_map(self):
        self.servers.set_map_url("principal", "http://minecraft:8100")
        self.login("sam", "friend-pw")
        res = self.client.post("/actions/map", data={
            "server_id": "principal", "map_url": "http://pirate.example",
            "csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 403)
        res = self.client.post("/actions/map/test", data={
            "map_url": "http://pirate.example", "csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 403)

    def test_invalid_map_url_rejected(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/map", data={
            "server_id": "principal", "map_url": "javascript:alert(1)",
            "csrf_token": self.page_csrf()})
        self.assertIn("invalide", self.client.get("/map").text)
        self.assertIn("Activer la carte du monde", self.client.get("/map").text)  # toujours assistant

    def test_pasted_browser_url_is_cleaned_of_fragment(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/map", data={
            "server_id": "principal",
            "map_url": "http://192.0.2.10:8100/#overworld:0:0:0:1500:0:0:0:0:perspective",
            "csrf_token": self.page_csrf()})
        self.assertEqual(self.servers.servers[0].map_url, "http://192.0.2.10:8100")
        self.client.post("/actions/map/test", data={
            "map_url": "http://192.0.2.10:8100/#overworld:0:0", "csrf_token": self.page_csrf()})
        self.assertEqual(self.map_probe.calls[-1], "http://192.0.2.10:8100")

    def test_disabling_map_removes_nav_for_viewer(self):
        self.servers.set_map_url("principal", "http://minecraft:8100")
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/map", data={
            "server_id": "principal", "map_url": "", "csrf_token": self.page_csrf()})
        self.client.post("/logout", data={"csrf_token": self.page_csrf()})
        self.login("sam", "friend-pw")
        self.assertNotIn('href="/map"', self.client.get("/").text)


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestAppUpdate(ApiTestBase):
    # Versions RELATIVES à APP_VERSION : un bump de version ne doit jamais
    # casser ces tests (vécu au passage 0.9.0 -> 0.10.0).
    NEWER_VERSION = "0.99.0"

    def _announce(self, version=NEWER_VERSION):
        from domain.model import AppRelease
        self.app_update_state.set(
            AppRelease(version=version, notes="Corrections et améliorations.",
                       url="https://github.com/jmartinoty/mc-admin/releases/tag/v" + version),
            datetime.now(timezone.utc))

    def test_no_verdict_no_card(self):
        self.login("jeremy", "owner-pw")
        self.assertNotIn("Mise à jour de mc-admin", self.client.get("/").text)

    def test_same_version_no_card(self):
        from config import APP_VERSION
        self._announce(APP_VERSION)
        self.login("jeremy", "owner-pw")
        self.assertNotIn("Mise à jour de mc-admin", self.client.get("/").text)

    def test_card_shown_to_owner_with_apply_button(self):
        self._announce()
        self.login("jeremy", "owner-pw")
        page = self.client.get("/").text
        self.assertIn("Mise à jour de mc-admin disponible", page)
        self.assertIn("v" + self.NEWER_VERSION, page)
        self.assertIn("Corrections et améliorations.", page)
        self.assertIn('action="/actions/app-update"', page)        # install ghcr -> bouton

    def test_card_hidden_from_viewer(self):
        self._announce()
        self.login("sam", "friend-pw")
        self.assertNotIn("Mise à jour de mc-admin", self.client.get("/").text)

    def test_git_install_shows_honest_reason_instead_of_button(self):
        self._announce()
        self.self_image.value = "mc-admin:latest"                  # build git local
        self.login("jeremy", "owner-pw")
        page = self.client.get("/").text
        self.assertIn("Mise à jour de mc-admin disponible", page)  # détection quand même
        self.assertNotIn('action="/actions/app-update"', page)
        self.assertIn("dépôt git", page)

    def test_apply_starts_updater_and_shows_waiting_page(self):
        self._announce()
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/app-update", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/updating")
        self.assertEqual(self.app_updater.starts, 1)
        self.assertIn("phase=app_update_applied version=" + self.NEWER_VERSION,
                      self.audit.entries[-1].detail)
        page = self.client.get("/updating").text
        self.assertIn("Mise à jour en cours", page)
        self.assertIn("/healthz", page)                            # sonde de retour

    def test_apply_refused_without_verdict(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/app-update", data={"csrf_token": self.page_csrf()})
        self.assertEqual(self.app_updater.starts, 0)
        self.assertIn("MAJ-01", self.client.get("/").text)         # flash_error à code

    def test_snooze_button_present_even_without_apply_button(self):
        self._announce()
        self.self_image.value = "mc-admin:latest"                  # build git : pas de bouton Appliquer
        self.login("jeremy", "owner-pw")
        self.assertIn('action="/actions/app-update/snooze"', self.client.get("/").text)

    def test_snooze_hides_the_banner_for_that_version(self):
        self._announce()
        self.login("jeremy", "owner-pw")
        self.assertIn("Mise à jour de mc-admin disponible", self.client.get("/").text)
        res = self.client.post("/actions/app-update/snooze", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 303)
        self.assertNotIn("Mise à jour de mc-admin disponible", self.client.get("/").text)
        self.assertIn("phase=app_update_snoozed version=" + self.NEWER_VERSION,
                      self.audit.entries[-1].detail)

    def test_snoozed_banner_reappears_for_a_newer_version(self):
        self._announce()
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/app-update/snooze", data={"csrf_token": self.page_csrf()})
        self.assertNotIn("Mise à jour de mc-admin disponible", self.client.get("/").text)
        self._announce(version="0.100.0")                          # une nouveauté sort entre-temps
        self.assertIn("Mise à jour de mc-admin disponible", self.client.get("/").text)

    def test_snooze_without_verdict_shows_flash_error(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/app-update/snooze", data={"csrf_token": self.page_csrf()})
        self.assertIn("MAJ-02", self.client.get("/").text)

    def test_apply_refused_on_git_install(self):
        self._announce()
        self.self_image.value = "mc-admin:latest"
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/app-update", data={"csrf_token": self.page_csrf()})
        self.assertEqual(self.app_updater.starts, 0)
        self.assertIn("phase=app_update_blocked", self.audit.entries[-1].detail)

    def test_apply_refused_during_restore(self):
        from datetime import timedelta

        from domain.model import PendingRestore
        self._announce()
        self.pending_restore.set(PendingRestore(
            filename="a.tar.gz", requested_by="jeremy",
            deadline=datetime.now(timezone.utc) + timedelta(minutes=15)))
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/app-update", data={"csrf_token": self.page_csrf()})
        self.assertEqual(self.app_updater.starts, 0)
        self.assertIn("restauration-en-cours", self.audit.entries[-1].detail)

    def test_apply_works_without_backup_tooling(self):
        # Install partielle (pas d'outil de sauvegarde) : un port absent n'est
        # pas une opération en cours — bug attrapé au banc réel du 19/07.
        self._announce()
        self.service._profile_backup = None
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/app-update", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.headers["location"], "/updating")
        self.assertEqual(self.app_updater.starts, 1)

    def test_apply_failure_is_audited_and_flashed(self):
        from domain.errors import UpdateUnavailable
        self._announce()
        self.app_updater.fail = UpdateUnavailable("conteneur absent")
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/app-update", data={"csrf_token": self.page_csrf()})
        self.assertIn("phase=app_update_failed", self.audit.entries[-1].detail)
        self.assertIn("MAJ-01", self.client.get("/").text)

    def test_viewer_cannot_apply(self):
        self._announce()
        self.login("sam", "friend-pw")
        res = self.client.post("/actions/app-update", data={"csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.app_updater.starts, 0)


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestPlayersSearchSort(ApiTestBase):
    def test_search_sort_controls_and_row_data(self):
        from domain.model import PlayerSummary

        self.player_history._summary = [
            PlayerSummary(player="Trappeur", total_seconds=120.0,
                          last_seen=datetime.now(timezone.utc), online=False)]
        self.login("jeremy", "owner-pw")
        page = self.client.get("/players").text
        self.assertIn('id="playerSearch"', page)                    # recherche
        self.assertIn('id="playerSort"', page)                      # tri
        self.assertIn('data-time="120"', page)                      # temps de jeu triable
        self.assertIn('data-last="', page)                          # dernière connexion triable


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestVitalsFragment(ApiTestBase):
    def test_fragment_returns_state_and_infra(self):
        self.login("jeremy", "owner-pw")
        res = self.client.get("/fragments/vitals")
        self.assertEqual(res.status_code, 200)
        self.assertIn('data-vitals="state"', res.text)
        self.assertIn("en ligne", res.text)
        self.assertIn("playit", res.text)                          # compagnon dans l'infra

    def test_fragment_degrades_and_requires_login(self):
        self.assertEqual(self.client.get("/fragments/vitals").status_code, 401)
        self.login("jeremy", "owner-pw")
        from domain.errors import ServerUnavailable
        def boom():
            raise ServerUnavailable("proxy down")
        self.container.status = boom
        res = self.client.get("/fragments/vitals")
        self.assertEqual(res.status_code, 200)
        self.assertIn("injoignable", res.text)                     # état honnête

    def test_dashboard_wraps_live_zones(self):
        self.login("jeremy", "owner-pw")
        home = self.client.get("/").text
        self.assertIn('id="vitalsState"', home)
        self.assertIn('id="vitalsInfra"', home)
        self.assertIn("/fragments/vitals", home)                   # le poll est branché

    def test_viewer_fragment_hides_runtime_and_companions(self):
        self.container.health = "healthy"
        self.login("sam", "friend-pw")

        fragment = self.client.get("/fragments/vitals").text

        self.assertIn("en ligne", fragment)
        self.assertNotIn("healthy", fragment)
        self.assertNotIn("playit", fragment)


class TestNoNativeConfirm(unittest.TestCase):
    def test_templates_never_use_native_confirm(self):
        """Règle UI (CLAUDE.md) : dialogues in-app uniquement — le dialogue
        global #appConfirm du shell remplace confirm()."""
        base = os.path.join(os.path.dirname(__file__), "..", "app", "api", "templates")
        offenders = []
        for root, _, files in os.walk(base):
            for name in files:
                if name.endswith(".html"):
                    with open(os.path.join(root, name), encoding="utf-8") as fh:
                        if "return confirm(" in fh.read():
                            offenders.append(name)
        self.assertEqual(offenders, [])


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestChannelCardSwitches(ApiTestBase):
    def test_cards_have_inline_event_switches(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/notifications").text
        card = page.split("profile-card")[1].split("</section>")[0]
        self.assertIn('class="switch"', card)
        self.assertIn("/actions/notify-channels/discord-importe/event", card)

    def test_toggle_flips_only_that_event(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/notify-channels/discord-importe/event", data={
            "event": "health", "value": "on", "csrf_token": self.page_csrf()})
        events = self.notify_config.get("discord-importe")["events"]
        self.assertEqual(events, {"player": True, "health": True, "restore": False})
        self.client.post("/actions/notify-channels/discord-importe/event", data={
            "event": "player", "csrf_token": self.page_csrf()})       # décoche
        events = self.notify_config.get("discord-importe")["events"]
        self.assertEqual(events, {"player": False, "health": True, "restore": False})

    def test_unknown_event_rejected(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/notify-channels/discord-importe/event", data={
            "event": "hacks", "value": "on", "csrf_token": self.page_csrf()})
        self.assertIn("événement inconnu", self.client.get("/notifications").text)
        self.assertEqual(self.notify_config.get("discord-importe")["events"]["player"], True)


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestSparkProfiling(ApiTestBase):
    def test_card_and_profiles_owner_only(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/performance").text
        self.assertIn("Profil spark", page)
        self.assertIn("profile-2026-07-17_17.06.46.sparkprofile", page)
        self.assertIn("spark.lucko.me", page)

    def test_viewer_has_no_spark_card(self):
        self.login("sam", "friend-pw")
        page = self.client.get("/performance").text
        self.assertNotIn("Profil spark", page)

    def test_start_arms_countdown_and_stop_saves(self):
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/performance/profile", data={
            "seconds": "60", "csrf_token": self.page_csrf()})
        self.assertEqual(res.headers["location"], "/performance?profiling=60")
        self.assertIn("spark profiler start --timeout 90", self.game.raw_calls)  # filet +30 s
        self.assertIn("phase=spark_start seconds=60", self.audit.entries[-1].detail)
        page = self.client.get("/performance?profiling=60").text
        self.assertIn("sparkCountdown", page)                       # compte à rebours armé
        self.client.post("/actions/performance/profile/stop",
                         data={"csrf_token": self.page_csrf()})
        self.assertIn("spark profiler stop --save-to-file", self.game.raw_calls)

    def test_invalid_duration_rejected(self):
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/performance/profile", data={
            "seconds": "9999", "csrf_token": self.page_csrf()})
        self.assertNotIn("spark profiler start --timeout 10029",
                         self.game.raw_calls)
        self.assertIn("durée invalide", self.client.get("/performance").text)

    def test_download_guards_traversal_and_permission(self):
        self.login("jeremy", "owner-pw")
        ok = self.client.get("/performance/spark/profile-2026-07-17_17.06.46.sparkprofile/download")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(
            self.client.get("/performance/spark/..%2F..%2Fdata%2Fusers.json/download").status_code,
            404)
        token = CSRF_RE.search(self.client.get("/").text).group(1)
        self.client.post("/logout", data={"csrf_token": token})
        self.login("sam", "friend-pw")
        denied = self.client.get("/performance/spark/profile-2026-07-17_17.06.46.sparkprofile/download")
        self.assertEqual(denied.status_code, 403)


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestChannelCreationGuards(ApiTestBase):
    def test_duplicate_config_rejected(self):
        self.login("jeremy", "owner-pw")
        data = {"type": "discord", "label": "Doublon",
                "webhook_url": "https://discord.example/hook",  # même config que l'importé
                "csrf_token": self.page_csrf()}
        self.client.post("/actions/notify-channels", data=data)
        page = self.client.get("/notifications").text
        self.assertIn("existe déjà", page)
        self.assertEqual(len(self.notify_config.list_channels()), 1)  # pas de 2e canal

    def test_telegram_error_is_translated_to_actionable_hint(self):
        import io
        import urllib.error

        import adapters.notifications as mod

        class BotToBot:
            def notify(self, *a, **k):
                raise urllib.error.HTTPError(
                    "https://api.telegram.org", 403, "Forbidden", {},
                    io.BytesIO(b'{"ok":false,"description":"Forbidden: the bot can\'t send messages to the bot"}'))
        original = mod.build_channel
        mod.build_channel = lambda t, c: BotToBot()
        self.addCleanup(lambda: setattr(mod, "build_channel", original))
        self.login("jeremy", "owner-pw")
        self.client.post("/actions/notify-channels/discord-importe/test",
                         data={"csrf_token": self.page_csrf()})
        page = self.client.get("/notifications").text
        self.assertIn("celui du bot lui-même", page)
        self.assertIn("/start", page)


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestTelegramChatDetection(ApiTestBase):
    def _stub(self, result=None, error=None):
        import adapters.notifications as mod
        original = mod.telegram_detect_chats
        def fake(token, timeout=6.0):
            self.detect_token = token
            if error:
                raise error
            return result or []
        mod.telegram_detect_chats = fake
        self.addCleanup(lambda: setattr(mod, "telegram_detect_chats", original))

    def test_multiple_chats_returned_for_picker(self):
        self._stub(result=[{"id": "969164401", "name": "Jérélien", "type": "privé"},
                           {"id": "-100123", "name": "Les copains", "type": "groupe"}])
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/notify-channels/detect-chats", data={
            "bot_token": "tok", "csrf_token": self.page_csrf()})
        chats = res.json()["chats"]
        self.assertEqual(len(chats), 2)                             # tous les chats proposés
        self.assertEqual(chats[1]["name"], "Les copains")
        self.assertEqual(self.detect_token, "tok")

    def test_empty_inbox_explains_start(self):
        self._stub(result=[])
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/notify-channels/detect-chats", data={
            "bot_token": "tok", "csrf_token": self.page_csrf()})
        self.assertIn("/start", res.json()["error"])

    def test_bad_token_and_missing_token(self):
        import io
        import urllib.error
        self._stub(error=urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b"")))
        self.login("jeremy", "owner-pw")
        res = self.client.post("/actions/notify-channels/detect-chats", data={
            "bot_token": "mauvais", "csrf_token": self.page_csrf()})
        self.assertIn("jeton invalide", res.json()["error"])
        res = self.client.post("/actions/notify-channels/detect-chats", data={
            "csrf_token": self.page_csrf()})
        self.assertIn("jeton", res.json()["error"])

    def test_viewer_403(self):
        self.login("sam", "friend-pw")
        res = self.client.post("/actions/notify-channels/detect-chats", data={
            "bot_token": "tok", "csrf_token": self.page_csrf()})
        self.assertEqual(res.status_code, 403)


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/httpx requis")
class TestModsModrinthBadges(ApiTestBase):
    def test_badges_update_available_and_unknown(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/mods").text
        self.assertIn("màj dispo", page)                             # lithium 0.22.1 -> 0.23.0
        self.assertIn("→ 0.23.0", page)
        self.assertIn("https://modrinth.com/project/lith", page)
        self.assertIn("inconnu de Modrinth", page)                   # mod perso, honnête
        self.assertIn("Modrinth vérifié le", page)

    def test_no_verdict_yet_shows_pending(self):
        self.mod_checks.checks = {}
        self.mod_checks.checked = None
        self.login("jeremy", "owner-pw")
        page = self.client.get("/mods").text
        self.assertIn("vérification à venir", page)
        self.assertNotIn("Modrinth vérifié le", page)


class TestReadableErrors(ApiTestBase):
    """Retour Jeremy 18/07 : erreur courte + code + détails dans un dialogue."""

    def test_structured_flash_shows_code_and_details_dialog(self):
        self.login("jeremy", "owner-pw")
        self.client.post(
            "/actions/game/gamerule",
            data={"name": "pas_une_regle", "value": "true", "csrf_token": self.page_csrf()},
        )
        page = self.client.get("/").text
        self.assertIn('class="err-code">JEU-01</span>', page)
        self.assertIn('id="errorDetails"', page)          # dialogue présent
        self.assertIn('id="errorDetailsCopy"', page)      # bouton copier
        self.assertNotIn("Traceback", page)

    def test_success_flash_stays_plain(self):
        self.login("jeremy", "owner-pw")
        self.client.post(
            "/actions/game/gamerule",
            data={"name": "keep_inventory", "value": "true", "csrf_token": self.page_csrf()},
        )
        page = self.client.get("/").text
        self.assertNotIn('id="errorDetails"', page)


class TestGameState(unittest.TestCase):
    """2d (retour Jeremy 18/07) : pastille honnête pendant le boot du jeu."""

    def _status(self, running=True, players_available=False, started_seconds_ago=60):
        from types import SimpleNamespace
        from datetime import datetime, timedelta, timezone
        started = datetime.now(timezone.utc) - timedelta(seconds=started_seconds_ago)
        return SimpleNamespace(
            container=SimpleNamespace(running=running, started_at=started),
            players_available=players_available,
        )

    def test_container_up_but_game_booting_is_starting(self):
        from api.routes.dashboard import _game_state
        self.assertEqual(_game_state(self._status()), "starting")

    def test_still_unreachable_after_five_minutes(self):
        from api.routes.dashboard import _game_state
        self.assertEqual(
            _game_state(self._status(started_seconds_ago=600)), "unreachable")

    def test_game_available_is_normal(self):
        from api.routes.dashboard import _game_state
        self.assertIsNone(_game_state(self._status(players_available=True)))
        self.assertIsNone(_game_state(self._status(running=False)))
        self.assertIsNone(_game_state(None))


class TestPolishUi(ApiTestBase):
    def test_players_page_has_skin_avatars(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/players").text
        self.assertIn("mc-heads.net/avatar/", page)
        self.assertIn('class="pavatar-img"', page)

    def test_shell_has_mobile_burger(self):
        self.login("jeremy", "owner-pw")
        page = self.client.get("/").text
        self.assertIn('id="navBurger"', page)
        self.assertIn('aria-expanded="false"', page)
