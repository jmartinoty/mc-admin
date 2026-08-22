"""Étape 2 du suivi des faits de jeu : persistance, tailer, Journal.

Le parseur lui-même est couvert par tests/test_game_events.py — ici on teste
ce qui l'entoure : ce qui est écrit en base, comment le tailer tranche
joueur/créature, et ce que le Journal en montre.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from adapters.game_events import SqliteGameEvents
from domain.game_events import parse_line
from domain.rbac import Permission
from player_watcher import PlayerLogWatcher

from tests.fakes import FakePlayerHistory

NOW = datetime(2026, 8, 22, 21, 15, tzinfo=timezone.utc)


def line(message: str) -> str:
    return f"[21:15:16] [Server thread/INFO]: {message}\n"


class SqliteGameEventsTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.path)
        self.store = SqliteGameEvents(self.path)

    def test_records_and_reads_back_a_death(self):
        self.store.record(parse_line(line("Trappeur was slain by Cave Spider")), NOW, "mob")
        (event,) = self.store.recent()
        self.assertEqual(event.player, "Trappeur")
        self.assertEqual(event.killer_id, "minecraft:cave_spider")
        self.assertEqual(event.killer_type, "mob")
        self.assertFalse(event.killer_is_player)
        self.assertEqual(event.at, NOW)

    def test_nothing_french_is_stored(self):
        """Le contrat d'internationalisation : la base ne contient que des
        identifiants, jamais une phrase — sinon l'historique serait figé en
        français le jour de la version anglaise."""
        self.store.record(parse_line(line("Trappeur fell from a high place")), NOW)
        (event,) = self.store.recent()
        self.assertEqual((event.key, event.cause), ("fell_high", "fall"))
        self.assertNotIn("tomb", event.raw)  # « est tombé » n'est pas en base

    def test_pvp_is_marked_as_such(self):
        self.store.record(parse_line(line("Trappeur was slain by Bucheron")), NOW, "player")
        (event,) = self.store.recent()
        self.assertTrue(event.killer_is_player)
        self.assertEqual(event.killer, "Bucheron")
        # Un pseudo capitalisé a la FORME d'un nom de créature : sans cette
        # remise à zéro, « Bucheron » serait rangé en `minecraft:bucheron` et
        # compterait comme un mob dans les agrégats de la page joueur.
        self.assertEqual(event.killer_id, "")

    def test_replaying_the_same_line_does_not_duplicate(self):
        """Rend l'import rétroactif des archives rejouable sans doubler
        l'historique (étape 3)."""
        event = parse_line(line("Trappeur was slain by Drowned"))
        self.store.record(event, NOW, "mob")
        self.store.record(event, NOW, "mob")
        self.assertEqual(len(self.store.recent()), 1)

    def test_recent_is_newest_first_and_filters(self):
        older = NOW - timedelta(days=10)
        self.store.record(parse_line(line("Trappeur drowned")), older)
        self.store.record(parse_line(line("<Trappeur> coucou")), NOW)
        self.assertEqual([e.kind for e in self.store.recent()], ["chat", "death"])
        self.assertEqual([e.kind for e in self.store.recent(kinds=("death",))], ["death"])
        recent_only = self.store.recent(since=NOW - timedelta(days=1))
        self.assertEqual([e.kind for e in recent_only], ["chat"])

    def test_purge_chat_spares_deaths(self):
        old = NOW - timedelta(days=200)
        self.store.record(parse_line(line("<Trappeur> vieux message")), old)
        self.store.record(parse_line(line("Trappeur drowned")), old)
        self.store.record(parse_line(line("<Trappeur> message récent")), NOW)
        purged = self.store.purge_chat(NOW - timedelta(days=90))
        self.assertEqual(purged, 1)
        kinds = sorted(e.kind for e in self.store.recent())
        self.assertEqual(kinds, ["chat", "death"])  # la mort ancienne est gardée


