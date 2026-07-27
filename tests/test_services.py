"""Tests d'AdminService : RBAC côté serveur + audit + orchestration des ports.

Le domaine est exercé uniquement via des fakes de ports (aucun Docker/RCON).
"""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from domain.errors import (
    BackupArchiveNotFound,
    BackupUnavailable,
    InvalidGameValue,
    InvalidDuration,
    InvalidOpLevel,
    InvalidPlayerName,
    OpLevelsUnavailable,
    PermissionDenied,
    RestoreUnavailable,
    ServerUnavailable,
    UpdateBlocked,
    UpdateUnavailable,
)
from adapters.pending_restore import InMemoryPendingRestore
from adapters.restart_schedule import InMemoryRestartSchedule
from domain.model import (
    ArchiveCheck,
    AuditEntry,
    BackupArchive,
    PendingOpLevel,
    PendingRestore,
    Player,
    RestoreOperation,
    StorageSnapshot,
)
from domain.rbac import build_roles, build_users
from domain.services import AdminService

from tests.fakes import (
    FakePlayerStats,
    FakeArchiveChecks,
    FakeArchiveValidator,
    FakeBackup,
    FakeContainer,
    FakeDoorman,
    FakeGame,
    FakeIncidents,
    FakeLogs,
    FakeMetrics,
    FakeNotifier,
    FakeOpLevelsApply,
    FakeBackupArchives,
    FakeBans,
    FakeTempBans,
    FakePlayerHistory,
    FakeOps,
    FakePendingOpLevels,
    FakeRecurringRestart,
    FakeRestore,
    FakeUpdater,
    FixedClock,
    MutableClock,
    RecordingAudit,
)

ROLE_SPEC = {
    "owner": ["*"],
    "admin": ["STATUS", "LOGS_VIEW", "RESTART", "START", "BACKUP_TRIGGER", "BACKUP_DOWNLOAD", "KICK"],
    "friend": ["STATUS", "LOGS_VIEW"],
    "guest": [],
}
USER_SPEC = {
    "jeremy": {"role": "owner"},
    "paul": {"role": "admin"},
    "sam": {"role": "friend"},
    "vic": {"role": "guest"},
}

CONTAINER_NAME = "minecraft"


class ServiceTestBase(unittest.TestCase):
    def setUp(self):
        roles = build_roles(ROLE_SPEC)
        self.users = build_users(USER_SPEC, roles)
        self.game = FakeGame(players=[Player("alice"), Player("bob")])
        self.container = FakeContainer(running=True)
        self.backup = FakeBackup()
        self.logs = FakeLogs(["[INFO] hello", "[INFO] world"])
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
        self.doorman = FakeDoorman()
        self.incidents = FakeIncidents()
        self.pending_restore = InMemoryPendingRestore()
        self.player_stats = FakePlayerStats()
        self.service = AdminService(
            game=self.game,
            container=self.container,
            logs=self.logs,
            audit=self.audit,
            clock=FixedClock(),
            container_name=CONTAINER_NAME,
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
            restore_safety_backup=self.backup,
            doorman=self.doorman,
            incidents=self.incidents,
            pending_restore=self.pending_restore,
            player_stats=self.player_stats,
            archive_checks=self.archive_checks,
            archive_validator=self.archive_validator,
        )

    # raccourcis
    @property
    def owner(self):
        return self.users["jeremy"]

    @property
    def admin(self):
        return self.users["paul"]

    @property
    def friend(self):
        return self.users["sam"]

    @property
    def guest(self):
        return self.users["vic"]


class TestStatus(ServiceTestBase):
    def test_status_running_lists_players(self):
        status = self.service.get_status(self.friend)
        self.assertTrue(status.container.running)
        self.assertTrue(status.players_available)
        self.assertEqual([p.name for p in status.players_online], ["alice", "bob"])

    def test_status_read_is_not_audited_on_success(self):
        self.service.get_status(self.friend)
        self.assertEqual(self.audit.entries, [])

    def test_status_when_rcon_down_degrades_gracefully(self):
        self.game._available = False
        status = self.service.get_status(self.friend)
        self.assertTrue(status.container.running)
        self.assertFalse(status.players_available)
        self.assertEqual(status.players_online, ())

    def test_status_when_container_stopped_skips_rcon(self):
        self.container._running = False
        status = self.service.get_status(self.owner)
        self.assertFalse(status.players_available)
        self.assertEqual(self.game.raw_calls, [])  # RCON jamais sollicité

    def test_status_denied_for_guest_is_audited(self):
        with self.assertRaises(PermissionDenied):
            self.service.get_status(self.guest)
        self.assertEqual(len(self.audit.entries), 1)
        entry = self.audit.entries[0]
        self.assertEqual(entry.outcome, "denied")
        self.assertEqual(entry.action, "STATUS")
        self.assertEqual(entry.username, "vic")


class TestRestart(ServiceTestBase):
    def test_admin_can_restart_and_it_is_audited(self):
        self.service.restart(self.admin)
        self.assertEqual(self.container.restarts, 1)
        self.assertEqual(len(self.audit.entries), 1)
        entry = self.audit.entries[0]
        self.assertEqual(entry.outcome, "allowed")
        self.assertEqual(entry.action, "RESTART")
        self.assertEqual(entry.username, "paul")
        self.assertEqual(entry.role, "admin")
        self.assertEqual(entry.target, CONTAINER_NAME)
        self.assertEqual(entry.timestamp, FixedClock().now())

    def test_friend_cannot_restart_port_untouched_and_denied_audited(self):
        with self.assertRaises(PermissionDenied):
            self.service.restart(self.friend)
        self.assertEqual(self.container.restarts, 0)  # port jamais appelé
        self.assertEqual(len(self.audit.entries), 1)
        self.assertEqual(self.audit.entries[0].outcome, "denied")

    def test_restart_error_is_audited_and_reraised(self):
        self.container._fail = True
        with self.assertRaises(RuntimeError):
            self.service.restart(self.owner)
        self.assertEqual(len(self.audit.entries), 1)
        self.assertEqual(self.audit.entries[0].outcome, "error")

    def test_pending_op_levels_use_transactional_worker(self):
        self.pending_op_levels.entries["trappeur"] = PendingOpLevel("Trappeur", 2)
        self.service.restart(self.owner)
        self.assertEqual(self.op_levels_apply.calls, 1)
        self.assertEqual(self.container.restarts, 0)
        self.assertIn("phase=op_levels_started", self.audit.entries[-1].detail)
        self.assertIn("count=1", self.audit.entries[-1].detail)

    def test_pending_op_levels_success_is_recorded_after_worker_exits(self):
        self.pending_op_levels.entries["trappeur"] = PendingOpLevel("Trappeur", 2)
        self.service.restart(self.owner)
        self.op_levels_apply.running = False
        self.op_levels_apply.exit_code_value = 0

        self.service.tick_scheduled_restart(self.owner)

        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome), ("RESTART", "allowed"))
        self.assertIn("phase=op_levels_applied", entry.detail)
        self.assertEqual(self.notifier.sent[-1][0], "Niveaux OP appliqués")

    def test_pending_op_levels_failure_is_recorded_once(self):
        self.pending_op_levels.entries["trappeur"] = PendingOpLevel("Trappeur", 2)
        self.service.restart(self.owner)
        self.op_levels_apply.running = False
        self.op_levels_apply.exit_code_value = 12

        self.service.tick_scheduled_restart(self.owner)
        entries_after_failure = len(self.audit.entries)
        self.service.tick_scheduled_restart(self.owner)

        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome), ("RESTART", "error"))
        self.assertIn("phase=op_levels_failed", entry.detail)
        self.assertIn("exit_code=12", entry.detail)
        self.assertEqual(len(self.audit.entries), entries_after_failure)

    def test_second_op_levels_apply_is_rejected_while_first_is_tracked(self):
        self.pending_op_levels.entries["trappeur"] = PendingOpLevel("Trappeur", 2)
        self.service.restart(self.owner)

        with self.assertRaises(OpLevelsUnavailable):
            self.service.restart(self.owner)

        self.assertEqual(self.op_levels_apply.calls, 1)
        self.assertEqual(self.container.restarts, 0)
        self.assertEqual(self.audit.entries[-1].outcome, "error")

    def test_pending_op_levels_worker_failure_does_not_restart_directly(self):
        self.pending_op_levels.entries["trappeur"] = PendingOpLevel("Trappeur", 2)
        self.op_levels_apply.fail = True
        with self.assertRaises(ServerUnavailable):
            self.service.restart(self.owner)
        self.assertEqual(self.container.restarts, 0)
        self.assertEqual(self.audit.entries[-1].outcome, "error")

    def test_unreadable_pending_levels_block_restart_and_are_audited(self):
        def fail_read():
            raise OpLevelsUnavailable("état des niveaux OP corrompu")

        self.pending_op_levels.list_pending = fail_read
        with self.assertRaises(OpLevelsUnavailable):
            self.service.restart(self.owner)
        self.assertEqual(self.container.restarts, 0)
        self.assertEqual(self.op_levels_apply.calls, 0)
        self.assertEqual(self.audit.entries[-1].outcome, "error")


