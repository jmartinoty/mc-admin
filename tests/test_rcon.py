"""Tests de l'adapter RconGame (fabrique de connexion factice) et du codec RCON."""
from __future__ import annotations

import struct
import unittest

from adapters.rcon import RconConnection, RconGame, decode_packet, encode_packet, parse_player_list
from domain.errors import ServerUnavailable


class TestPacketCodec(unittest.TestCase):
    def test_encode_layout(self):
        pkt = encode_packet(7, 2, "list")
        (length,) = struct.unpack("<i", pkt[:4])
        self.assertEqual(length, len(pkt) - 4)          # longueur = corps sans le préfixe
        self.assertEqual(pkt[-2:], b"\x00\x00")         # terminaison double nul
        req_id, ptype = struct.unpack("<ii", pkt[4:12])
        self.assertEqual((req_id, ptype), (7, 2))

    def test_roundtrip(self):
        pkt = encode_packet(42, 0, "There are 0 of a max of 20 players online:")
        req_id, ptype, payload = decode_packet(pkt[4:])
        self.assertEqual(req_id, 42)
        self.assertEqual(ptype, 0)
        self.assertEqual(payload, "There are 0 of a max of 20 players online:")

    def test_decode_tolerates_non_utf8(self):
        body = struct.pack("<ii", 1, 0) + b"\xff\xfebad" + b"\x00\x00"
        _, _, payload = decode_packet(body)
        self.assertIn("bad", payload)                    # pas d'exception


class FakeSocket:
    """Fausse socket : rejoue des paquets pré-encodés, enregistre les envois."""

    def __init__(self, packets: list[bytes]):
        self._rx = b"".join(packets)
        self.sent = b""

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, n: int) -> bytes:
        chunk, self._rx = self._rx[:n], self._rx[n:]
        return chunk

    def close(self) -> None:
        pass


class TestMultiPacketResponse(unittest.TestCase):
    """Un paquet PLEIN (4096 octets de payload) annonce des fragments (même id).
    La lecture est alors bornée par une commande sentinelle vide, envoyée APRÈS
    la première réponse (vanilla ferme la connexion si deux paquets partagent
    un segment TCP). Réponse courte = un seul aller-retour, pas de sentinelle."""

    FULL = "a" * 4096  # payload d'un fragment plein

    def _conn(self, packets):
        conn = RconConnection("minecraft", 25575, "pw")
        conn._sock = FakeSocket(packets)
        return conn

    def test_fragments_are_reassembled(self):
        # ids : commande = 1, sentinelle = 2 (compteur neuf, pas d'auth ici)
        conn = self._conn([
            encode_packet(1, 0, self.FULL),
            encode_packet(1, 0, "…suite"),
            encode_packet(2, 0, "Unknown command"),      # réponse à la sentinelle
        ])
        self.assertEqual(conn.command("help gamerule"), self.FULL + "…suite")
        self.assertEqual(conn._sock.sent,
                         encode_packet(1, 2, "help gamerule") + encode_packet(2, 2, ""))

    def test_exactly_full_single_packet_costs_one_sentinel(self):
        # Réponse d'exactement 4096 octets : indistinguable d'un 1er fragment,
        # la sentinelle tranche (un aller-retour de plus, pas de blocage).
        conn = self._conn([
            encode_packet(1, 0, self.FULL),
            encode_packet(2, 0, ""),
        ])
        self.assertEqual(conn.command("seed"), self.FULL)

    def test_short_response_sends_no_sentinel(self):
        conn = self._conn([
            encode_packet(1, 0, "There are 0 of a max of 20 players online:"),
        ])
        self.assertEqual(conn.command("list"), "There are 0 of a max of 20 players online:")
        self.assertEqual(conn._sock.sent, encode_packet(1, 2, "list"))


class FakeRcon:
    """Faux client mcrcon : context manager avec .command()."""

    def __init__(self, responses=None, enter_error=None):
        self.responses = responses or {}
        self.enter_error = enter_error
        self.commands: list[str] = []

    def __enter__(self):
        if self.enter_error is not None:
            raise self.enter_error
        return self

    def __exit__(self, *exc):
        return False

    def command(self, cmd):
        self.commands.append(cmd)
        return self.responses.get(cmd, "")


def _game(fake):
    return RconGame("minecraft", 25575, "pw", connect=lambda: fake)


class TestParsePlayerList(unittest.TestCase):
    def test_two_players(self):
        out = "There are 2 of a max of 1000 players online: alice, bob"
        self.assertEqual([p.name for p in parse_player_list(out)], ["alice", "bob"])

    def test_zero_players(self):
        out = "There are 0 of a max of 20 players online:"
        self.assertEqual(parse_player_list(out), [])

    def test_empty_or_garbage(self):
        self.assertEqual(parse_player_list(""), [])
        self.assertEqual(parse_player_list("no colon here"), [])