class WatcherGameEventTests(unittest.TestCase):
    """Le tailer est le seul à savoir qui est joueur : c'est lui qui tranche."""

    def setUp(self):
        fd, self.log = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        self.addCleanup(os.remove, self.log)
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db)
        self.store = SqliteGameEvents(self.db)
        self.history = FakePlayerHistory(summary=[])

    def _watcher(self, known=("bucheron",), retention=0) -> PlayerLogWatcher:
        return PlayerLogWatcher(
            self.log, self.history, clock=lambda: NOW, game_events=self.store,
            known_players=lambda: set(known), chat_retention_days=retention)

    def _consume(self, *messages, watcher=None):
        watcher = watcher or self._watcher()
        position = watcher._initial_position()
        with open(self.log, "a", encoding="utf-8") as fh:
            for message in messages:
                fh.write(line(message))
        watcher._consume_new_lines(position)
        return watcher

    def test_known_name_is_a_player_unknown_name_is_a_mob(self):
        self._consume("Trappeur was slain by Bucheron", "Trappeur was slain by Drowned")
        by_killer = {e.killer: e.killer_type for e in self.store.recent()}
        self.assertEqual(by_killer, {"Bucheron": "player", "Drowned": "mob"})

    def test_environment_death_has_no_killer_type(self):
        self._consume("Trappeur fell from a high place")
        (event,) = self.store.recent()
        self.assertEqual(event.killer_type, "")

    def test_joins_and_commands_still_work(self):
        """Non-régression : la mission première du tailer reste les sessions."""
        self._consume("alice joined the game",
                      "alice issued server command: /gamemode creative")
        self.assertEqual([j[0] for j in self.history.joins], ["alice"])
        self.assertEqual(len(self.store.recent()), 0)

    def test_a_broken_store_does_not_kill_the_tailer(self):
        class Explosif:
            def record(self, *a, **k):
                raise RuntimeError("disque plein")

        watcher = PlayerLogWatcher(self.log, self.history, clock=lambda: NOW,
                                   game_events=Explosif(), known_players=set)
        self._consume("Trappeur drowned", "alice joined the game", watcher=watcher)
        self.assertEqual([j[0] for j in self.history.joins], ["alice"])

    def test_unavailable_player_list_falls_back_to_mob(self):
        def boom():
            raise OSError("usercache illisible")

        watcher = PlayerLogWatcher(self.log, self.history, clock=lambda: NOW,
                                   game_events=self.store, known_players=boom)
        self._consume("Trappeur was slain by Bucheron", watcher=watcher)
        (event,) = self.store.recent()
        self.assertEqual(event.killer_type, "mob")

    def test_without_a_store_the_tailer_behaves_exactly_as_before(self):
        watcher = PlayerLogWatcher(self.log, self.history, clock=lambda: NOW)
        self._consume("Trappeur drowned", "alice joined the game", watcher=watcher)
        self.assertEqual([j[0] for j in self.history.joins], ["alice"])

    def test_chat_retention_purges_once_a_day(self):
        self.store.record(parse_line(line("<Trappeur> vieux")), NOW - timedelta(days=200))
        watcher = self._watcher(retention=90)
        watcher._purge_chat_if_due()
        self.assertEqual(self.store.recent(), [])
        # Deuxième passe le même jour : pas de nouvelle purge (le marqueur est
        # posé), ce que vérifie l'absence d'erreur et l'idempotence.
        watcher._purge_chat_if_due()

    def test_retention_zero_keeps_everything(self):
        self.store.record(parse_line(line("<Trappeur> vieux")), NOW - timedelta(days=500))
        self._watcher(retention=0)._purge_chat_if_due()
        self.assertEqual(len(self.store.recent()), 1)


if __name__ == "__main__":
    unittest.main()