class TestScheduledRestart(unittest.TestCase):
    """Instance dédiée (pas ServiceTestBase) : besoin d'une horloge AVANÇABLE
    pour simuler l'écoulement du temps entre les ticks du planificateur."""

    def setUp(self):
        roles = build_roles(ROLE_SPEC)
        self.users = build_users(USER_SPEC, roles)
        self.game = FakeGame(players=[Player("alice")])
        self.container = FakeContainer(running=True)
        self.audit = RecordingAudit()
        self.clock = MutableClock()
        self.restart_schedule = InMemoryRestartSchedule()
        self.recurring = FakeRecurringRestart()
        self.pending_op_levels = FakePendingOpLevels()
        self.op_levels_apply = FakeOpLevelsApply()
        self.service = AdminService(
            game=self.game,
            container=self.container,
            logs=FakeLogs(),
            audit=self.audit,
            clock=self.clock,
            container_name=CONTAINER_NAME,
            metrics=FakeMetrics(),
            updater=FakeUpdater(),
            ops=FakeOps(),
            notifications=FakeNotifier(),
            player_history=FakePlayerHistory(),
            backup_archives=FakeBackupArchives(),
            bans=FakeBans(),
            temp_bans=FakeTempBans(),
            restart_schedule=self.restart_schedule,
            pending_op_levels=self.pending_op_levels,
            op_levels_apply=self.op_levels_apply,
            recurring_restart=self.recurring,
        )
        # Utilisateur système minimal (même esprit que SCHEDULED_RESTART_USER
        # dans app/restart_scheduler.py, sans en dépendre depuis le domaine).
        from domain.model import Permission, Role, User

        self.system_user = User(
            username="scheduled-restart",
            role=Role(name="automation", permissions=frozenset({Permission.RESTART}), grants_all=False),
        )

    @property
    def owner(self):
        return self.users["jeremy"]

    @property
    def admin(self):
        return self.users["paul"]

    @property
    def friend(self):
        return self.users["sam"]

    def test_owner_can_schedule_and_status_reflects_it(self):
        self.service.schedule_restart(self.owner, 30)
        status = self.service.scheduled_restart_status(self.owner)
        self.assertEqual(status.restart_at, self.clock.now() + timedelta(minutes=30))
        self.assertEqual(status.scheduled_by, "jeremy")
        self.assertIn("⚠ Redémarrage du serveur programmé dans 30 minute(s).", self.game.said)
        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome), ("RESTART", "allowed"))
        self.assertIn("30 minute(s)", entry.detail)

    def test_admin_can_schedule_too(self):
        self.service.schedule_restart(self.admin, 15)
        self.assertIsNotNone(self.service.scheduled_restart_status(self.admin))

    def test_friend_cannot_schedule(self):
        with self.assertRaises(PermissionDenied):
            self.service.schedule_restart(self.friend, 30)
        self.assertIsNone(self.restart_schedule.status())
        self.assertEqual(self.game.said, [])

    def test_invalid_duration_rejected(self):
        for bad_minutes in (0, -5):
            with self.subTest(minutes=bad_minutes):
                with self.assertRaises(InvalidDuration):
                    self.service.schedule_restart(self.owner, bad_minutes)
        self.assertIsNone(self.restart_schedule.status())

    def test_rescheduling_replaces_previous(self):
        self.service.schedule_restart(self.owner, 30)
        self.service.schedule_restart(self.owner, 10)
        status = self.service.scheduled_restart_status(self.owner)
        self.assertEqual(status.restart_at, self.clock.now() + timedelta(minutes=10))

    def test_cancel_clears_pending_schedule(self):
        self.service.schedule_restart(self.owner, 30)
        self.service.cancel_scheduled_restart(self.owner)
        self.assertIsNone(self.service.scheduled_restart_status(self.owner))
        self.assertIn("phase=restart_cancelled", self.audit.entries[-1].detail)

    def test_cancel_with_nothing_pending_is_a_noop_but_still_audited(self):
        self.service.cancel_scheduled_restart(self.owner)
        entry = self.audit.entries[-1]
        self.assertEqual(entry.outcome, "allowed")
        self.assertIn("rien à annuler", entry.detail)

    def test_friend_cannot_cancel_or_view_status(self):
        with self.assertRaises(PermissionDenied):
            self.service.cancel_scheduled_restart(self.friend)
        with self.assertRaises(PermissionDenied):
            self.service.scheduled_restart_status(self.friend)

    def test_tick_without_pending_schedule_does_nothing(self):
        self.service.tick_scheduled_restart(self.system_user)
        self.assertEqual(self.game.said, [])
        self.assertEqual(self.container.restarts, 0)

    def test_tick_executes_restart_once_due(self):
        self.service.schedule_restart(self.owner, 10)
        self.clock.moment += timedelta(minutes=10)
        self.service.tick_scheduled_restart(self.system_user)
        self.assertEqual(self.container.restarts, 1)
        self.assertEqual(self.game.saved, 1)
        self.assertIn("⚠ Redémarrage du serveur en cours...", self.game.said)
        self.assertIsNone(self.restart_schedule.status())
        entry = self.audit.entries[-1]
        self.assertEqual(entry.username, "scheduled-restart")
        self.assertEqual((entry.action, entry.outcome), ("RESTART", "allowed"))

    def test_tick_with_pending_op_level_starts_worker_not_direct_restart(self):
        self.pending_op_levels.entries["trappeur"] = PendingOpLevel("Trappeur", 2)
        self.service.schedule_restart(self.owner, 10)
        self.clock.moment += timedelta(minutes=10)
        self.service.tick_scheduled_restart(self.system_user)
        self.assertEqual(self.op_levels_apply.calls, 1)
        self.assertEqual(self.container.restarts, 0)
        self.assertIn("phase=op_levels_started", self.audit.entries[-1].detail)
        self.assertIn(
            f"operation_id=restart::{self.clock.now().isoformat()}",
            self.audit.entries[-1].detail,
        )
        self.assertIn("requested_by=jeremy", self.audit.entries[-1].detail)

        self.op_levels_apply.running = False
        self.op_levels_apply.exit_code_value = 0
        self.service.tick_scheduled_restart(self.system_user)
        self.assertIn("phase=op_levels_applied", self.audit.entries[-1].detail)
        self.assertIn("requested_by=jeremy", self.audit.entries[-1].detail)

    def test_tick_announces_thresholds_as_time_passes_without_duplicates(self):
        self.service.schedule_restart(self.owner, 11)  # 660s -> au-delà du seuil 600
        self.game.said.clear()  # ignore l'annonce immédiate de la programmation

        self.clock.moment += timedelta(seconds=70)  # reste 590s -> seuil 600 franchi
        self.service.tick_scheduled_restart(self.system_user)
        self.assertEqual(self.game.said, ["⚠ Redémarrage du serveur programmé dans 10 minute(s)"])

        self.service.tick_scheduled_restart(self.system_user)  # même seuil : pas de doublon
        self.assertEqual(len(self.game.said), 1)

        self.clock.moment += timedelta(seconds=300)  # reste 290s -> seuil 300 franchi
        self.service.tick_scheduled_restart(self.system_user)
        self.assertEqual(self.game.said[-1], "⚠ Redémarrage du serveur programmé dans 5 minute(s)")
        self.assertEqual(len(self.game.said), 2)

    def test_short_schedule_does_not_retroactively_announce_large_thresholds(self):
        self.service.schedule_restart(self.owner, 4)  # 240s : jamais >= 600/300
        self.game.said.clear()

        self.clock.moment += timedelta(seconds=181)  # reste 59s -> seuil 60 franchi
        self.service.tick_scheduled_restart(self.system_user)
        self.assertEqual(self.game.said, ["⚠ Redémarrage du serveur programmé dans 1 minute(s)"])

    def test_tick_propagates_restart_failure_after_auditing(self):
        # tick_scheduled_restart audite puis RELAIE l'erreur (même contrat que
        # restart()) : c'est au thread de fond (RestartWarningScheduler) de
        # l'avaler pour rester en vie, pas au service de la masquer.
        self.service.schedule_restart(self.owner, 10)
        self.clock.moment += timedelta(minutes=10)
        self.container._fail = True
        with self.assertRaises(RuntimeError):
            self.service.tick_scheduled_restart(self.system_user)
        self.assertEqual(self.audit.entries[-1].outcome, "error")

    # ---- récurrent quotidien ----

    def _local(self):
        return self.clock.now().astimezone()

    def _hhmm_in(self, minutes: int) -> str:
        return (self._local() + timedelta(minutes=minutes)).strftime("%H:%M")

    def test_set_recurring_validates_and_audits(self):
        self.service.set_recurring_restart(self.owner, "06:00", 300)
        self.assertEqual(self.recurring.config.time_hhmm, "06:00")
        self.assertIn("06:00", self.audit.entries[-1].detail)
        for bad in ("6h", "25:00", "12:61", ""):
            with self.subTest(time=bad):
                with self.assertRaises(InvalidDuration):
                    self.service.set_recurring_restart(self.owner, bad, 300)
        with self.assertRaises(InvalidDuration):
            self.service.set_recurring_restart(self.owner, "06:00", 0)

    def test_friend_cannot_configure_recurring(self):
        with self.assertRaises(PermissionDenied):
            self.service.set_recurring_restart(self.friend, "06:00", 300)
        self.assertIsNone(self.recurring.config)

    def test_tick_arms_oneshot_inside_lead_window_once_per_day(self):
        from domain.model import RecurringRestart
        self.recurring.config = RecurringRestart(time_hhmm=self._hhmm_in(3), lead_seconds=300)
        self.service.tick_scheduled_restart(self.system_user)
        pending = self.restart_schedule.status()
        self.assertIsNotNone(pending)  # armé : dans la fenêtre de préavis (3 min <= 5 min)
        self.assertEqual(pending.restart_at.strftime("%H:%M"), self.recurring.config.time_hhmm)
        self.assertEqual(self.recurring.fired_day, self._local().date().isoformat())
        # Second tick le même jour : n'arme pas une deuxième fois.
        self.restart_schedule.cancel()
        self.service.tick_scheduled_restart(self.system_user)
        self.assertIsNone(self.restart_schedule.status())

    def test_tick_waits_outside_lead_window(self):
        from domain.model import RecurringRestart
        self.recurring.config = RecurringRestart(time_hhmm=self._hhmm_in(30), lead_seconds=300)
        self.service.tick_scheduled_restart(self.system_user)
        self.assertIsNone(self.restart_schedule.status())  # trop tôt
        self.assertIsNone(self.recurring.fired_day)        # pourra s'armer plus tard

    def test_tick_skips_missed_window_without_catching_up(self):
        from domain.model import RecurringRestart
        self.recurring.config = RecurringRestart(time_hhmm=self._hhmm_in(-60), lead_seconds=300)
        self.service.tick_scheduled_restart(self.system_user)
        self.assertIsNone(self.restart_schedule.status())  # pas de redémarrage surprise
        self.assertEqual(self.recurring.fired_day, self._local().date().isoformat())

    def test_tick_never_overrides_manual_oneshot(self):
        from domain.model import RecurringRestart
        self.service.schedule_restart(self.owner, 60)  # one-shot manuel en attente
        manual = self.restart_schedule.status()
        self.recurring.config = RecurringRestart(time_hhmm=self._hhmm_in(3), lead_seconds=300)
        self.service.tick_scheduled_restart(self.system_user)
        self.assertEqual(self.restart_schedule.status(), manual)  # inchangé


class TestBackup(ServiceTestBase):
    """RBAC du déclenchement — via les profils depuis la V6.5 (le port
    legacy `backup` n'existe plus)."""

    def setUp(self):
        super().setUp()
        from domain.model import BackupProfile
        from tests.fakes import FakeBackupProfiles, FakeProfileBackup
        self.profiles = FakeBackupProfiles([
            BackupProfile(id="manual", name="Manuelles", include=("all",), dest="manual")])
        self.profile_backup = FakeProfileBackup()
        self.service._backup_profiles = self.profiles
        self.service._profile_backup = self.profile_backup

    def test_admin_can_trigger_backup(self):
        result = self.service.trigger_profile_backup(self.admin, "manual")
        self.assertTrue(result.started)
        self.assertEqual(len(self.profile_backup.calls), 1)
        self.assertEqual(self.audit.entries[-1].outcome, "allowed")

    def test_friend_cannot_trigger_backup(self):
        with self.assertRaises(PermissionDenied):
            self.service.trigger_profile_backup(self.friend, "manual")
        self.assertEqual(self.profile_backup.calls, [])
        self.assertEqual(self.audit.entries[-1].outcome, "denied")

    def test_backup_failure_notifies_error(self):
        self.profile_backup._fail = True
        with self.assertRaises(RuntimeError):
            self.service.trigger_profile_backup(self.owner, "manual")
        self.assertEqual(self.notifier.sent[-1][::2], ("Sauvegarde échouée", "error"))

    def test_admin_can_list_archives(self):
        archives = self.service.list_backups(self.admin)
        self.assertEqual(len(archives), 1)
        self.assertEqual(self.audit.entries, [])  # lecture non auditée

    def test_backup_progress_reports_manual_backup(self):
        self.profile_backup.running = True
        self.service._backup_started_at["manual"] = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        self.backup_archives._archives = [
            BackupArchive("manual/previous.tar.gz", 1_000, datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc)),
            BackupArchive("manual/current.tar.gz", 500, datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)),
        ]

        progress = self.service.backup_progress(self.admin)

        self.assertEqual(progress.kind, "manual")
        self.assertEqual(progress.label, "Manuelles")               # libellé du profil
        self.assertEqual(progress.progress_percent, 50)
        self.assertEqual(progress.archive.filename, "manual/current.tar.gz")

    def test_backup_progress_waits_until_active_archive_is_known(self):
        self.profile_backup.running = True
        self.service._backup_started_at["manual"] = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        self.backup_archives._archives = [
            BackupArchive("manual/previous.tar.gz", 1_000, datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc)),
        ]

        progress = self.service.backup_progress(self.admin)

        self.assertEqual(progress.kind, "manual")
        self.assertIsNone(progress.progress_percent)
        self.assertIsNone(progress.archive)

    def test_backup_progress_is_monotonic(self):
        self.profile_backup.running = True
        self.service._backup_started_at["manual"] = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        self.backup_archives._archives = [
            BackupArchive("manual/previous.tar.gz", 1_000, datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc)),
            BackupArchive("manual/current.tar.gz", 700, datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)),
        ]
        self.assertEqual(self.service.backup_progress(self.admin).progress_percent, 70)
        self.backup_archives._archives[1] = BackupArchive(
            "manual/current.tar.gz",
            300,
            datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(self.service.backup_progress(self.admin).progress_percent, 70)

    def test_backup_progress_reports_scheduled_backup(self):
        from domain.model import BackupProfile
        self.profiles.upsert(BackupProfile(
            id="auto", name="Automatiques", include=("all",), dest="scheduled"))
        self.profile_backup.running = True
        self.service._backup_started_at["auto"] = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        self.backup_archives._archives = [
            BackupArchive("scheduled/previous.tar.gz", 1_000, datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc)),
            BackupArchive("scheduled/current.tar.gz", 250, datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)),
        ]

        progress = self.service.backup_progress(self.admin)

        self.assertEqual(progress.kind, "auto")                     # id du profil
        self.assertEqual(progress.progress_percent, 25)
        self.assertEqual(progress.archive.filename, "scheduled/current.tar.gz")

    def test_friend_cannot_list_archives(self):
        with self.assertRaises(PermissionDenied):
            self.service.list_backups(self.friend)
        self.assertEqual(self.audit.entries[-1].outcome, "denied")

    def test_admin_can_resolve_download_path(self):
        path = self.service.backup_download_path(self.admin, "world-20260701-0000.tar.gz")
        self.assertEqual(path, "/tmp/world-20260701-0000.tar.gz")

    def test_friend_cannot_resolve_download_path(self):
        with self.assertRaises(PermissionDenied):
            self.service.backup_download_path(self.friend, "world-20260701-0000.tar.gz")
        self.assertEqual(self.audit.entries[-1].outcome, "denied")

    def test_missing_archive_download_path_raises(self):
        from domain.errors import BackupArchiveNotFound

        with self.assertRaises(BackupArchiveNotFound):
            self.service.backup_download_path(self.admin, "missing.tar.gz")

    def test_owner_can_preview_retention_plan(self):
        self.backup_archives._archives = [
            BackupArchive("manual/manual-old.tar.gz", 10, datetime(2025, 6, 1, tzinfo=timezone.utc)),
            BackupArchive("legacy-root.tar.gz", 10, datetime(2025, 6, 1, tzinfo=timezone.utc)),
            BackupArchive("scheduled/recent.tar.gz", 10, datetime(2026, 6, 20, tzinfo=timezone.utc)),
            BackupArchive("scheduled/same-month-old.tar.gz", 10, datetime(2026, 6, 1, tzinfo=timezone.utc)),
            BackupArchive("scheduled/may-newest.tar.gz", 10, datetime(2026, 5, 31, tzinfo=timezone.utc)),
            BackupArchive("scheduled/may-older.tar.gz", 10, datetime(2026, 5, 1, tzinfo=timezone.utc)),
            BackupArchive("scheduled/too-old.tar.gz", 10, datetime(2025, 6, 1, tzinfo=timezone.utc)),
        ]

        plan = self.service.backup_retention_plan(self.owner)

        # Les protégées (manuelles, historique) sont HORS périmètre : ni
        # comptées ni supprimables — le compteur UI ne parle que des autos.
        self.assertEqual(
            [item.archive.filename for item in plan.keep],
            [
                "scheduled/recent.tar.gz",
                "scheduled/may-newest.tar.gz",
            ],
        )
        self.assertEqual(
            [item.archive.filename for item in plan.delete],
            [
                "scheduled/same-month-old.tar.gz",
                "scheduled/may-older.tar.gz",
                "scheduled/too-old.tar.gz",
            ],
        )
        self.assertEqual(self.audit.entries, [])  # dry-run lecture non auditée

    def test_admin_cannot_preview_retention_plan(self):
        with self.assertRaises(PermissionDenied):
            self.service.backup_retention_plan(self.admin)
        self.assertEqual(self.audit.entries[-1].outcome, "denied")

    def test_retention_plan_uses_custom_policy(self):
        self.backup_archives._archives = [
            BackupArchive("scheduled/day-20.tar.gz", 10, datetime(2026, 6, 11, tzinfo=timezone.utc)),
            BackupArchive("scheduled/day-15.tar.gz", 10, datetime(2026, 6, 16, tzinfo=timezone.utc)),
        ]

        plan = self.service.backup_retention_plan(self.owner, daily_days=10, monthly_months=0)

        self.assertEqual([item.archive.filename for item in plan.keep], [])
        self.assertEqual(
            [item.archive.filename for item in plan.delete],
            ["scheduled/day-15.tar.gz", "scheduled/day-20.tar.gz"],
        )


