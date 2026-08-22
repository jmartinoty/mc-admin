"""Parseur de faits de jeu : formes RÉELLES relevées dans les archives.

Les pseudos sont fictifs (Trappeur, Bucheron…) : ce fichier part en public au
squash de release : on n'y met jamais le pseudo d'un joueur réel, une
fixture ayant déjà dû être scrubée après coup pour cette raison.
"""
import unittest

from domain.game_events import (
    _DEATHS,
    KEYS_WITH_KILLER,
    GameEvent,
    entity_id,
    parse_line,
)
from domain.game_events_fr import MESSAGES, cause_label, describe, mob_name


def line(message: str) -> str:
    return f"[21:15:16] [Server thread/INFO]: {message}"


class ParseDeathTests(unittest.TestCase):
    def test_slain_by_mob(self):
        ev = parse_line(line("Trappeur was slain by Drowned"))
        self.assertEqual(ev.kind, "death")
        self.assertEqual(ev.player, "Trappeur")
        self.assertEqual(ev.killer, "Drowned")
        self.assertEqual(ev.killer_id, "minecraft:drowned")
        self.assertEqual((ev.key, ev.cause), ("slain_by", "attack"))

    def test_slain_by_player(self):
        ev = parse_line(line("Trappeur was slain by Bucheron"))
        self.assertEqual((ev.player, ev.killer), ("Trappeur", "Bucheron"))

    def test_death_with_weapon(self):
        ev = parse_line(line("Trappeur was slain by Bucheron using [Netherite Sword]"))
        self.assertEqual(ev.killer, "Bucheron")
        self.assertEqual(ev.weapon, "Netherite Sword")

    def test_environment_death_has_no_killer(self):
        ev = parse_line(line("Trappeur fell from a high place"))
        self.assertEqual((ev.key, ev.cause), ("fell_high", "fall"))
        self.assertEqual((ev.killer, ev.killer_id), ("", ""))

    def test_forms_seen_in_real_archives(self):
        # Relevées telles quelles dans les 203 archives de l'ancien serveur.
        cases = {
            "Trappeur was impaled by Drowned": ("impaled_by", "Drowned"),
            "Trappeur was speared by Bucheron": ("speared_by", "Bucheron"),
            "Trappeur was blown up by Creeper": ("blown_up_by", "Creeper"),
            "Trappeur was shot by Skeleton": ("shot_by", "Skeleton"),
            "Trappeur fell off a ladder": ("fell_ladder", ""),
            "Trappeur was doomed to fall": ("doomed_to_fall", ""),
            "Trappeur drowned": ("drowned", ""),
            "Trappeur burned to death": ("burned", ""),
            "Trappeur was obliterated by a sonically-charged shriek": ("sonic_boom", ""),
        }
        for message, (key, killer) in cases.items():
            with self.subTest(message=message):
                ev = parse_line(line(message))
                self.assertIsNotNone(ev, message)
                self.assertEqual((ev.key, ev.killer), (key, killer))

    def test_multi_word_mob_names(self):
        """Non-régression : le premier jet exigeait un pseudo pour le tueur et
        ratait 7 morts sur 64 dans les archives réelles — les mobs à nom
        composé (et les mobs renommés par étiquette) en font partie."""
        for mob in ("Cave Spider", "Iron Golem", "Magma Cube", "Piglin Brute",
                    "Elder Guardian", "Bob l'éponge"):
            with self.subTest(mob=mob):
                ev = parse_line(line(f"Trappeur was slain by {mob}"))
                self.assertIsNotNone(ev, mob)
                self.assertEqual(ev.killer, mob)

    def test_escape_forms_keep_their_killer(self):
        ev = parse_line(line("Trappeur drowned whilst trying to escape Bucheron"))
        self.assertEqual((ev.key, ev.killer), ("drowned_escaping", "Bucheron"))