class JournalIntegrationTests(unittest.TestCase):
    """Bout en bout : ce que le Journal affichera vraiment. La traduction se
    fait ICI (au service), jamais en base."""

    def setUp(self):
        from domain.model import User
        from domain.rbac import Role
        from domain.services import AdminService
        from adapters.restart_schedule import InMemoryRestartSchedule
        from tests.fakes import (
            FakeBackupArchives, FakeBans, FakeContainer, FakeGame, FakeLogs,
            FakeMetrics, FakeNotifier, FakeOps, FakeTempBans, FakeUpdater,
            FixedClock, RecordingAudit,
        )

        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db)
        self.store = SqliteGameEvents(self.db)
        self.service = AdminService(
            game=FakeGame(), container=FakeContainer(), logs=FakeLogs(),
            audit=RecordingAudit(), clock=FixedClock(), container_name="minecraft",
            metrics=FakeMetrics(), updater=FakeUpdater(), ops=FakeOps(),
            notifications=FakeNotifier(), player_history=FakePlayerHistory(summary=[]),
            backup_archives=FakeBackupArchives(), bans=FakeBans(),
            temp_bans=FakeTempBans(), restart_schedule=InMemoryRestartSchedule(),
            game_events=self.store,
        )
        self.owner = User(username="jeremy", role=Role(
            name="owner", permissions=frozenset(Permission)))

    def _game_facts(self):
        """Le Journal mêle sessions et faits de jeu : on isole les seconds."""
        return [(player, kind, text)
                for _at, player, kind, text in self.service.ingame_events(self.owner)
                if kind in ("death", "advancement", "chat")]

    def test_deaths_reach_the_journal_in_french(self):
        self.store.record(parse_line(line("Trappeur was slain by Cave Spider")), NOW, "mob")
        self.store.record(parse_line(line("Trappeur was slain by Bucheron")),
                          NOW - timedelta(minutes=5), "player")
        rendered = [f"{player} {text}" for player, _kind, text in self._game_facts()]
        self.assertEqual(rendered, [
            "Trappeur a été tué par une Araignée venimeuse",
            "Trappeur a été tué par Bucheron",
        ])

    def test_kinds_are_exposed_for_the_journal_families(self):
        self.store.record(parse_line(line("<Trappeur> coucou")), NOW)
        (_player, kind, text), = self._game_facts()
        self.assertEqual(kind, "chat")
        self.assertEqual(text, "a écrit : coucou")

    def test_reading_requires_the_audit_permission(self):
        from domain.errors import PermissionDenied
        from domain.model import User as U
        from domain.rbac import Role as R
        viewer = U(username="curieux", role=R(name="viewer", permissions=frozenset()))
        with self.assertRaises(PermissionDenied):
            self.service.ingame_events(viewer)

    def test_without_the_store_the_journal_keeps_working(self):
        from domain.services import AdminService
        from adapters.restart_schedule import InMemoryRestartSchedule
        from tests.fakes import (
            FakeBackupArchives, FakeBans, FakeContainer, FakeGame, FakeLogs,
            FakeMetrics, FakeNotifier, FakeOps, FakeTempBans, FakeUpdater,
            FixedClock, RecordingAudit,
        )
        service = AdminService(
            game=FakeGame(), container=FakeContainer(), logs=FakeLogs(),
            audit=RecordingAudit(), clock=FixedClock(), container_name="minecraft",
            metrics=FakeMetrics(), updater=FakeUpdater(), ops=FakeOps(),
            notifications=FakeNotifier(), player_history=FakePlayerHistory(summary=[]),
            backup_archives=FakeBackupArchives(), bans=FakeBans(),
            temp_bans=FakeTempBans(), restart_schedule=InMemoryRestartSchedule(),
        )
        kinds = {kind for _at, _p, kind, _t in service.ingame_events(self.owner)}
        self.assertEqual(kinds - {"join", "leave", "command"}, set())


class PlayerRecordTests(unittest.TestCase):
    """Les agrégats de la fiche joueur."""

    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db)
        self.store = SqliteGameEvents(self.db)

    def _death(self, message: str, killer_type: str = "", minutes: int = 0):
        self.store.record(parse_line(line(message)),
                          NOW - timedelta(minutes=minutes), killer_type)

    def test_counts_deaths_kills_and_causes(self):
        self._death("Trappeur was slain by Drowned", "mob", 1)
        self._death("Trappeur fell from a high place", "", 2)
        self._death("Bucheron was slain by Trappeur", "player", 3)
        record = self.store.player_record("Trappeur")
        self.assertEqual((record.deaths, record.kills), (2, 1))
        self.assertEqual(record.victims, (("Bucheron", 1),))

    def test_nemesis_is_whoever_kills_most_not_whoever_is_a_player(self):
        """Le défaut corrigé le 22/08 : un rival croisé une fois éclipsait la
        créature qui vous tue toutes les semaines."""
        for i in range(5):
            self._death("Trappeur was slain by Drowned", "mob", i)
        self._death("Trappeur was slain by Bucheron", "player", 99)
        nature, _id, name, count = self.store.player_record("Trappeur").nemesis
        self.assertEqual((nature, name, count), ("mob", "Drowned", 5))

    def test_nemesis_can_be_a_player(self):
        self._death("Trappeur was slain by Bucheron", "player", 1)
        self._death("Trappeur was slain by Bucheron", "player", 2)
        self._death("Trappeur was slain by Drowned", "mob", 3)
        nature, _id, name, count = self.store.player_record("Trappeur").nemesis
        self.assertEqual((nature, name, count), ("player", "Bucheron", 2))

    def test_unknown_player_has_an_empty_record(self):
        record = self.store.player_record("Personne")
        self.assertEqual((record.deaths, record.kills), (0, 0))
        self.assertIsNone(record.nemesis)

    def test_name_case_does_not_matter(self):
        """L'historique importé et le tailer n'ont pas forcément la même
        graphie que la fiche consultée."""
        self._death("Trappeur drowned")
        self.assertEqual(self.store.player_record("trappeur").deaths, 1)

    def test_messages_are_readable_per_player(self):
        self.store.record(parse_line(line("<Trappeur> salut")), NOW)
        self.store.record(parse_line(line("<Bucheron> coucou")), NOW)
        messages = self.store.for_player("Trappeur", kinds=("chat",))
        self.assertEqual([m.detail for m in messages], ["salut"])

    def test_pvp_pairs(self):
        self._death("Trappeur was slain by Bucheron", "player", 1)
        self._death("Trappeur was slain by Bucheron", "player", 2)
        self._death("Trappeur was slain by Drowned", "mob", 3)
        self.assertEqual(self.store.pvp_pairs(), [("Bucheron", "Trappeur", 2)])