class TestRestore(ServiceTestBase):
    ARCHIVE = "world-20260701-0000.tar.gz"  # présent dans FakeBackupArchives par défaut

    def test_restore_preflight_ok_without_active_operation(self):
        self.assertEqual(self.service.restore_preflight(self.owner, self.ARCHIVE), ())

    def test_restore_preflight_reports_active_backup(self):
        self.backup.running = True   # self.backup = port de SÉCURITÉ depuis la V6.5
        issues = self.service.restore_preflight(self.owner, self.ARCHIVE)
        self.assertIn("sauvegarde de sécurité déjà en cours", issues)

    def test_restore_preflight_rejects_archive_without_verdict(self):
        self.archive_checks.checks.clear()

        issues = self.service.restore_preflight(self.owner, self.ARCHIVE)

        self.assertIn("archive pas encore vérifiée", issues[0])
        with self.assertRaises(RestoreUnavailable):
            self.service.restore_backup(self.owner, self.ARCHIVE)
        self.assertEqual(self.backup.triggers, 0)

    def test_restore_preflight_rejects_corrupt_or_incomplete_archive(self):
        archive = self.backup_archives.list_archives()[0]
        for ok, restorable, detail, expected in (
            (False, False, "flux gzip tronqué", "archive illisible ou dangereuse"),
            (True, False, "level.dat absent", "archive non restaurable"),
        ):
            with self.subTest(expected=expected):
                self.archive_checks.checks[self.ARCHIVE] = ArchiveCheck(
                    filename=self.ARCHIVE,
                    ok=ok,
                    checked_at=FixedClock().now(),
                    size_bytes=archive.size_bytes,
                    detail=detail,
                    restorable=restorable,
                    archive_created_at=archive.created_at,
                )
                issues = self.service.restore_preflight(self.owner, self.ARCHIVE)
                self.assertTrue(any(expected in issue for issue in issues))

    def test_restore_preflight_rejects_file_changed_since_check(self):
        archive = self.backup_archives._archives[0]
        self.backup_archives._archives[0] = BackupArchive(
            filename=archive.filename,
            size_bytes=archive.size_bytes + 1,
            created_at=archive.created_at,
        )

        issues = self.service.restore_preflight(self.owner, self.ARCHIVE)

        self.assertTrue(any("archive modifiée" in issue for issue in issues))

    def test_restore_rejected_when_backup_is_running(self):
        self.backup.running = True
        with self.assertRaises(RestoreUnavailable):
            self.service.restore_backup(self.owner, self.ARCHIVE)
        self.assertEqual(self.backup.triggers, 0)
        self.assertEqual(self.restore.calls, [])
        self.assertIn("phase=restore_rejected", self.audit.entries[-1].detail)

    def test_owner_restore_waits_for_security_backup(self):
        # V5.x : mc-restore ne démarre PLUS immédiatement — la sauvegarde de
        # sécurité doit finir d'abord (archive corrompue sinon, constaté en réel).
        outcome = self.service.restore_backup(self.owner, self.ARCHIVE)
        self.assertEqual(outcome, "pending")
        self.assertEqual(self.backup.triggers, 1)
        self.assertEqual(self.restore.calls, [])                    # rien d'écrasé encore
        pending = self.pending_restore.get()
        self.assertEqual((pending.filename, pending.requested_by), (self.ARCHIVE, "jeremy"))
        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome), ("BACKUP_RESTORE", "allowed"))
        self.assertIn("phase=restore_pending", entry.detail)

    def test_restore_audit_events_share_operation_id(self):
        self.service.restore_backup(self.owner, self.ARCHIVE)
        operation_ids = set()
        for entry in self.audit.entries:
            if entry.action != "BACKUP_RESTORE" or "operation_id=" not in entry.detail:
                continue
            operation_ids.add(entry.detail.split("operation_id=", 1)[1].split()[0])
        self.assertEqual(len(operation_ids), 1)

    def test_restore_uses_dedicated_safety_backup(self):
        safety = FakeBackup()
        self.service._restore_safety_backup = safety

        outcome = self.service.restore_backup(self.owner, self.ARCHIVE)

        self.assertEqual(outcome, "pending")
        self.assertEqual(safety.triggers, 1)
        self.assertEqual(self.backup.triggers, 0)

    def test_restore_notifications_are_tagged_for_ui_toggle(self):
        # Le filtrage vit dans le notifier (interrupteur « restore » de l'UI) ;
        # le service TAGUE chaque notification de restauration.
        self.service.restore_backup(self.owner, self.ARCHIVE)
        self.assertTrue(self.notifier.sent)
        self.assertTrue(all(evt == "restore" for _, _, _, evt in self.notifier.sent))

    def test_restore_progress_idle_without_pending_restore(self):
        progress = self.service.restore_progress(self.owner)
        self.assertIsNone(progress.pending)
        self.assertEqual(progress.phase, "idle")
        self.assertIsNone(progress.progress_percent)

    def test_restore_progress_estimates_security_backup(self):
        self.pending_restore.set(
            PendingRestore(
                filename=self.ARCHIVE,
                requested_by="jeremy",
                deadline=datetime(2026, 7, 1, 12, 15, tzinfo=timezone.utc),
            )
        )
        self.backup.running = True
        self.backup_archives._archives = [
            BackupArchive(
                "restore-safety/safety-before.tar.gz",
                1_000,
                datetime(2026, 7, 1, 11, 55, tzinfo=timezone.utc),
            ),
            BackupArchive(
                "restore-safety/safety-current.tar.gz",
                250,
                datetime(2026, 7, 1, 12, 1, tzinfo=timezone.utc),
            ),
        ]

        progress = self.service.restore_progress(self.owner)

        self.assertEqual(progress.phase, "backup_running")
        self.assertTrue(progress.backup_running)
        self.assertEqual(progress.progress_percent, 25)
        self.assertEqual(progress.security_archive.filename, "restore-safety/safety-current.tar.gz")

    def test_restore_progress_reports_restore_phase_after_backup(self):
        self.pending_restore.set(
            PendingRestore(
                filename=self.ARCHIVE,
                requested_by="jeremy",
                deadline=datetime(2026, 7, 1, 12, 15, tzinfo=timezone.utc),
            )
        )
        self.backup.running = False

        progress = self.service.restore_progress(self.owner)

        self.assertEqual(progress.phase, "restore_pending")
        self.assertFalse(progress.backup_running)
        self.assertEqual(progress.progress_percent, 100)

    def test_tick_starts_restore_once_backup_done(self):
        self.service.restore_backup(self.owner, self.ARCHIVE)
        self.backup.running = True
        self.assertIsNone(self.service.tick_pending_restore(self.owner))
        self.assertEqual(self.restore.calls, [])                    # sauvegarde en cours : attendre
        self.backup.running = False
        self.assertEqual(self.service.tick_pending_restore(self.owner), "restored")
        self.assertEqual(self.restore.calls, [self.ARCHIVE])
        self.assertIsNone(self.pending_restore.get())
        self.assertIn("phase=restore_started", self.audit.entries[-1].detail)
        # V7b : la consigne « restauration » du portier est amorcée AVANT le
        # lancement du worker (qui prendra le poste), sans le démarrer ici.
        self.assertEqual(len(self.doorman.primed), 1)
        self.assertIn("Restauration", self.doorman.primed[0][0])
        self.assertFalse(self.doorman.running)

    def test_doorman_priming_failure_never_blocks_restore(self):
        # Un portier indisponible ne doit jamais empêcher une restauration :
        # le priming est best-effort.
        self.doorman.prime = lambda motd, kick: (_ for _ in ()).throw(RuntimeError("KO"))
        self.service.restore_backup(self.owner, self.ARCHIVE)
        self.backup.running = False
        self.assertEqual(self.service.tick_pending_restore(self.owner), "restored")
        self.assertEqual(self.restore.calls, [self.ARCHIVE])

    def test_tick_aborts_when_security_backup_exits_with_error(self):
        self.service.restore_backup(self.owner, self.ARCHIVE)
        self.backup.exit_code_value = 12

        self.assertEqual(self.service.tick_pending_restore(self.owner), "abandoned")
        self.assertEqual(self.restore.calls, [])
        self.assertIsNone(self.pending_restore.get())
        self.assertIn("phase=security_backup_failed", self.audit.entries[-1].detail)
        self.assertIn("code de sortie 12", self.audit.entries[-1].detail)

    def test_tick_aborts_when_no_new_security_archive_exists(self):
        self.backup.on_trigger = None
        self.service.restore_backup(self.owner, self.ARCHIVE)

        self.assertEqual(self.service.tick_pending_restore(self.owner), "abandoned")
        self.assertEqual(self.restore.calls, [])
        self.assertEqual(self.service.restore_progress(self.owner).phase, "failed")
        self.assertIn("sans produire", self.service.restore_progress(self.owner).message)

    def test_tick_aborts_when_security_archive_is_invalid(self):
        self.service.restore_backup(self.owner, self.ARCHIVE)
        safety = next(
            archive
            for archive in self.backup_archives.list_archives()
            if archive.filename.startswith("restore-safety/")
        )
        self.archive_validator.results[safety.filename] = ArchiveCheck(
            filename=safety.filename,
            ok=False,
            checked_at=FixedClock().now(),
            size_bytes=safety.size_bytes,
            detail="fin de flux gzip inattendue",
            restorable=False,
            archive_created_at=safety.created_at,
        )

        self.assertEqual(self.service.tick_pending_restore(self.owner), "abandoned")
        self.assertEqual(self.restore.calls, [])
        self.assertIn("sauvegarde de sécurité illisible", self.service.restore_progress(self.owner).message)

    def test_tick_rechecks_target_after_security_backup(self):
        self.service.restore_backup(self.owner, self.ARCHIVE)
        target = self.backup_archives._archives[0]
        self.backup_archives._archives[0] = BackupArchive(
            filename=target.filename,
            size_bytes=target.size_bytes + 1,
            created_at=target.created_at,
        )

        self.assertEqual(self.service.tick_pending_restore(self.owner), "abandoned")
        self.assertEqual(self.restore.calls, [])
        self.assertIn("n'est plus utilisable", self.service.restore_progress(self.owner).message)

    def test_restore_operation_waits_while_restore_container_runs(self):
        self.service.restore_backup(self.owner, self.ARCHIVE)
        self.restore.running = True
        self.backup.running = False

        self.assertEqual(self.service.tick_pending_restore(self.owner), "restored")
        self.assertEqual(self.service.restore_progress(self.owner).phase, "restore_running")
        self.assertIsNone(self.service.tick_pending_restore(self.owner))
        self.assertEqual(self.service.restore_progress(self.owner).phase, "restore_running")

    def test_restore_worker_has_its_own_execution_deadline(self):
        clock = MutableClock()
        self.service._clock = clock
        self.service.restore_backup(self.owner, self.ARCHIVE)
        self.restore.running = True
        self.service.tick_pending_restore(self.owner)

        self.assertEqual(
            self.service._restore_operation.deadline,
            clock.now() + timedelta(minutes=30),
        )
        clock.moment += timedelta(minutes=31)
        self.assertEqual(self.service.tick_pending_restore(self.owner), "failed")
        self.assertIn(
            "conteneur de restauration",
            self.service.restore_progress(self.owner).message,
        )

    def test_health_deadline_starts_after_worker_finishes(self):
        clock = MutableClock()
        self.service._clock = clock
        self.service.restore_backup(self.owner, self.ARCHIVE)
        self.restore.running = True
        self.service.tick_pending_restore(self.owner)
        clock.moment += timedelta(minutes=20)
        self.restore.running = False
        self.container.health = "starting"

        self.service.tick_pending_restore(self.owner)

        operation = self.service._restore_operation
        self.assertEqual(operation.phase, "waiting_minecraft")
        self.assertEqual(operation.deadline, clock.now() + timedelta(minutes=5))

    def test_restore_operation_waits_for_minecraft_health_then_succeeds(self):
        self.service.restore_backup(self.owner, self.ARCHIVE)
        self.backup.running = False
        self.container.health = "starting"

        self.service.tick_pending_restore(self.owner)
        self.service.tick_pending_restore(self.owner)
        self.assertEqual(self.service.restore_progress(self.owner).phase, "waiting_minecraft")

        self.container.health = "healthy"
        self.assertEqual(self.service.tick_pending_restore(self.owner), "success")
        progress = self.service.restore_progress(self.owner)
        self.assertEqual(progress.phase, "success")
        self.assertEqual(progress.level, "success")
        phases = [e.detail.split()[0] for e in self.audit.entries if e.action == "BACKUP_RESTORE"]
        self.assertIn("phase=restore_done", phases)
        self.assertIn("phase=minecraft_healthy", phases)
        self.assertIn("phase=restore_completed", phases)

    def test_restore_container_error_does_not_wait_for_minecraft(self):
        self.service.restore_backup(self.owner, self.ARCHIVE)
        self.restore.exit_code_value = 9

        self.assertEqual(self.service.tick_pending_restore(self.owner), "restored")
        self.assertEqual(self.service.tick_pending_restore(self.owner), "failed")
        progress = self.service.restore_progress(self.owner)
        self.assertEqual(progress.phase, "failed")
        self.assertIn("code de sortie 9", progress.message)
        self.assertFalse(any(
            "phase=minecraft_restarting" in entry.detail for entry in self.audit.entries
        ))

    def test_restore_operation_waits_for_rcon_even_when_health_is_ok(self):
        self.service.restore_backup(self.owner, self.ARCHIVE)
        self.backup.running = False
        self.container.health = "healthy"
        self.game._available = False

        self.service.tick_pending_restore(self.owner)
        self.service.tick_pending_restore(self.owner)
        self.assertEqual(self.service.restore_progress(self.owner).phase, "waiting_minecraft")

        self.game._available = True
        self.assertEqual(self.service.tick_pending_restore(self.owner), "success")

    def test_restore_operation_fails_after_health_timeout(self):
        self.service._restore_operation = RestoreOperation(
            filename=self.ARCHIVE,
            requested_by="jeremy",
            phase="waiting_minecraft",
            started_at=datetime(2026, 7, 1, 11, 50, tzinfo=timezone.utc),
            deadline=datetime(2026, 7, 1, 11, 59, tzinfo=timezone.utc),
            message="attente",
        )

        self.assertEqual(self.service.tick_pending_restore(self.owner), "failed")
        progress = self.service.restore_progress(self.owner)
        self.assertEqual(progress.phase, "failed")
        self.assertEqual(progress.level, "error")
        self.assertIn("phase=restore_failed", self.audit.entries[-1].detail)

    def test_tick_abandons_after_deadline_without_touching_world(self):
        past = datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc)     # avant FixedClock (12:00)
        self.pending_restore.set(PendingRestore(filename=self.ARCHIVE, requested_by="jeremy", deadline=past))
        self.assertEqual(self.service.tick_pending_restore(self.owner), "abandoned")
        self.assertEqual(self.restore.calls, [])                    # le monde n'a PAS été touché
        self.assertIsNone(self.pending_restore.get())
        self.assertEqual(self.audit.entries[-1].outcome, "error")
        self.assertIn("Restauration abandonnée", [t for t, *_ in self.notifier.sent])

    def test_tick_waits_when_backup_state_unknown(self):
        # Proxy injoignable : ne jamais restaurer à l'aveugle — la deadline tranchera.
        self.service.restore_backup(self.owner, self.ARCHIVE)
        self.backup.status_fail = True
        self.assertIsNone(self.service.tick_pending_restore(self.owner))
        self.assertEqual(self.restore.calls, [])

    def test_backup_failure_aborts_without_touching_world(self):
        # Une restauration destructive ne continue jamais sans sauvegarde de sécurité.
        self.backup._fail = True
        with self.assertRaises(RestoreUnavailable):
            self.service.restore_backup(self.owner, self.ARCHIVE)
        self.assertEqual(self.restore.calls, [])
        self.assertTrue(any("phase=security_backup_failed" in e.detail for e in self.audit.entries))
        self.assertIn("Restauration annulée", [t for t, *_ in self.notifier.sent])

    def test_restore_requires_pending_coordinator(self):
        self.service._pending_restore = None

        with self.assertRaises(RestoreUnavailable):
            self.service.restore_backup(self.owner, self.ARCHIVE)

        self.assertEqual(self.backup.triggers, 0)
        self.assertEqual(self.restore.calls, [])

    def test_second_restore_rejected_while_pending(self):
        self.service.restore_backup(self.owner, self.ARCHIVE)
        with self.assertRaises(RestoreUnavailable):
            self.service.restore_backup(self.owner, self.ARCHIVE)
        self.assertEqual(self.audit.entries[-1].outcome, "error")

    def test_tick_restore_failure_is_audited_and_reraised(self):
        self.service.restore_backup(self.owner, self.ARCHIVE)
        self.restore._fail = True
        with self.assertRaises(RestoreUnavailable):
            self.service.tick_pending_restore(self.owner)
        self.assertEqual(self.audit.entries[-1].outcome, "error")
        self.assertIsNone(self.pending_restore.get())               # pas de re-tentative en boucle

    def test_admin_denied_and_audited(self):
        with self.assertRaises(PermissionDenied):
            self.service.restore_backup(self.admin, self.ARCHIVE)
        self.assertEqual(self.restore.calls, [])
        self.assertEqual(self.audit.entries[-1].outcome, "denied")
        with self.assertRaises(PermissionDenied):
            self.service.tick_pending_restore(self.admin)

    def test_unknown_archive_rejected_before_restore(self):
        with self.assertRaises(BackupArchiveNotFound):
            self.service.restore_backup(self.owner, "does-not-exist.tar.gz")
        self.assertEqual(self.restore.calls, [])
        self.assertEqual(self.backup.triggers, 0)  # validé avant toute action destructive

    def test_not_configured_raises(self):
        self.service._restore = None
        with self.assertRaises(RestoreUnavailable):
            self.service.restore_backup(self.owner, self.ARCHIVE)
        self.assertEqual(self.audit.entries[-1].outcome, "error")


