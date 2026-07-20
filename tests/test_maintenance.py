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
from domain.errors import MaintenanceUnavailable, PermissionDenied
from domain.model import Permission, Role, User
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


def _service(*, doorman=None, container=None, game=None, audit=None, clock=None, pending=None):
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
        notifications=FakeNotifier(),
        bans=FakeBans(),
        temp_bans=FakeTempBans(),
        restart_schedule=InMemoryRestartSchedule(),
        doorman=doorman if doorman is not None else FakeDoorman(),
        pending_maintenance=pending if pending is not None else InMemoryPendingMaintenance(),
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


if __name__ == "__main__":
    unittest.main()