class TestRconGame(unittest.TestCase):
    def test_list_players(self):
        fake = FakeRcon({"list": "There are 1 of a max of 5 players online: neo"})
        players = _game(fake).list_players()
        self.assertEqual([p.name for p in players], ["neo"])
        self.assertEqual(fake.commands, ["list"])

    def test_save_all_issues_command(self):
        fake = FakeRcon()
        _game(fake).save_all()
        self.assertEqual(fake.commands, ["save-all"])

    def test_raw_returns_output(self):
        fake = FakeRcon({"whitelist add toto": "Added toto to the whitelist"})
        self.assertEqual(_game(fake).raw("whitelist add toto"), "Added toto to the whitelist")

    def test_connection_error_becomes_server_unavailable(self):
        fake = FakeRcon(enter_error=ConnectionRefusedError("down"))
        with self.assertRaises(ServerUnavailable):
            _game(fake).list_players()


class TestWhitelistCommands(unittest.TestCase):
    def test_whitelist_list_parses_names(self):
        fake = FakeRcon({"whitelist list": "There are 2 whitelisted player(s): alice, bob"})
        self.assertEqual(_game(fake).whitelist_list(), ["alice", "bob"])

    def test_whitelist_empty(self):
        fake = FakeRcon({"whitelist list": "There are no whitelisted players"})
        self.assertEqual(_game(fake).whitelist_list(), [])

    def test_whitelist_add_and_remove_send_expected_commands(self):
        fake = FakeRcon({
            "whitelist add toto": "Added toto to the whitelist",
            "whitelist remove toto": "Removed toto from the whitelist",
        })
        game = _game(fake)
        self.assertEqual(game.whitelist_add("toto"), "Added toto to the whitelist")
        self.assertEqual(game.whitelist_remove("toto"), "Removed toto from the whitelist")
        self.assertEqual(fake.commands, ["whitelist add toto", "whitelist remove toto"])


class TestOpCommands(unittest.TestCase):
    def test_op_and_deop_send_expected_commands(self):
        fake = FakeRcon({
            "op toto": "Made toto a server operator",
            "deop toto": "Made toto no longer a server operator",
        })
        game = _game(fake)
        self.assertEqual(game.op("toto"), "Made toto a server operator")
        self.assertEqual(game.deop("toto"), "Made toto no longer a server operator")
        self.assertEqual(fake.commands, ["op toto", "deop toto"])


class TestKickBanCommands(unittest.TestCase):
    def test_kick_with_and_without_reason(self):
        fake = FakeRcon({
            "kick toto": "Kicked toto",
            "kick toto AFK": "Kicked toto",
        })
        game = _game(fake)
        self.assertEqual(game.kick("toto"), "Kicked toto")
        self.assertEqual(game.kick("toto", "AFK"), "Kicked toto")
        self.assertEqual(fake.commands, ["kick toto", "kick toto AFK"])

    def test_ban_and_pardon(self):
        fake = FakeRcon({
            "ban toto Triche": "Banned toto",
            "pardon toto": "Unbanned toto",
        })
        game = _game(fake)
        self.assertEqual(game.ban("toto", "Triche"), "Banned toto")
        self.assertEqual(game.pardon("toto"), "Unbanned toto")
        self.assertEqual(fake.commands, ["ban toto Triche", "pardon toto"])


if __name__ == "__main__":
    unittest.main()


class TestGameSettingsCommands(unittest.TestCase):
    def test_get_difficulty_parses_response(self):
        fake = FakeRcon({"difficulty": "The difficulty is Normal"})
        self.assertEqual(_game(fake).get_difficulty(), "normal")

    def test_get_difficulty_unexpected_response_is_none(self):
        fake = FakeRcon({"difficulty": "???"})
        self.assertIsNone(_game(fake).get_difficulty())

    def test_get_gamerule_parses_bool(self):
        fake = FakeRcon({"gamerule keep_inventory": "Gamerule keep_inventory is currently set to: false"})
        self.assertIs(_game(fake).get_gamerule("keep_inventory"), False)

    def test_setters_build_expected_commands(self):
        fake = FakeRcon({
            "difficulty hard": "ok", "gamerule keep_inventory true": "ok",
            "weather clear": "ok", "time set day": "ok",
        })
        game = _game(fake)
        game.set_difficulty("hard")
        game.set_gamerule("keep_inventory", True)
        game.set_weather("clear")
        game.set_time("day")
        self.assertEqual(fake.commands, [
            "difficulty hard", "gamerule keep_inventory true", "weather clear", "time set day",
        ])