class TestRetentionApply(ServiceTestBase):
    def _seed_archives(self):
        from datetime import datetime, timezone
        old_sched = BackupArchive(filename="scheduled/mc-auto-20250101-010000.tar.gz",
                                  size_bytes=700 * 1048576,
                                  created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        old_manual = BackupArchive(filename="manual/mc-manual-20250101-010000.tar.gz",
                                   size_bytes=700 * 1048576,
                                   created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        self.backup_archives._archives = [old_sched, old_manual]
        return old_sched, old_manual

    def test_apply_deletes_only_obsolete_scheduled(self):
        old_sched, old_manual = self._seed_archives()
        deleted = self.service.apply_backup_retention(self.owner, daily_days=30, monthly_months=12)
        self.assertEqual(deleted, 1)
        self.assertEqual(self.backup_archives.deleted, [old_sched.filename])
        remaining = [a.filename for a in self.backup_archives.list_archives()]
        self.assertIn(old_manual.filename, remaining)  # manuelle intouchable
        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome), ("BACKUP_RETENTION", "allowed"))
        self.assertIn("1 supprimée(s)", entry.detail)
        self.assertIn("700 Mio", entry.detail)

    def test_apply_with_nothing_to_delete_is_noop(self):
        deleted = self.service.apply_backup_retention(self.owner)
        self.assertEqual(deleted, 0)
        self.assertIn("0 supprimée(s)", self.audit.entries[-1].detail)

    def test_admin_denied(self):
        with self.assertRaises(PermissionDenied):
            self.service.apply_backup_retention(self.admin)
        self.assertEqual(self.backup_archives.deleted, [])

    def test_failed_deletion_is_audited_as_error(self):
        self._seed_archives()
        self.backup_archives.delete_archive = lambda f: False  # FS refuse
        self.service.apply_backup_retention(self.owner, daily_days=30, monthly_months=12)
        entry = self.audit.entries[-1]
        self.assertEqual(entry.outcome, "error")
        self.assertIn("échecs", entry.detail)