class MessagePermissionTests(unittest.TestCase):
    """Relire les messages de quelqu'un est un geste de modération : barrière
    plus haute que le reste de la fiche."""

    def setUp(self):
        from adapters.restart_schedule import InMemoryRestartSchedule
        from domain.model import User
        from domain.rbac import Role
        from domain.services import AdminService
        from tests.fakes import (
            FakeBackupArchives, FakeBans, FakeContainer, FakeGame, FakeLogs,
            FakeMetrics, FakeNotifier, FakeOps, FakeTempBans, FakeUpdater,
            FixedClock, RecordingAudit,
        )

        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db)
        self.store = SqliteGameEvents(self.db)
        self.store.record(parse_line(line("<Trappeur> une insulte")), NOW)
        self.service = AdminService(
            game=FakeGame(), container=FakeContainer(), logs=FakeLogs(),
            audit=RecordingAudit(), clock=FixedClock(), container_name="minecraft",
            metrics=FakeMetrics(), updater=FakeUpdater(), ops=FakeOps(),
            notifications=FakeNotifier(), player_history=FakePlayerHistory(summary=[]),
            backup_archives=FakeBackupArchives(), bans=FakeBans(),
            temp_bans=FakeTempBans(), restart_schedule=InMemoryRestartSchedule(),
            game_events=self.store,
        )
        self.moderator = User(username="jeremy", role=Role(
            name="owner", permissions=frozenset(Permission)))
        self.watcher = User(username="curieux", role=Role(
            name="viewer", permissions=frozenset({Permission.STATUS})))

    def test_a_moderator_can_read_them(self):
        messages = self.service.player_messages(self.moderator, "Trappeur")
        self.assertEqual([m.detail for m in messages], ["une insulte"])

    def test_status_alone_is_not_enough(self):
        from domain.errors import PermissionDenied
        with self.assertRaises(PermissionDenied):
            self.service.player_messages(self.watcher, "Trappeur")

    def test_but_the_game_record_is_visible_to_everyone_who_sees_the_page(self):
        record = self.service.player_game_record(self.watcher, "Trappeur")
        self.assertIsNotNone(record)


class PagesTests(unittest.TestCase):
    """Les pages, pour de vrai : requêtes HTTP sur l'application.

    `paul` (rôle admin du jeu de rôles de test) a KICK mais PAS BAN_MANAGE :
    c'est lui qui prouve que les messages restent réservés aux comptes
    pouvant sanctionner.
    """

    def setUp(self):
        from tests.test_api import ApiTestBase

        class Base(ApiTestBase):
            def runTest(self):  # pragma: no cover - support d'instanciation
                pass

        self.base = Base()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.client = self.base.client

        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, db)
        store = SqliteGameEvents(db)
        store.record(parse_line(line("Trappeur was slain by Bucheron")), NOW, "player")
        store.record(parse_line(line("Trappeur was slain by Cave Spider")), NOW, "mob")
        store.record(parse_line(line("<Trappeur> un message douteux")), NOW)
        # Même convention que les autres ports optionnels des tests d'API.
        self.base.service._game_events = store

    def test_players_page_lists_duels(self):
        self.base.login("jeremy", "owner-pw")
        page = self.client.get("/players").text
        self.assertIn("Duels", page)
        self.assertIn("Bucheron", page)

    def test_player_page_shows_game_record(self):
        self.base.login("jeremy", "owner-pw")
        page = self.client.get("/players/Trappeur").text
        self.assertIn("Faits de jeu", page)
        self.assertIn("Morts observées", page)
        self.assertIn("Bête noire", page)

    def test_moderator_sees_messages(self):
        self.base.login("jeremy", "owner-pw")
        page = self.client.get("/players/Trappeur").text
        self.assertIn("un message douteux", page)

    def test_account_without_ban_rights_does_not_see_messages(self):
        self.base.login("paul", "admin-pw")
        page = self.client.get("/players/Trappeur").text
        self.assertIn("Faits de jeu", page)          # les stats, oui
        self.assertNotIn("un message douteux", page)  # la conversation, non


