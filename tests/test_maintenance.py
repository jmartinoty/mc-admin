"""Tests du mode maintenance (service + compte à rebours).

Ce qui est vraiment couvert ici, ce sont les trois garde-fous du module
`domain/services/maintenance.py` — l'ordre des opérations est la sécurité :

  - on n'arrête jamais le serveur sans que le portier prenne le relais,
    et si le portier échoue, on le DIT au lieu de faire semblant ;
  - on ne redémarre jamais le serveur avant que le portier ait rendu
    l'adresse (les deux se disputent la même IP statique) ;
  - l'état « en maintenance » est toujours relu du portier réel, jamais
    déduit d'une variable locale.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from adapters.maintenance_state import InMemoryPendingMaintenance
from adapters.restart_schedule import InMemoryRestartSchedule
from domain.errors import InvalidDuration, MaintenanceUnavailable, PermissionDenied
from domain.model import Permission, Player, Role, ScheduledMaintenance, User
from domain.services import AdminService
from domain.services.maintenance import (
    MAINTENANCE_STOP_TIMEOUT_SECONDS,
    build_maintenance_messages,
)
from maintenance_scheduler import MAINTENANCE_USER, MaintenanceScheduler

from tests.fakes import (
    FakeBackupArchives,
    FakeBans,
    FakeContainer,
    FakeDoorman,
    FakeGame,
    FakeLogs,
    FakeMetrics,
    FakeNotifier,
    FakeOps,
    FakePlayerHistory,
    FakeScheduledMaintenance,
    FakeTempBans,
    FakeUpdater,
    MutableClock,
    RecordingAudit,
)

T0 = datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc)

OWNER = User(username="jeremy", role=Role(name="owner", permissions=frozenset(), grants_all=True))
VIEWER = User(
    username="trappeur",
    role=Role(name="viewer", permissions=frozenset({Permission.STATUS}), grants_all=False),
)


def _service(*, doorman=None, container=None, game=None, audit=None, clock=None,
             pending=None, scheduled_maintenance=None, notifier=None):
    return AdminService(
        game=game or FakeGame(),
        container=container or FakeContainer(),
        logs=FakeLogs(),
        audit=audit if audit is not None else RecordingAudit(),
        clock=clock or MutableClock(T0),
        container_name="minecraft",
        metrics=FakeMetrics(),
        player_history=FakePlayerHistory(),
        backup_archives=FakeBackupArchives(),
        updater=FakeUpdater(),
        ops=FakeOps(),
        notifications=notifier if notifier is not None else FakeNotifier(),
        bans=FakeBans(),
        temp_bans=FakeTempBans(),
        restart_schedule=InMemoryRestartSchedule(),
        doorman=doorman if doorman is not None else FakeDoorman(),
        pending_maintenance=pending if pending is not None else InMemoryPendingMaintenance(),
        scheduled_maintenance=scheduled_maintenance,
    )


class TestMessages(unittest.TestCase):
    """Le seul endroit qui décide ce que le joueur lit — fonction pure."""

    def test_message_et_horizon_apparaissent_dans_le_motd_et_le_refus(self):
        motd, kick = build_maintenance_messages("Migration du monde", "14h30")
        self.assertIn("Migration du monde", motd)
        self.assertIn("14h30", motd)
        self.assertIn("Migration du monde", kick)
        self.assertIn("14h30", kick)

    def test_motd_tient_sur_deux_lignes(self):
        motd, _ = build_maintenance_messages("Migration du monde", "14h30")
        self.assertEqual(len(motd.split("\n")), 2)

    def test_message_vide_donne_un_texte_par_defaut_honnete(self):
        motd, kick = build_maintenance_messages("", "")
        self.assertIn("maintenance", motd.lower())
        self.assertTrue(kick.strip())

    def test_motd_ne_repete_pas_le_titre_quand_rien_a_ajouter(self):
        # Retour Jeremy 31/07 : « Maintenance en cours » puis « Le serveur est
        # fermé pour maintenance » disait deux fois la même chose. Sans message
        # ni retour prévu, le MOTD tient sur la SEULE ligne de titre.
        motd, kick = build_maintenance_messages("", "")
        self.assertEqual(len(motd.split("\n")), 1)
        # Le refus au login, lui, garde une phrase complète (pas de titre
        # sous les yeux du joueur).
        self.assertIn("maintenance", kick.lower())

    def test_motd_sans_message_affiche_seulement_le_retour_prevu(self):
        motd, kick = build_maintenance_messages("", "14h30")
        lines = motd.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertIn("14h30", lines[1])
        self.assertNotIn("fermé pour maintenance", lines[1])
        self.assertIn("14h30", kick)

    def test_texte_multiligne_est_aplati(self):
        # Défense en profondeur : le MOTD a une structure de lignes, un texte
        # libre ne doit pas pouvoir la casser.
        motd, _ = build_maintenance_messages("ligne1\nligne2\nligne3", "")
        self.assertEqual(len(motd.split("\n")), 2)


class TestEnterMaintenance(unittest.TestCase):
    def test_ferme_le_serveur_puis_met_le_portier_en_poste(self):
        container, doorman = FakeContainer(), FakeDoorman()
        svc = _service(container=container, doorman=doorman)

        svc.enter_maintenance(OWNER, message="Migration", until="14h30")

        self.assertEqual(container.stops, 1)
        self.assertTrue(doorman.running)
        motd, kick = doorman.consignes[0]
        self.assertIn("Migration", motd)
        self.assertIn("Migration", kick)

    def test_laisse_a_minecraft_le_temps_de_sauvegarder(self):
        # Le défaut docker (10 s) tuait le serveur en pleine sauvegarde.
        container = FakeContainer()
        _service(container=container).enter_maintenance(OWNER)
        self.assertEqual(container.stop_timeouts, [MAINTENANCE_STOP_TIMEOUT_SECONDS])

    def test_previent_les_joueurs_avant_de_couper(self):
        game = FakeGame()
        _service(game=game).enter_maintenance(OWNER)
        self.assertTrue(game.said)
        self.assertEqual(game.saved, 1)

    def test_rcon_muet_n_empeche_pas_la_maintenance(self):
        # C'est souvent PARCE QUE le serveur va mal qu'on ferme.
        container, doorman = FakeContainer(), FakeDoorman()
        svc = _service(container=container, doorman=doorman, game=FakeGame(available=False))
        svc.enter_maintenance(OWNER)
        self.assertEqual(container.stops, 1)
        self.assertTrue(doorman.running)

    def test_refuse_si_deja_en_maintenance_sans_re_arreter_le_serveur(self):
        container = FakeContainer()
        svc = _service(container=container, doorman=FakeDoorman(running=True))
        with self.assertRaises(MaintenanceUnavailable):
            svc.enter_maintenance(OWNER)
        self.assertEqual(container.stops, 0)

    def test_refus_rbac_ne_touche_pas_au_serveur(self):
        container, doorman = FakeContainer(), FakeDoorman()
        svc = _service(container=container, doorman=doorman)
        with self.assertRaises(PermissionDenied):
            svc.enter_maintenance(VIEWER)
        self.assertEqual(container.stops, 0)
        self.assertFalse(doorman.running)

    def test_portier_defaillant_laisse_le_serveur_ferme_et_le_dit(self):
        """Le pire scénario : serveur arrêté, personne à l'accueil. On ne
        prétend pas que tout va bien — audit ET notification."""
        container = FakeContainer()
        doorman = FakeDoorman(fail_engage=True)
        audit, notifier = RecordingAudit(), FakeNotifier()
        svc = _service(container=container, doorman=doorman, audit=audit)
        svc._notify = notifier

        with self.assertRaises(MaintenanceUnavailable):
            svc.enter_maintenance(OWNER)

        self.assertEqual(container.stops, 1)
        self.assertFalse(doorman.running)
        details = " ".join(e.detail for e in audit.entries)
        self.assertIn("maintenance_doorman_failed", details)
        self.assertTrue(any(n[2] == "error" for n in notifier.sent))

    def test_engagement_est_audite(self):
        audit = RecordingAudit()
        _service(audit=audit).enter_maintenance(OWNER, message="Migration")
        details = " ".join(e.detail for e in audit.entries)
        self.assertIn("maintenance_engaged", details)
        self.assertIn("requested_by=jeremy", details)


class TestExitMaintenance(unittest.TestCase):
    def test_releve_le_portier_avant_de_redemarrer(self):
        container = FakeContainer()
        doorman = FakeDoorman(running=True)
        svc = _service(container=container, doorman=doorman)

        svc.exit_maintenance(OWNER)

        self.assertEqual(doorman.releases, 1)
        self.assertFalse(doorman.running)
        self.assertEqual(container.starts, 1)

    def test_portier_qui_tient_l_adresse_empeche_le_redemarrage(self):
        """LE garde-fou : redémarrer ici échouerait sur un conflit d'IP.
        On préfère refuser la réouverture et le dire."""
        container = FakeContainer()
        doorman = FakeDoorman(running=True, fail_release=True)
        svc = _service(container=container, doorman=doorman)

        with self.assertRaises(MaintenanceUnavailable):
            svc.exit_maintenance(OWNER)

        self.assertEqual(container.starts, 0)

    def test_reprise_apres_un_engagement_a_moitie_reussi(self):
        # Serveur arrêté, portier jamais monté : « Rouvrir » doit marcher.
        container, doorman = FakeContainer(running=False), FakeDoorman(running=False)
        _service(container=container, doorman=doorman).exit_maintenance(OWNER)
        self.assertEqual(container.starts, 1)

    def test_refus_rbac_ne_redemarre_rien(self):
        container = FakeContainer()
        svc = _service(container=container, doorman=FakeDoorman(running=True))
        with self.assertRaises(PermissionDenied):
            svc.exit_maintenance(VIEWER)
        self.assertEqual(container.starts, 0)

    def test_reouverture_est_auditee(self):
        audit = RecordingAudit()
        svc = _service(audit=audit, doorman=FakeDoorman(running=True))
        svc.exit_maintenance(OWNER)
        self.assertIn("maintenance_ended", " ".join(e.detail for e in audit.entries))


class TestMaintenanceStatus(unittest.TestCase):
    def test_l_etat_vient_du_portier_reel(self):
        svc = _service(doorman=FakeDoorman(running=True))
        self.assertTrue(svc.maintenance_status(OWNER).active)

    def test_un_simple_lecteur_voit_le_bandeau(self):
        # Tout le monde a le droit de savoir que le serveur est fermé.
        svc = _service(doorman=FakeDoorman(running=True))
        self.assertTrue(svc.maintenance_status(VIEWER).active)

    def test_portier_illisible_ne_casse_pas_le_tableau_de_bord(self):
        doorman = FakeDoorman()
        doorman.status_fail = True
        self.assertFalse(_service(doorman=doorman).maintenance_status(OWNER).active)


class TestGraceDelay(unittest.TestCase):
    def test_le_delai_annonce_sans_fermer_tout_de_suite(self):
        container, doorman, game = FakeContainer(), FakeDoorman(), FakeGame()
        svc = _service(container=container, doorman=doorman, game=game)

        svc.enter_maintenance(OWNER, message="Migration", grace_minutes=10)

        self.assertEqual(container.stops, 0)
        self.assertFalse(doorman.running)
        self.assertTrue(any("maintenance" in m.lower() for m in game.said))

    def test_l_echeance_ferme_le_serveur(self):
        clock = MutableClock(T0)
        container, doorman = FakeContainer(), FakeDoorman()
        svc = _service(container=container, doorman=doorman, clock=clock)

        svc.enter_maintenance(OWNER, message="Migration", grace_minutes=10)
        clock.moment = T0 + timedelta(minutes=10, seconds=1)
        svc.tick_maintenance(MAINTENANCE_USER)

        self.assertEqual(container.stops, 1)
        self.assertTrue(doorman.running)

    def test_avant_l_echeance_le_serveur_reste_ouvert(self):
        clock = MutableClock(T0)
        container = FakeContainer()
        svc = _service(container=container, clock=clock)

        svc.enter_maintenance(OWNER, grace_minutes=10)
        clock.moment = T0 + timedelta(minutes=5)
        svc.tick_maintenance(MAINTENANCE_USER)

        self.assertEqual(container.stops, 0)

    def test_le_compte_a_rebours_previent_les_joueurs(self):
        clock = MutableClock(T0)
        game = FakeGame()
        svc = _service(game=game, clock=clock)
        svc.enter_maintenance(OWNER, grace_minutes=10)
        before = len(game.said)

        clock.moment = T0 + timedelta(minutes=5, seconds=1)  # seuil des 5 min
        svc.tick_maintenance(MAINTENANCE_USER)

        self.assertGreater(len(game.said), before)

    def test_un_seuil_deja_passe_n_est_pas_annonce_retroactivement(self):
        # Annoncer « dans 10 minutes » pour un délai de 2 minutes serait faux.
        clock = MutableClock(T0)
        game = FakeGame()
        svc = _service(game=game, clock=clock)
        svc.enter_maintenance(OWNER, grace_minutes=2)
        game.said.clear()

        clock.moment = T0 + timedelta(seconds=1)
        svc.tick_maintenance(MAINTENANCE_USER)

        self.assertFalse([m for m in game.said if "10 minute" in m])

    def test_annulation_laisse_le_serveur_ouvert(self):
        clock = MutableClock(T0)
        container = FakeContainer()
        svc = _service(container=container, clock=clock)

        svc.enter_maintenance(OWNER, grace_minutes=10)
        svc.cancel_pending_maintenance(OWNER)
        clock.moment = T0 + timedelta(minutes=11)
        svc.tick_maintenance(MAINTENANCE_USER)

        self.assertEqual(container.stops, 0)

    def test_rouvrir_annule_une_fermeture_annoncee(self):
        clock = MutableClock(T0)
        container = FakeContainer()
        svc = _service(container=container, clock=clock)

        svc.enter_maintenance(OWNER, grace_minutes=10)
        svc.exit_maintenance(OWNER)
        clock.moment = T0 + timedelta(minutes=11)
        svc.tick_maintenance(MAINTENANCE_USER)

        self.assertEqual(container.stops, 0)  # l'annonce ne survit pas

    def test_annonce_est_auditee(self):
        audit = RecordingAudit()
        _service(audit=audit).enter_maintenance(OWNER, grace_minutes=10)
        self.assertIn("maintenance_scheduled", " ".join(e.detail for e in audit.entries))


class TestMaintenanceUser(unittest.TestCase):
    def test_l_identite_systeme_ne_peut_que_la_maintenance(self):
        self.assertTrue(MAINTENANCE_USER.can(Permission.MAINTENANCE))
        self.assertFalse(MAINTENANCE_USER.can(Permission.STOP))
        self.assertFalse(MAINTENANCE_USER.can(Permission.RCON_RAW))

    def test_le_thread_survit_a_un_service_qui_leve(self):
        class Boom:
            def tick_maintenance(self, _user):
                raise RuntimeError("boum")

        scheduler = MaintenanceScheduler(Boom(), poll_seconds=0)
        calls = {"n": 0}

        def fake_wait(_seconds):
            calls["n"] += 1
            if calls["n"] >= 3:
                scheduler._stop.set()
            return False

        scheduler._wait = fake_wait
        scheduler._run()  # ne doit pas lever
        self.assertGreaterEqual(calls["n"], 3)


class TestMaintenanceGuardrailAndRecurring(unittest.TestCase):
    """Garde-fou joueurs + maintenance récurrente quotidienne (palier b)."""

    def _hhmm_in(self, clock, minutes):
        return (clock.now().astimezone() + timedelta(minutes=minutes)).strftime("%H:%M")

    def _today(self, clock):
        return clock.now().astimezone().date().isoformat()

    # ---- garde-fou « ne pas exécuter tant que joueurs connectés » ----

    def test_defer_holds_close_until_server_empty(self):
        clock = MutableClock(T0)
        game = FakeGame(players=[Player("alice")])
        doorman = FakeDoorman(running=False)
        svc = _service(game=game, doorman=doorman, clock=clock)
        svc.enter_maintenance(OWNER, grace_minutes=5, defer_if_players=True)
        clock.moment += timedelta(minutes=5)  # échéance atteinte
        svc.tick_maintenance(MAINTENANCE_USER)
        # personne n'est déconnecté : aucune prise de poste, pending intact.
        self.assertEqual(doorman.consignes, [])
        self.assertIsNotNone(svc._pending_maintenance.status())
        # serveur vidé -> la fermeture s'engage enfin.
        game._players = []
        svc.tick_maintenance(MAINTENANCE_USER)
        self.assertEqual(len(doorman.consignes), 1)
        self.assertIsNone(svc._pending_maintenance.status())

    def test_defer_when_rcon_unavailable_holds_close(self):
        clock = MutableClock(T0)
        game = FakeGame(available=False)  # vide NON confirmable
        svc = _service(game=game, doorman=FakeDoorman(running=False), clock=clock)
        svc.enter_maintenance(OWNER, grace_minutes=5, defer_if_players=True)
        clock.moment += timedelta(minutes=5)
        svc.tick_maintenance(MAINTENANCE_USER)
        self.assertIsNotNone(svc._pending_maintenance.status())

    def test_defer_off_closes_even_with_players(self):
        clock = MutableClock(T0)
        game = FakeGame(players=[Player("alice")])
        doorman = FakeDoorman(running=False)
        svc = _service(game=game, doorman=doorman, clock=clock)
        svc.enter_maintenance(OWNER, grace_minutes=5)  # sans flag
        clock.moment += timedelta(minutes=5)
        svc.tick_maintenance(MAINTENANCE_USER)
        self.assertEqual(len(doorman.consignes), 1)  # fermé malgré alice

    # ---- maintenances programmées (liste : once + weekly) ----

    def _weekly_today(self, clock, minutes, **kw):
        wd = clock.now().astimezone().weekday()
        return ScheduledMaintenance(
            id="e1", kind="weekly", time_hhmm=self._hhmm_in(clock, minutes),
            weekdays=(wd,), **kw,
        )

    def test_scheduled_weekly_arms_in_lead_window_with_flag(self):
        clock = MutableClock(T0)
        store = FakeScheduledMaintenance(entries=[
            self._weekly_today(clock, 3, lead_seconds=300, message="Migration",
                               defer_if_players=True),
        ])
        svc = _service(clock=clock, doorman=FakeDoorman(running=False),
                       scheduled_maintenance=store)
        svc.tick_maintenance(MAINTENANCE_USER)
        pending = svc._pending_maintenance.status()
        self.assertIsNotNone(pending)
        self.assertTrue(pending.defer_if_players)
        self.assertEqual(store.last_fired("e1"), self._today(clock))

    def test_scheduled_once_arms_on_its_date(self):
        clock = MutableClock(T0)
        store = FakeScheduledMaintenance(entries=[
            ScheduledMaintenance(id="e1", kind="once", date=self._today(clock),
                                 time_hhmm=self._hhmm_in(clock, 3), lead_seconds=300),
        ])
        svc = _service(clock=clock, doorman=FakeDoorman(running=False),
                       scheduled_maintenance=store)
        svc.tick_maintenance(MAINTENANCE_USER)
        self.assertIsNotNone(svc._pending_maintenance.status())

    def test_scheduled_not_armed_before_lead_window(self):
        clock = MutableClock(T0)
        store = FakeScheduledMaintenance(entries=[self._weekly_today(clock, 30, lead_seconds=300)])
        svc = _service(clock=clock, doorman=FakeDoorman(running=False),
                       scheduled_maintenance=store)
        svc.tick_maintenance(MAINTENANCE_USER)
        self.assertIsNone(svc._pending_maintenance.status())

    def test_scheduled_weekly_ignored_on_other_weekday(self):
        clock = MutableClock(T0)
        other = (clock.now().astimezone().weekday() + 1) % 7
        store = FakeScheduledMaintenance(entries=[
            ScheduledMaintenance(id="e1", kind="weekly", weekdays=(other,),
                                 time_hhmm=self._hhmm_in(clock, 3), lead_seconds=300),
        ])
        svc = _service(clock=clock, doorman=FakeDoorman(running=False),
                       scheduled_maintenance=store)
        svc.tick_maintenance(MAINTENANCE_USER)
        self.assertIsNone(svc._pending_maintenance.status())

    def test_scheduled_once_past_date_is_removed(self):
        clock = MutableClock(T0)
        store = FakeScheduledMaintenance(entries=[
            ScheduledMaintenance(id="e1", kind="once", date="2020-01-01",
                                 time_hhmm="04:00", lead_seconds=300),
        ])
        svc = _service(clock=clock, doorman=FakeDoorman(running=False),
                       scheduled_maintenance=store)
        svc.tick_maintenance(MAINTENANCE_USER)
        self.assertEqual(store.list(), [])  # date passée : nettoyée

    def test_scheduled_skips_when_already_in_maintenance(self):
        clock = MutableClock(T0)
        store = FakeScheduledMaintenance(entries=[self._weekly_today(clock, 3, lead_seconds=300)])
        svc = _service(clock=clock, doorman=FakeDoorman(running=True),
                       scheduled_maintenance=store)
        svc.tick_maintenance(MAINTENANCE_USER)
        self.assertIsNone(svc._pending_maintenance.status())

    def test_add_scheduled_validates_and_persists(self):
        clock = MutableClock(T0)
        store = FakeScheduledMaintenance()
        svc = _service(clock=clock, scheduled_maintenance=store)
        future = (clock.now().astimezone().date() + timedelta(days=3)).isoformat()
        eid = svc.add_scheduled_maintenance(OWNER, kind="once", date=future, time_hhmm="03:00")
        self.assertTrue(eid)
        self.assertEqual(len(store.list()), 1)
        for bad in (
            dict(kind="once", date=future, time_hhmm="26:00"),   # heure invalide
            dict(kind="once", date="2020-01-01", time_hhmm="03:00"),  # date passée
            dict(kind="weekly", time_hhmm="03:00", weekdays=()),  # aucun jour
        ):
            with self.assertRaises(InvalidDuration):
                svc.add_scheduled_maintenance(OWNER, **bad)

    def test_remove_scheduled(self):
        store = FakeScheduledMaintenance(entries=[
            ScheduledMaintenance(id="e1", kind="weekly", weekdays=(0,), time_hhmm="04:00"),
        ])
        svc = _service(scheduled_maintenance=store)
        svc.remove_scheduled_maintenance(OWNER, "e1")
        self.assertEqual(store.list(), [])

    def test_update_refused_while_maintenance_active(self):
        # Le portier occupe l'IP statique du serveur : le `up -d` de mc-updater
        # réclamerait la même et échouerait, serveur laissé fermé. Refus AVANT
        # d'agir — et `force` ne contourne pas (contrainte technique).
        from domain.errors import UpdateUnavailable
        audit = RecordingAudit()
        svc = _service(doorman=FakeDoorman(running=True), audit=audit)
        for force in (False, True):
            with self.subTest(force=force):
                with self.assertRaises(UpdateUnavailable):
                    svc.apply_update(OWNER, force=force)
        self.assertEqual(svc._updater.applied, 0)          # rien n'a été lancé
        self.assertEqual(audit.entries[-1].outcome, "denied")
        self.assertIn("maintenance", audit.entries[-1].detail)

    def test_start_and_restart_refused_while_maintenance_active(self):
        # Incident du 31/07 : « Démarrer » pendant une maintenance -> Docker
        # refuse l'adresse tenue par le portier -> erreur 500 illisible.
        # Désormais : refus net, audité, avec le geste correct à faire.
        audit = RecordingAudit()
        container = FakeContainer(running=False)
        svc = _service(doorman=FakeDoorman(running=True), container=container, audit=audit)
        with self.assertRaises(MaintenanceUnavailable) as raised:
            svc.start(OWNER)
        self.assertIn("Rouvrir le serveur", str(raised.exception))
        with self.assertRaises(MaintenanceUnavailable):
            svc.restart(OWNER)
        self.assertEqual((container.starts, container.restarts), (0, 0))
        self.assertEqual(audit.entries[-1].outcome, "denied")

    def test_start_allowed_once_maintenance_ended(self):
        container = FakeContainer(running=False)
        svc = _service(doorman=FakeDoorman(running=False), container=container)
        svc.start(OWNER)
        self.assertEqual(container.starts, 1)

    def test_update_allowed_when_no_doorman_in_place(self):
        svc = _service(doorman=FakeDoorman(running=False), game=FakeGame(players=[]))
        svc.apply_update(OWNER)
        self.assertEqual(svc._updater.applied, 1)

    def test_viewer_cannot_add_scheduled(self):
        svc = _service(scheduled_maintenance=FakeScheduledMaintenance())
        with self.assertRaises(PermissionDenied):
            svc.add_scheduled_maintenance(VIEWER, kind="weekly", time_hhmm="04:00", weekdays=(0,))


if __name__ == "__main__":
    unittest.main()