class TestBackupProfileDaily(unittest.TestCase):
    """Échéance quotidienne des profils : horloge AVANÇABLE requise."""

    def setUp(self):
        roles = build_roles(ROLE_SPEC)
        self.users = build_users(USER_SPEC, roles)
        self.audit = RecordingAudit()
        self.clock = MutableClock()
        from domain.model import BackupProfile
        from tests.fakes import FakeBackupProfiles, FakeProfileBackup
        self.profiles = FakeBackupProfiles([
            BackupProfile(id="auto", name="Automatiques", include=("all",), dest="scheduled")])
        self.profile_backup = FakeProfileBackup()
        self.service = AdminService(
            game=FakeGame(),
            container=FakeContainer(),
            logs=FakeLogs(),
            audit=self.audit,
            clock=self.clock,
            container_name=CONTAINER_NAME,
            metrics=FakeMetrics(),
            updater=FakeUpdater(),
            ops=FakeOps(),
            notifications=FakeNotifier(),
            player_history=FakePlayerHistory(),
            backup_archives=FakeBackupArchives(),
            bans=FakeBans(),
            temp_bans=FakeTempBans(),
            restart_schedule=InMemoryRestartSchedule(),
            backup_profiles=self.profiles,
            profile_backup=self.profile_backup,
        )
        from scheduler import SYSTEM_USER
        self.system_user = SYSTEM_USER

    def _local(self):
        return self.clock.now().astimezone()

    def _hhmm_in(self, minutes: int) -> str:
        from datetime import timedelta
        return (self._local() + timedelta(minutes=minutes)).strftime("%H:%M")

    def _set_daily(self, daily_at: str) -> None:
        import dataclasses
        self.profiles.upsert(dataclasses.replace(self.profiles.get("auto"), daily_at=daily_at))

    def test_waits_before_the_daily_time(self):
        self._set_daily(self._hhmm_in(30))
        self.service.tick_backup_profiles(self.system_user)
        self.assertEqual(self.profile_backup.calls, [])

    def test_fires_once_after_the_time_then_not_again_same_day(self):
        from datetime import timedelta
        self._set_daily(self._hhmm_in(2))
        self.clock.moment += timedelta(minutes=5)  # heure passée
        self.service.tick_backup_profiles(self.system_user)
        self.assertEqual(len(self.profile_backup.calls), 1)
        self.assertEqual(self.audit.entries[-1].username, "scheduler")
        self.clock.moment += timedelta(hours=2)    # même jour : pas de doublon
        self.service.tick_backup_profiles(self.system_user)
        self.assertEqual(len(self.profile_backup.calls), 1)
        self.clock.moment += timedelta(days=1)     # lendemain : repart
        self.service.tick_backup_profiles(self.system_user)
        self.assertEqual(len(self.profile_backup.calls), 2)

    def test_missed_time_is_caught_up_same_day(self):
        # mc-admin « éteint » à l'heure dite : premier tick 3 h après.
        self._set_daily(self._hhmm_in(-180) if self._local().hour >= 3 else "00:00")
        self.service.tick_backup_profiles(self.system_user)
        self.assertEqual(len(self.profile_backup.calls), 1)  # rattrapée, pas sautée


class TestStart(ServiceTestBase):
    def test_admin_can_start_and_it_is_audited(self):
        self.service.start(self.admin)
        self.assertEqual(self.container.starts, 1)
        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome), ("START", "allowed"))

    def test_friend_cannot_start(self):
        with self.assertRaises(PermissionDenied):
            self.service.start(self.friend)
        self.assertEqual(self.container.starts, 0)
        self.assertEqual(self.audit.entries[-1].outcome, "denied")

    def test_start_with_pending_level_uses_worker(self):
        self.pending_op_levels.entries["trappeur"] = PendingOpLevel("Trappeur", 2)
        self.service.start(self.owner)
        self.assertEqual(self.op_levels_apply.calls, 1)
        self.assertEqual(self.container.starts, 0)
        self.assertIn("phase=op_levels_started", self.audit.entries[-1].detail)


class TestPrivilegedActions(ServiceTestBase):
    def test_only_owner_runs_raw_rcon(self):
        out = self.service.run_rcon(self.owner, "whitelist add toto")
        self.assertEqual(out, "ran: whitelist add toto")
        self.assertEqual(self.game.raw_calls, ["whitelist add toto"])

    def test_admin_cannot_run_raw_rcon(self):
        with self.assertRaises(PermissionDenied):
            self.service.run_rcon(self.admin, "op paul")
        self.assertEqual(self.game.raw_calls, [])  # commande jamais exécutée

    def test_owner_can_access_console(self):
        self.service.can_access_console(self.owner)  # ne lève pas
        self.assertEqual(self.audit.entries, [])  # accès (lecture) non audité

    def test_admin_cannot_access_console_and_it_is_audited(self):
        with self.assertRaises(PermissionDenied):
            self.service.can_access_console(self.admin)
        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome), ("RCON_RAW", "denied"))

    def test_only_owner_can_stop(self):
        self.service.stop(self.owner)
        self.assertEqual(self.container.stops, 1)

    def test_friend_cannot_stop(self):
        with self.assertRaises(PermissionDenied):
            self.service.stop(self.friend)
        self.assertEqual(self.container.stops, 0)


class TestNotifications(ServiceTestBase):
    """Contrat : notifiées sur échec (restart/start/stop/backup/update) et sur
    succès d'update ; jamais sur un simple refus RBAC (bruit, pas anomalie)."""

    def test_restart_failure_notifies_error(self):
        self.container._fail = True
        with self.assertRaises(RuntimeError):
            self.service.restart(self.owner)
        self.assertEqual(len(self.notifier.sent), 1)
        title, _, level, _ = self.notifier.sent[0]
        self.assertEqual((title, level), ("Redémarrage échoué", "error"))

    def test_start_failure_notifies_error(self):
        self.container._fail = True
        with self.assertRaises(RuntimeError):
            self.service.start(self.owner)
        self.assertEqual(self.notifier.sent[-1][::2], ("Démarrage échoué", "error"))

    def test_stop_failure_notifies_error(self):
        self.container._fail = True
        with self.assertRaises(RuntimeError):
            self.service.stop(self.owner)
        self.assertEqual(self.notifier.sent[-1][::2], ("Arrêt échoué", "error"))

    def test_update_success_notifies_info(self):
        self.game._players = []  # pas de joueurs -> pas de blocage
        self.service.apply_update(self.owner)
        self.assertEqual(self.notifier.sent[-1][::2], ("Mise à jour du serveur lancée", "info"))

    def test_update_failure_notifies_error(self):
        self.game._players = []
        self.updater._apply_fails = True
        with self.assertRaises(UpdateUnavailable):
            self.service.apply_update(self.owner)
        self.assertEqual(self.notifier.sent[-1][::2], ("Mise à jour échouée", "error"))

    def test_permission_denial_does_not_notify(self):
        with self.assertRaises(PermissionDenied):
            self.service.restart(self.friend)
        self.assertEqual(self.notifier.sent, [])

    def test_update_blocked_by_players_does_not_notify(self):
        with self.assertRaises(UpdateBlocked):
            self.service.apply_update(self.owner)  # joueurs connectés par défaut
        self.assertEqual(self.notifier.sent, [])


class TestMetrics(ServiceTestBase):
    def test_friend_with_status_can_read_metrics(self):
        readings = self.service.metrics_snapshot(self.friend)
        self.assertEqual([r.key for r in readings], ["players", "ram"])
        self.assertEqual(self.audit.entries, [])  # lecture non auditée

    def test_guest_denied_and_audited(self):
        with self.assertRaises(PermissionDenied):
            self.service.metrics_snapshot(self.guest)
        self.assertEqual(self.audit.entries[-1].outcome, "denied")


class TestUpdate(ServiceTestBase):
    def test_owner_reads_update_status(self):
        status = self.service.update_status(self.owner)
        self.assertEqual(status.current_version, "26.1")
        self.assertTrue(status.update_available)
        self.assertEqual(self.audit.entries, [])  # lecture non auditée

    def test_admin_denied(self):
        with self.assertRaises(PermissionDenied):
            self.service.update_status(self.admin)
        with self.assertRaises(PermissionDenied):
            self.service.apply_update(self.admin)
        self.assertEqual(self.updater.applied, 0)
        self.assertEqual([e.outcome for e in self.audit.entries], ["denied", "denied"])

    def test_blocked_when_players_online_without_force(self):
        # le fake a alice+bob connectés
        with self.assertRaises(UpdateBlocked):
            self.service.apply_update(self.owner)
        self.assertEqual(self.updater.applied, 0)
        entry = self.audit.entries[-1]
        self.assertEqual(entry.outcome, "denied")
        self.assertIn("2 joueur(s)", entry.detail)

    def test_blocked_when_players_unknown_without_force(self):
        self.game._available = False  # RCON down -> joueurs inconnus
        with self.assertRaises(UpdateBlocked):
            self.service.apply_update(self.owner)
        self.assertEqual(self.updater.applied, 0)
        self.assertIn("inconnu", self.audit.entries[-1].detail)

    def test_force_runs_say_save_then_updater_and_audits_steps(self):
        self.service.apply_update(self.owner, force=True)
        self.assertEqual(len(self.game.said), 1)          # avertissement in-game
        self.assertEqual(self.game.saved, 1)              # save-all avant
        self.assertEqual(self.updater.applied, 1)
        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome), ("UPDATE", "allowed"))
        self.assertEqual(entry.detail, "say; save-all; mc-updater démarré")

    def test_no_players_proceeds_without_force(self):
        self.game._players = []
        self.service.apply_update(self.owner)
        self.assertEqual(self.updater.applied, 1)

    def test_rcon_down_with_force_skips_say_save_but_updates(self):
        self.game._available = False
        self.service.apply_update(self.owner, force=True)
        self.assertEqual(self.updater.applied, 1)
        self.assertIn("RCON indisponible", self.audit.entries[-1].detail)

    def test_updater_failure_is_audited_and_reraised(self):
        self.game._players = []
        self.updater._apply_fails = True
        with self.assertRaises(UpdateUnavailable):
            self.service.apply_update(self.owner)
        self.assertEqual(self.audit.entries[-1].outcome, "error")


class TestWhitelist(ServiceTestBase):
    def test_owner_can_list(self):
        self.game.whitelist_names = ["alice"]
        self.assertEqual(self.service.whitelist(self.owner), ["alice"])
        self.assertEqual(self.audit.entries, [])  # lecture non auditée

    def test_admin_denied_and_audited(self):
        with self.assertRaises(PermissionDenied):
            self.service.whitelist(self.admin)
        self.assertEqual(self.audit.entries[-1].outcome, "denied")
        self.assertEqual(self.audit.entries[-1].action, "WHITELIST_MANAGE")

    def test_owner_add_is_audited_with_player_name(self):
        out = self.service.whitelist_add(self.owner, "toto_64")
        self.assertIn("toto_64", out)
        self.assertEqual(self.game.whitelist_calls, [("add", "toto_64")])
        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome, entry.detail),
                         ("WHITELIST_MANAGE", "allowed", "add toto_64"))

    def test_owner_remove_is_audited(self):
        self.game.whitelist_names = ["toto"]
        self.service.whitelist_remove(self.owner, "toto")
        self.assertEqual(self.game.whitelist_calls, [("remove", "toto")])
        self.assertEqual(self.audit.entries[-1].detail, "remove toto")

    def test_invalid_names_rejected_before_rcon(self):
        for bad in ["ab", "x" * 17, "bad name", "a;op me", "héllo", "", "a\nb"]:
            with self.subTest(name=bad):
                with self.assertRaises(InvalidPlayerName):
                    self.service.whitelist_add(self.owner, bad)
        self.assertEqual(self.game.whitelist_calls, [])  # RCON jamais sollicité

    def test_friend_cannot_add(self):
        with self.assertRaises(PermissionDenied):
            self.service.whitelist_add(self.friend, "toto")
        self.assertEqual(self.game.whitelist_calls, [])