class JournalFilterTests(unittest.TestCase):
    """Cases à cocher du Journal. Le tchat est décoché par défaut : 288 des
    420 faits importés chez Jeremy, c'est lui qui noyait le journal."""

    def setUp(self):
        from tests.test_api import ApiTestBase

        class Base(ApiTestBase):
            def runTest(self):  # pragma: no cover - support d'instanciation
                pass

        self.base = Base()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.client = self.base.client

        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, db)
        store = SqliteGameEvents(db)
        store.record(parse_line(line("Trappeur was slain by Cave Spider")), NOW, "mob")
        store.record(parse_line(line("Trappeur has made the advancement [Diamonds!]")), NOW)
        store.record(parse_line(line("<Trappeur> un message bavard")), NOW)
        self.base.service._game_events = store
        self.base.login("jeremy", "owner-pw")

    def test_chat_is_hidden_by_default(self):
        page = self.client.get("/audit").text
        self.assertIn("Araignée venimeuse", page)   # la mort, oui
        self.assertIn("Diamonds!", page)            # le succès, oui
        self.assertNotIn("un message bavard", page)  # le tchat, non

    def test_chat_appears_once_ticked(self):
        page = self.client.get("/audit?igset=1&ig=death&ig=chat").text
        self.assertIn("un message bavard", page)

    def test_ticking_only_chat_hides_the_rest(self):
        page = self.client.get("/audit?igset=1&ig=chat").text
        self.assertIn("un message bavard", page)
        self.assertNotIn("Araignée venimeuse", page)

    def test_everything_can_be_unticked(self):
        """`igset` sans aucune case : « rien », et non « pas encore choisi »."""
        page = self.client.get("/audit?igset=1").text
        self.assertNotIn("Araignée venimeuse", page)
        self.assertNotIn("un message bavard", page)

    def test_checkboxes_are_wired_to_auto_submit(self):
        """Non-régression : le script masque le bouton « Filtrer » et
        n'auto-soumettait que les listes déroulantes — cocher une case ne
        faisait donc rien du tout (constaté par Jeremy sur le staging)."""
        page = self.client.get("/audit").text
        script = page[page.index("auditFilters"):]
        self.assertIn("input[type='checkbox']", script)

    def test_selection_survives_pagination_and_family_chips(self):
        page = self.client.get("/audit?igset=1&ig=chat").text
        self.assertIn("ig=chat", page)  # repris dans les liens construits


class PanelPreviewTests(unittest.TestCase):
    """Volet latéral : un aperçu (3 lignes) et le total, pas la liste entière
    — retour de Jeremy, 22/08/2026."""

    def _render(self, panel_mode: bool):
        import sys
        sys.path.insert(0, "app")
        from api.routes.common import templates
        from domain.game_events import PlayerGameRecord, RecordedEvent

        messages = [
            RecordedEvent(at=NOW - timedelta(minutes=i), kind="chat",
                          player="Trappeur", key="chat", detail=f"message {i}")
            for i in range(10)
        ]
        return templates.env.get_template("partials/player_detail_sections.html").render(
            panel_mode=panel_mode, player_name="Trappeur", summary=None, stats=None,
            sessions=[], player_messages=messages,
            game_record=PlayerGameRecord(deaths=2, messages=42),
        )

    def test_panel_shows_three_messages_and_a_link(self):
        html = self._render(panel_mode=True)
        self.assertIn("message 0", html)
        self.assertIn("message 2", html)
        self.assertNotIn("message 3", html)
        self.assertIn("Voir les 42 messages", html)   # le total, pas les 10 chargés

    def test_full_page_shows_them_all_with_tabs(self):
        html = self._render(panel_mode=False)
        self.assertIn("message 9", html)
        self.assertIn("pd-tabs", html)
        self.assertIn("#pd-messages", html)
        self.assertIn("pd-scroll", html)   # liste longue : cadre défilant

    def test_panel_has_no_tabs(self):
        self.assertNotIn("pd-tabs", self._render(panel_mode=True))