class EntityIdTests(unittest.TestCase):
    """La dérivation nom -> identifiant, vérifiée sur les 57 identifiants
    réellement présents dans les statistiques du serveur."""

    def test_derives_vanilla_ids(self):
        cases = {
            "Drowned": "minecraft:drowned",
            "Cave Spider": "minecraft:cave_spider",
            "Iron Golem": "minecraft:iron_golem",
            "Piglin Brute": "minecraft:piglin_brute",
            "Elder Guardian": "minecraft:elder_guardian",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(entity_id(name), expected)

    def test_no_id_for_renamed_or_modded_entities(self):
        # Étiquette, accents, chiffres, minuscule initiale : pas d'identifiant
        # dérivable, on gardera le nom brut à l'affichage.
        for name in ("Bob l'éponge", "gentil zombie", "Zombie2", "", "Robert Le Zombie 3"):
            with self.subTest(name=name):
                self.assertEqual(entity_id(name), "")

    def test_players_get_no_entity_id(self):
        # Un pseudo passe la forme « mot capitalisé » : c'est l'appelant qui
        # tranche joueur/créature, l'identifiant dérivé n'est utilisé que pour
        # les créatures.
        ev = parse_line(line("Trappeur was slain by Bucheron"))
        self.assertEqual(ev.killer, "Bucheron")


class FalsePositiveTests(unittest.TestCase):
    """Les pièges constatés en vrai : 13 kicks dans l'historique, et le chat
    par lequel un joueur pourrait fabriquer de faux événements."""

    def test_kicks_are_not_deaths(self):
        for message in ("Trappeur was kicked due to keepalive timeout!",
                        "Trappeur was kicked for floating too long!"):
            with self.subTest(message=message):
                self.assertIsNone(parse_line(line(message)))

    def test_chat_cannot_forge_a_death(self):
        ev = parse_line(line("<Trappeur> Bucheron was slain by Trappeur"))
        self.assertEqual(ev.kind, "chat")
        self.assertEqual(ev.player, "Trappeur")

    def test_other_threads_are_ignored(self):
        self.assertIsNone(parse_line(
            "[21:15:16] [Worker-Main-3/INFO]: Trappeur was slain by Drowned"))

    def test_papermc_async_chat_thread_is_read(self):
        """Non-régression : PaperMC écrit le chat depuis un thread dédié, et
        7 messages réels d'août-septembre 2025 étaient perdus."""
        ev = parse_line("[20:49:52] [Async Chat Thread - #4/INFO]: <Trappeur> hello")
        self.assertEqual((ev.kind, ev.player, ev.detail), ("chat", "Trappeur", "hello"))

    def test_only_chat_is_trusted_on_the_chat_thread(self):
        """Une mort ne transite jamais par ce thread : l'y accepter rouvrirait
        la porte aux faux positifs que la liste blanche referme."""
        self.assertIsNone(parse_line(
            "[20:49:52] [Async Chat Thread - #4/INFO]: Trappeur was slain by Creeper"))

    def test_noise_is_ignored(self):
        for message in ("Starting minecraft server version 26.1",
                        "Trappeur joined the game",
                        "Trappeur issued server command: /gamemode creative"):
            with self.subTest(message=message):
                self.assertIsNone(parse_line(line(message)), message)

    def test_never_raises(self):
        for junk in ("", "\x00", "[", "[21:15:16] [Server thread/INFO]: ",
                     "pas une ligne de log"):
            with self.subTest(junk=junk):
                parse_line(junk)


class AdvancementAndChatTests(unittest.TestCase):
    def test_advancement(self):
        ev = parse_line(line("Trappeur has made the advancement [We Need to Go Deeper]"))
        self.assertEqual(ev.kind, "advancement")
        self.assertEqual(ev.detail, "We Need to Go Deeper")

    def test_challenge_and_goal(self):
        for verb in ("has completed the challenge", "has reached the goal"):
            with self.subTest(verb=verb):
                ev = parse_line(line(f"Trappeur {verb} [Le Bout du Monde]"))
                self.assertEqual(ev.kind, "advancement")

    def test_chat(self):
        ev = parse_line(line("<Trappeur> salut les gens"))
        self.assertEqual((ev.kind, ev.player, ev.detail),
                         ("chat", "Trappeur", "salut les gens"))


class FrenchLabelTests(unittest.TestCase):
    """Le texte se lit APRÈS le pseudo, comme « s'est connecté » dans le
    Journal — c'est le contrat de ingame_events()."""

    def test_every_message_key_is_translated(self):
        """Garde-fou d'internationalisation : ajouter un motif au parseur sans
        sa phrase laisserait un trou dans l'affichage."""
        missing = {key for _pattern, key, _cause in _DEATHS if key not in MESSAGES}
        self.assertEqual(missing, set())

    def test_mob_is_translated_player_is_not(self):
        mob = parse_line(line("Trappeur was slain by Cave Spider"))
        self.assertEqual(describe(mob), "a été tué par une Araignée venimeuse")
        pvp = parse_line(line("Trappeur was slain by Bucheron"))
        self.assertEqual(describe(pvp, killer_is_player=True),
                         "a été tué par Bucheron")

    def test_unknown_mob_keeps_its_english_name(self):
        # Mob d'une version plus récente que le dictionnaire (le serveur voit
        # déjà « parched » et « happy_ghast » en 26.1) : pas d'erreur, on
        # affiche le nom d'origine.
        ev = parse_line(line("Trappeur was slain by Parched"))
        self.assertEqual(describe(ev), "a été tué par un Parched")

    def test_renamed_mob_keeps_its_tag(self):
        ev = parse_line(line("Trappeur was slain by Bob l'éponge"))
        self.assertEqual(describe(ev), "a été tué par un Bob l'éponge")

    def test_weapon_is_appended(self):
        ev = parse_line(line("Trappeur was slain by Bucheron using [Hache]"))
        self.assertEqual(describe(ev, killer_is_player=True),
                         "a été tué par Bucheron (avec Hache)")

    def test_environment(self):
        self.assertEqual(describe(parse_line(line("Trappeur fell from a high place"))),
                         "est tombé de haut")

    def test_advancement_and_chat(self):
        adv = parse_line(line("Trappeur has made the advancement [Diamonds!]"))
        self.assertEqual(describe(adv), "a obtenu le succès « Diamonds! »")
        chat = parse_line(line("<Trappeur> coucou"))
        self.assertEqual(describe(chat), "a écrit : coucou")

    def test_unknown_key_falls_back(self):
        self.assertEqual(describe(GameEvent(kind="death", player="Trappeur")),
                         "est mort")

    def test_mob_name_helpers(self):
        self.assertEqual(mob_name("minecraft:warden", "Warden"), "un Warden")
        self.assertEqual(mob_name("", "Parched"), "un Parched")
        self.assertEqual(mob_name("", ""), "quelque chose")

    def test_cause_labels(self):
        self.assertEqual(cause_label("fall"), "Chute")
        self.assertEqual(cause_label("n_importe_quoi"), "Inconnue")

    def test_keys_with_killer_are_flagged(self):
        self.assertIn("slain_by", KEYS_WITH_KILLER)
        self.assertNotIn("fell_high", KEYS_WITH_KILLER)


if __name__ == "__main__":
    unittest.main()