class TestOps(ServiceTestBase):
    def test_owner_can_list(self):
        ops = self.service.ops(self.owner)
        self.assertEqual([(o.name, o.level) for o in ops], [("Trappeur", 4)])
        self.assertEqual(self.audit.entries, [])  # lecture non auditée

    def test_admin_denied_and_audited(self):
        with self.assertRaises(PermissionDenied):
            self.service.ops(self.admin)
        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome), ("OP_MANAGE", "denied"))

    def test_owner_op_add_is_audited(self):
        out = self.service.op_add(self.owner, "toto_64")
        self.assertIn("toto_64", out)
        self.assertEqual(self.game.op_calls, [("op", "toto_64")])
        self.assertEqual(self.pending_op_levels.set_calls, [])
        self.assertEqual(self.pending_op_levels.remove_calls, ["toto_64"])
        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome), ("OP_MANAGE", "allowed"))
        self.assertIn("op toto_64 niveau 4", entry.detail)
        self.assertIn("effectif immédiatement", entry.detail)

    def test_owner_op_add_with_custom_level(self):
        self.service.op_add(self.owner, "toto_64", level=2)
        self.assertEqual(self.pending_op_levels.set_calls, [("toto_64", 2)])
        self.assertIn("niveau 2", self.audit.entries[-1].detail)
        self.assertIn("prochain redémarrage", self.audit.entries[-1].detail)

    def test_existing_op_level_change_is_queued(self):
        self.service.op_add(self.owner, "Trappeur", level=2)
        self.assertEqual(self.pending_op_levels.set_calls, [("Trappeur", 2)])

    def test_existing_level_cancels_an_older_pending_change(self):
        self.pending_op_levels.entries["trappeur"] = PendingOpLevel("Trappeur", 2)
        self.service.op_add(self.owner, "Trappeur", level=4)
        self.assertEqual(self.pending_op_levels.remove_calls, ["Trappeur"])
        self.assertEqual(self.service.pending_op_levels(self.owner), [])

    def test_invalid_level_rejected_before_rcon(self):
        for bad_level in (0, 5, -1):
            with self.subTest(level=bad_level):
                with self.assertRaises(InvalidOpLevel):
                    self.service.op_add(self.owner, "toto_64", level=bad_level)
        self.assertEqual(self.game.op_calls, [])

    def test_queue_failure_reports_partial_success(self):
        self.pending_op_levels.fail = True
        with self.assertRaises(OpLevelsUnavailable):
            self.service.op_add(self.owner, "toto_64", level=3)
        self.assertEqual(self.game.op_calls, [("op", "toto_64")])
        self.assertEqual(self.audit.entries[-1].outcome, "error")
        self.assertIn("op toto_64 réussi", self.audit.entries[-1].detail)

    def test_owner_op_remove_is_audited(self):
        self.service.op_remove(self.owner, "Trappeur")
        self.assertEqual(self.game.op_calls, [("deop", "Trappeur")])
        self.assertEqual(self.pending_op_levels.remove_calls, ["Trappeur"])
        self.assertEqual(self.audit.entries[-1].detail, "deop Trappeur")

    def test_pending_levels_are_owner_only(self):
        self.pending_op_levels.entries["trappeur"] = PendingOpLevel("Trappeur", 2)
        self.assertEqual(
            self.service.pending_op_levels(self.owner),
            [PendingOpLevel("Trappeur", 2)],
        )
        with self.assertRaises(PermissionDenied):
            self.service.pending_op_levels(self.admin)

    def test_op_mutations_are_blocked_while_levels_are_being_applied(self):
        self.pending_op_levels.entries["trappeur"] = PendingOpLevel("Trappeur", 2)
        self.service.restart(self.owner)

        with self.assertRaises(OpLevelsUnavailable):
            self.service.op_add(self.owner, "toto_64", level=3)
        with self.assertRaises(OpLevelsUnavailable):
            self.service.op_remove(self.owner, "Trappeur")

        self.assertEqual(self.game.op_calls, [])
        self.assertEqual(self.audit.entries[-1].outcome, "error")

    def test_invalid_name_rejected_before_rcon(self):
        with self.assertRaises(InvalidPlayerName):
            self.service.op_add(self.owner, "bad name!")
        self.assertEqual(self.game.op_calls, [])

    def test_admin_cannot_op_add(self):
        with self.assertRaises(PermissionDenied):
            self.service.op_add(self.admin, "toto")
        self.assertEqual(self.game.op_calls, [])

    def test_ops_list_unavailable_propagates(self):
        self.ops._available = False
        with self.assertRaises(ServerUnavailable):
            self.service.ops(self.owner)


class TestKick(ServiceTestBase):
    def test_admin_can_kick_and_it_is_audited(self):
        out = self.service.kick(self.admin, "toto_64", "AFK trop longtemps")
        self.assertIn("toto_64", out)
        self.assertEqual(self.game.kick_calls, [("toto_64", "AFK trop longtemps")])
        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome), ("KICK", "allowed"))
        self.assertIn("AFK trop longtemps", entry.detail)

    def test_kick_without_reason(self):
        self.service.kick(self.admin, "toto_64")
        self.assertEqual(self.game.kick_calls, [("toto_64", "")])

    def test_friend_cannot_kick(self):
        with self.assertRaises(PermissionDenied):
            self.service.kick(self.friend, "toto_64")
        self.assertEqual(self.game.kick_calls, [])

    def test_invalid_name_rejected_before_rcon(self):
        with self.assertRaises(InvalidPlayerName):
            self.service.kick(self.admin, "bad name!")
        self.assertEqual(self.game.kick_calls, [])

    def test_reason_sanitized_before_rcon(self):
        self.service.kick(self.admin, "toto_64", "ligne1\nligne2   avec   espaces")
        self.assertEqual(self.game.kick_calls, [("toto_64", "ligne1 ligne2 avec espaces")])


class TestBans(ServiceTestBase):
    def test_owner_can_list(self):
        entries = self.service.bans(self.owner)
        self.assertEqual([e.player for e in entries], ["griefer"])
        self.assertEqual(self.audit.entries, [])  # lecture non auditée

    def test_admin_cannot_list_and_it_is_audited(self):
        with self.assertRaises(PermissionDenied):
            self.service.bans(self.admin)
        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome), ("BAN_MANAGE", "denied"))

    def test_owner_ban_is_audited(self):
        out = self.service.ban(self.owner, "toto_64", "Triche")
        self.assertIn("toto_64", out)
        self.assertEqual(self.game.ban_calls, [("ban", "toto_64", "Triche")])
        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome), ("BAN_MANAGE", "allowed"))
        self.assertIn("Triche", entry.detail)

    def test_owner_pardon_is_audited(self):
        out = self.service.pardon(self.owner, "griefer")
        self.assertIn("griefer", out)
        self.assertEqual(self.game.ban_calls, [("pardon", "griefer", "")])
        self.assertEqual(self.audit.entries[-1].detail, "pardon griefer")

    def test_admin_cannot_ban(self):
        with self.assertRaises(PermissionDenied):
            self.service.ban(self.admin, "toto_64")
        self.assertEqual(self.game.ban_calls, [])

    def test_invalid_name_rejected_before_rcon(self):
        with self.assertRaises(InvalidPlayerName):
            self.service.ban(self.owner, "bad name!")
        self.assertEqual(self.game.ban_calls, [])

    def test_bans_list_unavailable_propagates(self):
        self.bans._available = False
        with self.assertRaises(ServerUnavailable):
            self.service.bans(self.owner)


class TestTemporaryBans(ServiceTestBase):
    def test_owner_ban_temporary_schedules_unban(self):
        out = self.service.ban_temporary(self.owner, "toto_64", 24, "Triche")
        self.assertIn("toto_64", out)
        self.assertEqual(self.game.ban_calls, [("ban", "toto_64", "Triche")])
        pending = self.temp_bans.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].player, "toto_64")
        self.assertEqual(pending[0].unban_at, FixedClock().now() + timedelta(hours=24))
        entry = self.audit.entries[-1]
        self.assertEqual((entry.action, entry.outcome), ("BAN_MANAGE", "allowed"))
        self.assertIn("24h", entry.detail)

    def test_admin_cannot_ban_temporary(self):
        with self.assertRaises(PermissionDenied):
            self.service.ban_temporary(self.admin, "toto_64", 24)
        self.assertEqual(self.game.ban_calls, [])
        self.assertEqual(self.temp_bans.pending(), [])

    def test_invalid_duration_rejected_before_rcon(self):
        for bad_hours in (0, -5):
            with self.subTest(hours=bad_hours):
                with self.assertRaises(InvalidDuration):
                    self.service.ban_temporary(self.owner, "toto_64", bad_hours)
        self.assertEqual(self.game.ban_calls, [])

    def test_invalid_name_rejected_before_rcon(self):
        with self.assertRaises(InvalidPlayerName):
            self.service.ban_temporary(self.owner, "bad name!", 24)
        self.assertEqual(self.game.ban_calls, [])

    def test_rescheduling_replaces_previous_timer(self):
        self.service.ban_temporary(self.owner, "toto_64", 24)
        self.service.ban_temporary(self.owner, "toto_64", 48)
        pending = self.temp_bans.pending()
        self.assertEqual(len(pending), 1)  # pas deux minuteries en parallèle
        self.assertEqual(pending[0].unban_at, FixedClock().now() + timedelta(hours=48))

    def test_permanent_ban_cancels_pending_timer(self):
        self.service.ban_temporary(self.owner, "toto_64", 24)
        self.service.ban(self.owner, "toto_64")  # re-ban permanent
        self.assertEqual(self.temp_bans.pending(), [])

    def test_manual_pardon_cancels_pending_timer(self):
        self.service.ban_temporary(self.owner, "toto_64", 24)
        self.service.pardon(self.owner, "toto_64")
        self.assertEqual(self.temp_bans.pending(), [])

    def test_owner_can_view_pending(self):
        self.service.ban_temporary(self.owner, "toto_64", 24)
        pending = self.service.pending_unbans(self.owner)
        self.assertEqual([p.player for p in pending], ["toto_64"])
        # lecture non auditée (seule l'entrée "allowed" du ban_temporary existe)
        self.assertEqual(len(self.audit.entries), 1)

    def test_admin_cannot_view_pending(self):
        with self.assertRaises(PermissionDenied):
            self.service.pending_unbans(self.admin)


class TestPlayerHistory(ServiceTestBase):
    def test_friend_with_status_can_read(self):
        summary = self.service.player_history(self.friend)
        self.assertEqual([s.player for s in summary], ["alice"])
        self.assertEqual(self.audit.entries, [])  # lecture non auditée

    def test_guest_denied_and_audited(self):
        with self.assertRaises(PermissionDenied):
            self.service.player_history(self.guest)
        self.assertEqual(self.audit.entries[-1].outcome, "denied")
        self.assertEqual(self.audit.entries[-1].action, "STATUS")


class TestGameSettings(ServiceTestBase):
    def test_owner_reads_settings(self):
        settings = self.service.game_settings(self.owner)
        self.assertEqual(settings.difficulty, "normal")
        self.assertIs(settings.keep_inventory, False)
        self.assertIs(settings.mob_griefing, True)
        self.assertEqual(self.audit.entries, [])  # lecture non auditée

    def test_mutations_whitelisted_and_audited(self):
        from domain.errors import InvalidGameValue
        self.service.set_difficulty(self.owner, "hard")
        self.service.set_gamerule(self.owner, "keep_inventory", True)
        self.service.set_weather(self.owner, "clear")
        self.service.set_time(self.owner, "day")
        self.assertEqual(self.game.game_calls, [
            ("difficulty", "hard"), ("gamerule", "keep_inventory", True),
            ("weather", "clear"), ("time", "day"),
        ])
        self.assertEqual([e.outcome for e in self.audit.entries], ["allowed"] * 4)
        self.assertIn("difficulté -> hard", self.audit.entries[0].detail)
        # Hors liste blanche : refusé AVANT tout envoi RCON.
        for call in (
            lambda: self.service.set_difficulty(self.owner, "extreme"),
            lambda: self.service.set_gamerule(self.owner, "fire_tick", True),
            lambda: self.service.set_weather(self.owner, "tornado"),
            lambda: self.service.set_time(self.owner, "13:37"),
        ):
            with self.assertRaises(InvalidGameValue):
                call()
        self.assertEqual(len(self.game.game_calls), 4)  # rien de plus n'est parti

    def test_admin_denied(self):
        with self.assertRaises(PermissionDenied):
            self.service.set_difficulty(self.admin, "hard")
        self.assertEqual(self.game.game_calls, [])

    def test_rcon_error_is_audited_and_reraised(self):
        self.game.set_weather = lambda kind: (_ for _ in ()).throw(ServerUnavailable("down"))
        with self.assertRaises(ServerUnavailable):
            self.service.set_weather(self.owner, "rain")
        self.assertEqual(self.audit.entries[-1].outcome, "error")


class TestPlayerDetail(ServiceTestBase):
    def test_returns_summary_sessions_and_stats_case_insensitive(self):
        summary, sessions, stats = self.service.player_detail(self.friend, "ALICE")
        self.assertIsNotNone(summary)
        self.assertEqual(summary.player, "alice")
        self.assertEqual(len(sessions), 1)  # session du fake
        self.assertEqual(stats.deaths, 14)  # stats vanilla jointes par pseudo

    def test_unknown_player_returns_none_and_empty(self):
        summary, sessions, stats = self.service.player_detail(self.friend, "inconnu")
        self.assertIsNone(summary)
        self.assertEqual(sessions, [])
        self.assertIsNone(stats)

    def test_stats_not_configured_degrades_to_none(self):
        self.service._player_stats = None
        _, _, stats = self.service.player_detail(self.friend, "alice")
        self.assertIsNone(stats)
        self.assertEqual(self.service.player_stats(self.friend), [])

    def test_invalid_name_rejected(self):
        with self.assertRaises(InvalidPlayerName):
            self.service.player_detail(self.friend, "bad name!")

    def test_guest_denied(self):
        with self.assertRaises(PermissionDenied):
            self.service.player_detail(self.guest, "alice")


class TestAuditTrail(ServiceTestBase):
    def test_owner_reads_entries_without_adding_one(self):
        self.service.restart(self.admin)  # produit 1 entrée "allowed"
        entries = self.service.audit_trail(self.owner)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].action, "RESTART")
        # la consultation n'a PAS ajouté d'entrée :
        self.assertEqual(len(self.audit.entries), 1)

    def test_friend_denied_and_audited(self):
        with self.assertRaises(PermissionDenied):
            self.service.audit_trail(self.friend)
        self.assertEqual(self.audit.entries[-1].outcome, "denied")
        self.assertEqual(self.audit.entries[-1].action, "AUDIT_VIEW")


class TestEvents(ServiceTestBase):
    def test_owner_gets_merged_sorted_timeline(self):
        # Génère une entrée d'audit (restart) ; le FakePlayerHistory fournit
        # une session alice (join 30/06 23:00, leave 01/07 00:00).
        self.service.restart(self.owner)
        events = self.service.events(self.owner)
        kinds = [ev.kind for ev in events]
        self.assertIn("audit", kinds)
        self.assertIn("join", kinds)
        self.assertIn("leave", kinds)
        stamps = [ev.timestamp for ev in events]
        self.assertEqual(stamps, sorted(stamps, reverse=True))  # plus récent en premier

    def test_open_session_has_no_leave_event(self):
        from domain.model import PlayerSession
        from datetime import datetime, timezone
        self.player_history._sessions = [
            PlayerSession(player="bob", joined_at=datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc), left_at=None),
        ]
        events = self.service.events(self.owner)
        bob_events = [ev for ev in events if "bob" in ev.title]
        self.assertEqual([ev.kind for ev in bob_events], ["join"])

    def test_admin_denied_and_audited(self):
        with self.assertRaises(PermissionDenied):
            self.service.events(self.admin)
        self.assertEqual(self.audit.entries[-1].outcome, "denied")


class TestPerformanceEvents(ServiceTestBase):
    """Marqueurs annotés des graphes de performance (UX-7) : sauvegardes,
    redémarrages, mises à jour DU SERVEUR — jamais l'auto-mise à jour de
    mc-admin, jamais un refus, jamais l'auteur ni le détail technique."""

    def _now(self):
        return FixedClock().now()

    def test_includes_backup_restart_and_server_update(self):
        self.service.restart(self.owner)
        self.audit.record(AuditEntry(
            timestamp=self._now(), username="backup-watch", role="automation",
            action="BACKUP_TRIGGER", target=CONTAINER_NAME, outcome="allowed",
            detail="phase=backup_done kind=scheduled archive=x.tar.gz size_mio=12",
        ))
        self.audit.record(AuditEntry(
            timestamp=self._now(), username="jeremy", role="owner",
            action="UPDATE", target=CONTAINER_NAME, outcome="allowed",
            detail="say; save-all; mc-updater démarré",
        ))
        events = self.service.performance_events(self.owner, self._now() - timedelta(hours=1))
        self.assertEqual(sorted(kind for _, kind in events), ["backup", "restart", "update"])

    def test_excludes_mc_admin_self_update(self):
        self.audit.record(AuditEntry(
            timestamp=self._now(), username="jeremy", role="owner",
            action="UPDATE", target=CONTAINER_NAME, outcome="allowed",
            detail="phase=app_update_applied version=0.10.0",
        ))
        events = self.service.performance_events(self.owner, self._now() - timedelta(hours=1))
        self.assertEqual(events, [])

    def test_excludes_denied_and_error_outcomes(self):
        self.audit.record(AuditEntry(
            timestamp=self._now(), username="jeremy", role="owner",
            action="RESTART", target=CONTAINER_NAME, outcome="denied", detail="",
        ))
        self.audit.record(AuditEntry(
            timestamp=self._now(), username="jeremy", role="owner",
            action="UPDATE", target=CONTAINER_NAME, outcome="error", detail="échec",
        ))
        events = self.service.performance_events(self.owner, self._now() - timedelta(hours=1))
        self.assertEqual(events, [])

    def test_excludes_events_before_since(self):
        self.service.restart(self.owner)
        events = self.service.performance_events(self.owner, self._now() + timedelta(seconds=1))
        self.assertEqual(events, [])

    def test_guest_denied_and_audited(self):
        with self.assertRaises(PermissionDenied):
            self.service.performance_events(self.guest, self._now())
        self.assertEqual(self.audit.entries[-1].outcome, "denied")
        self.assertEqual(self.audit.entries[-1].action, "STATUS")


class TestIncidents(ServiceTestBase):
    def _now(self):
        return FixedClock().now()

    def test_incident_correlates_actions_in_its_window(self):
        from domain.model import Incident
        now = self._now()
        self.incidents._recent = [Incident(
            id="server:1", subject="server", kind="availability", label="Serveur",
            started_at=now - timedelta(minutes=30),
            ended_at=now - timedelta(minutes=10),
        )]
        # Redémarrage PENDANT l'incident -> corrélé.
        self.audit.record(AuditEntry(
            timestamp=now - timedelta(minutes=20), username="jeremy", role="owner",
            action="RESTART", target=CONTAINER_NAME, outcome="allowed", detail="",
        ))
        # Sauvegarde AVANT l'incident -> hors fenêtre, non corrélée.
        self.audit.record(AuditEntry(
            timestamp=now - timedelta(minutes=50), username="backup-watch", role="automation",
            action="BACKUP_TRIGGER", target=CONTAINER_NAME, outcome="allowed",
            detail="phase=backup_done archive=x.tar.gz",
        ))
        result = self.service.incidents(self.owner)
        self.assertEqual(len(result), 1)
        incident, actions = result[0]
        self.assertEqual(incident.subject, "server")
        self.assertEqual([kind for _, kind in actions], ["restart"])

    def test_ongoing_incident_window_extends_to_now(self):
        from domain.model import Incident
        now = self._now()
        self.incidents._recent = [Incident(
            id="disk:1", subject="disk", kind="disk", label="Espace disque",
            started_at=now - timedelta(minutes=5), ended_at=None,
        )]
        self.audit.record(AuditEntry(
            timestamp=now - timedelta(minutes=2), username="jeremy", role="owner",
            action="BACKUP_RESTORE", target=CONTAINER_NAME, outcome="allowed",
            detail="phase=restore_started",
        ))
        _incident, actions = self.service.incidents(self.owner)[0]
        self.assertEqual([kind for _, kind in actions], ["restore"])

    def test_no_incident_store_returns_empty(self):
        self.incidents._recent = []
        self.assertEqual(self.service.incidents(self.owner), [])

    def test_guest_denied_and_audited(self):
        with self.assertRaises(PermissionDenied):
            self.service.incidents(self.guest)
        self.assertEqual(self.audit.entries[-1].outcome, "denied")


class TestLogs(ServiceTestBase):
    def test_friend_can_view_logs(self):
        lines = self.service.recent_logs(self.friend, lines=50)
        self.assertEqual(lines, ["[INFO] hello", "[INFO] world"])
        self.assertEqual(self.logs.calls, [50])

    def test_logs_denied_for_guest_is_audited(self):
        with self.assertRaises(PermissionDenied):
            self.service.recent_logs(self.guest)
        self.assertEqual(self.audit.entries[-1].outcome, "denied")
        self.assertEqual(self.audit.entries[-1].action, "LOGS_VIEW")


if __name__ == "__main__":
    unittest.main()


class TestBackupProfiles(ServiceTestBase):
    """Profils de sauvegarde (V6) : service reconstruit avec les ports profils."""

    def setUp(self):
        super().setUp()
        from domain.model import BackupProfile
        from tests.fakes import FakeBackupProfiles, FakeProfileBackup
        self.profiles = FakeBackupProfiles([
            BackupProfile(id="manual", name="Manuelles", include=("all",), dest="manual"),
            BackupProfile(id="auto", name="Automatiques", include=("all",), dest="scheduled",
                          daily_at="04:00", retention_daily_days=30, retention_monthly_months=12),
        ])
        self.profile_backup = FakeProfileBackup()
        self.service._backup_profiles = self.profiles
        self.service._profile_backup = self.profile_backup
        self.service._world_dir = "old_world_1"

    def test_create_profile_derives_slug_dest_and_audits(self):
        profile = self.service.create_backup_profile(
            self.owner, "Monde seul", ("world", "config"), hours=12,
            retention_daily_days=7, retention_monthly_months=3)
        self.assertEqual(profile.id, "monde-seul")
        self.assertEqual(profile.dest, "profiles/monde-seul")       # jamais un chemin libre
        self.assertEqual(profile.include, ("world", "config"))
        self.assertIn("phase=profile_created", self.audit.entries[-1].detail)

    def test_create_rejects_bad_values(self):
        with self.assertRaises(InvalidGameValue):
            self.service.create_backup_profile(self.owner, "X", ("nimporte",))
        with self.assertRaises(InvalidGameValue):
            self.service.create_backup_profile(self.owner, "X", ("world",), hours=7)
        with self.assertRaises(InvalidGameValue):
            self.service.create_backup_profile(self.owner, "X", ("world",), daily_at="25:99")
        with self.assertRaises(PermissionDenied):
            self.service.create_backup_profile(self.admin, "X", ("world",))

    def test_slug_collision_gets_suffix(self):
        self.service.create_backup_profile(self.owner, "Monde", ("world",))
        second = self.service.create_backup_profile(self.owner, "monde", ("world",))
        self.assertEqual(second.id, "monde-2")

    def test_trigger_profile_writes_consigne_from_closed_categories(self):
        self.service.create_backup_profile(self.owner, "Monde seul", ("world", "admin"))
        self.service.trigger_profile_backup(self.owner, "monde-seul")
        (call,) = self.profile_backup.calls
        self.assertEqual(call["dest"], "profiles/monde-seul")
        self.assertEqual(call["name"], "mc-monde-seul")
        self.assertIn("old_world_1", call["includes"])              # monde résolu dynamiquement
        self.assertIn("whitelist.json", call["includes"])
        self.assertEqual(call["excludes"], "cache,logs")            # cache/logs seuls écartés
        self.assertIn("phase=backup_started", self.audit.entries[-1].detail)

    def test_mods_category_includes_jars(self):
        self.service.create_backup_profile(self.owner, "Mods complets", ("mods",))
        self.service.trigger_profile_backup(self.owner, "mods-complets")
        (call,) = self.profile_backup.calls
        self.assertEqual(call["includes"], "mods")
        self.assertNotIn("*.jar", call["excludes"])                  # les mods sont des .jar

    def test_tick_runs_due_profiles_only(self):
        from datetime import datetime, timezone
        from domain.model import BackupProfile
        self.profiles.upsert(BackupProfile(
            id="rapide", name="Rapide", include=("world",), dest="profiles/rapide",
            hours=6, last_run=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)))
        self.service.tick_backup_profiles(self.owner)               # FixedClock = 12:00 -> 6 h écoulées
        dests = [c["dest"] for c in self.profile_backup.calls]
        self.assertIn("profiles/rapide", dests)
        self.assertNotIn("manual", dests)                            # pas de planification -> pas déclenché

    def test_manual_profile_backup_is_blocked_during_restore(self):
        self.service.restore_backup(self.owner, "world-20260701-0000.tar.gz")

        with self.assertRaisesRegex(BackupUnavailable, "pendant une restauration"):
            self.service.trigger_profile_backup(self.owner, "manual")

        self.assertEqual(self.profile_backup.calls, [])

    def test_manual_profile_backup_is_blocked_during_safety_backup(self):
        self.backup.running = True

        with self.assertRaisesRegex(BackupUnavailable, "sauvegarde de sécurité"):
            self.service.trigger_profile_backup(self.owner, "manual")

        self.assertEqual(self.profile_backup.calls, [])

    def test_scheduler_does_not_capture_restore_transaction(self):
        from datetime import datetime, timezone
        from domain.model import BackupProfile

        last_run = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
        self.profiles.upsert(BackupProfile(
            id="rapide",
            name="Rapide",
            include=("all",),
            dest="profiles/rapide",
            hours=6,
            last_run=last_run,
        ))
        self.service.restore_backup(self.owner, "world-20260701-0000.tar.gz")

        self.service.tick_backup_profiles(self.owner)

        self.assertEqual(self.profile_backup.calls, [])
        self.assertEqual(self.profiles.get("rapide").last_run, last_run)

    def test_scheduler_does_not_advance_during_safety_backup(self):
        from datetime import datetime, timezone
        from domain.model import BackupProfile

        last_run = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
        self.profiles.upsert(BackupProfile(
            id="rapide",
            name="Rapide",
            include=("all",),
            dest="profiles/rapide",
            hours=6,
            last_run=last_run,
        ))
        self.backup.running = True

        self.service.tick_backup_profiles(self.owner)

        self.assertEqual(self.profile_backup.calls, [])
        self.assertEqual(self.profiles.get("rapide").last_run, last_run)

    def test_interval_first_pass_seeds_counter_without_backup(self):

        from domain.model import BackupProfile
        self.profiles.upsert(BackupProfile(
            id="rapide", name="Rapide", include=("world",), dest="profiles/rapide", hours=6))
        self.service.tick_backup_profiles(self.owner)
        dests = [c["dest"] for c in self.profile_backup.calls]
        self.assertNotIn("profiles/rapide", dests)                   # pas de sauvegarde surprise
        self.assertIsNotNone(self.profiles.get("rapide").last_run)   # compteur amorcé

    def test_interval_waits_when_not_elapsed(self):
        from datetime import datetime, timezone

        from domain.model import BackupProfile
        self.profiles.upsert(BackupProfile(
            id="rapide", name="Rapide", include=("world",), dest="profiles/rapide",
            hours=6, last_run=datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)))
        self.service.tick_backup_profiles(self.owner)                # FixedClock 12:00 -> 3 h
        dests = [c["dest"] for c in self.profile_backup.calls]
        self.assertNotIn("profiles/rapide", dests)

    def test_suspended_profile_never_ticks(self):
        self.service.toggle_backup_profile(self.owner, "auto", enabled=False)
        self.service.tick_backup_profiles(self.owner)
        self.assertEqual(self.profile_backup.calls, [])

    def test_delete_profile_keeps_archives_and_refuses_while_running(self):
        self.profile_backup.running = True
        with self.assertRaises(BackupUnavailable):
            self.service.delete_backup_profile(self.owner, "auto")
        self.profile_backup.running = False
        self.service.delete_backup_profile(self.owner, "auto")
        self.assertIsNone(self.profiles.get("auto"))

    def test_update_profile_preserves_dest_and_last_run(self):
        updated = self.service.update_backup_profile(
            self.owner, "auto", "Nuit", ("world",), daily_at="03:30",
            retention_daily_days=14, retention_monthly_months=6)
        self.assertEqual(updated.dest, "scheduled")                  # la destination ne bouge jamais
        self.assertEqual(updated.daily_at, "03:30")

    def test_successful_backup_applies_profile_retention_automatically(self):
        from datetime import datetime, timezone
        from domain.model import BackupArchive, BackupProfile
        self.profiles.upsert(BackupProfile(
            id="auto", name="Automatiques", include=("all",), dest="scheduled",
            daily_at="04:00", retention_daily_days=30, retention_monthly_months=12))
        self.backup_archives._archives = [
            BackupArchive("scheduled/tres-vieille.tar.gz", 10, datetime(2024, 1, 1, tzinfo=timezone.utc)),
            BackupArchive("scheduled/fraiche.tar.gz", 10, datetime(2026, 7, 1, 12, 0, 30, tzinfo=timezone.utc)),
        ]
        self.service.trigger_profile_backup(self.owner, "auto")
        self.profile_backup.running = False
        self.service._scan_backup_outcomes()                          # issue constatée
        deleted = self.backup_archives.deleted
        self.assertIn("scheduled/tres-vieille.tar.gz", deleted)       # rétention appliquée seule
        self.assertNotIn("scheduled/fraiche.tar.gz", deleted)
        self.assertTrue(any("phase=retention_applied" in e.detail for e in self.audit.entries))

    def test_failed_backup_never_applies_retention(self):
        self.service.trigger_profile_backup(self.owner, "manual")     # dest manual/ : protégée
        self.profile_backup.running = False
        self.service._scan_backup_outcomes()
        self.assertEqual(self.backup_archives.deleted, [])


class TestStorageOverview(ServiceTestBase):
    """Historique de stockage + projection de saturation (backlog n° 6)."""

    def _history(self, **kwargs):
        from tests.fakes import FakeStorageHistory
        history = FakeStorageHistory(**kwargs)
        # Injecté après coup : ServiceTestBase ne câble pas ce port optionnel.
        self.service._storage_history = history
        return history

    def test_no_port_configured_returns_empty(self):
        history, saturation = self.service.storage_overview(self.owner)
        self.assertEqual(history, [])
        self.assertIsNone(saturation)

    def test_returns_history_and_no_saturation_on_flat_trend(self):
        self._history(snapshots=[
            StorageSnapshot(date="2026-07-01", free_bytes=1000, families={"manual": 500}),
            StorageSnapshot(date="2026-07-05", free_bytes=1000, families={"manual": 500}),
        ])
        history, saturation = self.service.storage_overview(self.owner)
        self.assertEqual([s.date for s in history], ["2026-07-01", "2026-07-05"])
        self.assertIsNone(saturation)  # tendance plate : rien à projeter

    def test_projects_saturation_date_on_shrinking_free_space(self):
        self._history(snapshots=[
            StorageSnapshot(date="2026-07-01", free_bytes=1000, families={"manual": 500}),
            StorageSnapshot(date="2026-07-05", free_bytes=600, families={"manual": 900}),
        ])
        _, saturation = self.service.storage_overview(self.owner)
        # -100 octets/jour, 600 restants -> 6 jours de plus depuis le 05/07.
        self.assertEqual(saturation, date(2026, 7, 11))

    def test_guest_denied(self):
        self._history()
        with self.assertRaises(PermissionDenied):
            self.service.storage_overview(self.guest)


class TestAlertThresholds(ServiceTestBase):
    """Seuils d'alerte réglables (backlog fiabilité n° 4)."""

    def _thresholds(self):
        from tests.fakes import FakeAlertThresholds
        store = FakeAlertThresholds()
        # Injecté après coup : ServiceTestBase ne câble pas ce port optionnel.
        self.service._alert_thresholds = store
        return store

    def test_no_port_configured_returns_domain_defaults(self):
        from domain.model import AlertThresholds
        self.assertEqual(self.service.alert_thresholds(self.owner), AlertThresholds())

    def test_owner_can_set_and_read_back(self):
        self._thresholds()
        self.service.set_alert_thresholds(self.owner, 80.0, 25.0, 5.0)
        result = self.service.alert_thresholds(self.owner)
        self.assertEqual((result.mspt_threshold_ms, result.disk_min_free_gib,
                         result.mspt_sustained_minutes), (80.0, 25.0, 5.0))
        self.assertIn("phase=alert_thresholds_updated", self.audit.entries[-1].detail)

    def test_admin_denied_and_audited(self):
        self._thresholds()
        with self.assertRaises(PermissionDenied):
            self.service.set_alert_thresholds(self.admin, 80.0, 25.0, 5.0)
        self.assertEqual(self.audit.entries[-1].outcome, "denied")

    def test_invalid_values_rejected(self):
        self._thresholds()
        with self.assertRaises(InvalidGameValue):
            self.service.set_alert_thresholds(self.owner, 0, 25.0, 5.0)
        with self.assertRaises(InvalidGameValue):
            self.service.set_alert_thresholds(self.owner, 80.0, -1, 5.0)
        with self.assertRaises(InvalidGameValue):
            self.service.set_alert_thresholds(self.owner, 80.0, 25.0, 0)

    def test_set_without_port_configured_raises(self):
        with self.assertRaises(ServerUnavailable):
            self.service.set_alert_thresholds(self.owner, 80.0, 25.0, 5.0)


class TestEventTags(ServiceTestBase):
    """Chaque notification débrayable porte son tag (filtres par canal)."""

    def test_restart_success_tagged(self):
        self.service.restart(self.owner)
        title, _, _, event = self.notifier.sent[-1]
        self.assertEqual((title, event), ("Serveur redémarré", "restart"))

    def test_kick_and_ban_tagged_moderation(self):
        self.service.kick(self.owner, "alice", "afk")
        self.assertEqual(self.notifier.sent[-1][3], "moderation")
        self.service.ban(self.owner, "alice", "grief")
        title, body, _, event = self.notifier.sent[-1]
        self.assertEqual(event, "moderation")
        self.assertIn("a banni alice", body)

    def test_update_success_tagged(self):
        self.game._players = []
        self.service.apply_update(self.owner)
        self.assertEqual(self.notifier.sent[-1][3], "update")


class TestWorkerIntegrityGuards(ServiceTestBase):
    """Un one-shot désaligné (image/réseau périmé) bloque avant d'agir."""

    def _integrity(self, **kwargs):
        from tests.fakes import FakeWorkerIntegrity
        integrity = FakeWorkerIntegrity(**kwargs)
        # Injecté après coup : ServiceTestBase ne câble pas ce port optionnel.
        self.service._worker_integrity = integrity
        return integrity

    def _with_profiles(self):
        from domain.model import BackupProfile
        from tests.fakes import FakeBackupProfiles, FakeProfileBackup
        self.profiles = FakeBackupProfiles([
            BackupProfile(id="manual", name="Manuelles", include=("all",), dest="manual")])
        self.profile_backup = FakeProfileBackup()
        self.service._backup_profiles = self.profiles
        self.service._profile_backup = self.profile_backup

    def test_confirmed_misalignment_blocks_profile_backup(self):
        self._with_profiles()
        self._integrity(issues_by_kind={"backup": ["Outil de sauvegarde : périmé"]})
        with self.assertRaisesRegex(BackupUnavailable, "périmé"):
            self.service.trigger_profile_backup(self.owner, "manual")
        self.assertEqual(self.profile_backup.calls, [])

    def test_unknown_state_does_not_block_profile_backup(self):
        self._with_profiles()
        self._integrity(unknown_by_kind={"backup": ["Outil : vérification impossible"]})
        self.service.trigger_profile_backup(self.owner, "manual")
        self.assertEqual(len(self.profile_backup.calls), 1)

    def test_restore_preflight_blocks_even_on_unknown(self):
        self._integrity(unknown_by_kind={
            "restore": ["Outil de restauration : vérification impossible (proxy muet)"]})
        issues = self.service.restore_preflight(self.owner, "world-20260701-0000.tar.gz")
        self.assertTrue(any("vérification impossible" in issue for issue in issues))

    def test_connection_checks_include_worker_rows(self):
        from domain.model import WorkerCheck
        self._integrity(checks=[
            WorkerCheck("mc-restore", "Outil de restauration", False, "périmé — recréer"),
            WorkerCheck("mc-op-levels", "Outil des niveaux OP", True, "aligné"),
        ])
        checks = self.service.connection_checks(self.owner)
        stale = next(c for c in checks if c.key == "worker_mc-restore")
        self.assertFalse(stale.ok)
        self.assertIn("périmé", stale.detail)
        aligned = next(c for c in checks if c.key == "worker_mc-op-levels")
        self.assertTrue(aligned.ok)


class TestProfileDeleteDuringBackup(ServiceTestBase):
    """2b (retour Jeremy 18/07) : seul le profil ACTIF est verrouillé."""

    def setUp(self):
        super().setUp()
        from domain.model import BackupProfile
        from tests.fakes import FakeBackupProfiles, FakeProfileBackup
        self.profiles = FakeBackupProfiles([
            BackupProfile(id="actif", name="Actif", include=("all",), dest="profiles/actif"),
            BackupProfile(id="autre", name="Autre", include=("world",), dest="profiles/autre"),
        ])
        self.profile_backup = FakeProfileBackup()
        self.service._backup_profiles = self.profiles
        self.service._profile_backup = self.profile_backup

    def test_other_profile_deletable_while_backup_runs(self):
        self.service.trigger_profile_backup(self.owner, "actif")
        self.profile_backup.running = True
        self.service.delete_backup_profile(self.owner, "autre")   # ne lève pas
        self.assertIsNone(self.profiles.get("autre"))

    def test_active_profile_locked_while_backup_runs(self):
        self.service.trigger_profile_backup(self.owner, "actif")
        self.profile_backup.running = True
        with self.assertRaisesRegex(BackupUnavailable, "en cours"):
            self.service.delete_backup_profile(self.owner, "actif")

    def test_unknown_active_profile_stays_conservative(self):
        # App redémarrée pendant une sauvegarde : identité inconnue -> blocage.
        self.profile_backup.running = True
        with self.assertRaisesRegex(BackupUnavailable, "en cours"):
            self.service.delete_backup_profile(self.owner, "actif")

    def test_no_backup_running_deletes_freely(self):
        self.service.delete_backup_profile(self.owner, "actif")
        self.assertIsNone(self.profiles.get("actif"))
