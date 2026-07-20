"""Tests du portier de maintenance (doorman_server) — vraies sockets locales.

Le portier écoute sur un port éphémère (port 0) : chaque test parle le vrai
protocole d'un client Minecraft (handshake varint, status, login) sans rien
mocker — c'est précisément la surface exposée aux joueurs.
"""
from __future__ import annotations

import json
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path

import doorman_server
from doorman_server import (
    DoormanServer,
    _encode_varint,
    _read_varint,
    load_consigne,
)


def _mc_string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return _encode_varint(len(raw)) + raw


def _handshake(next_state: int, protocol: int = 773) -> bytes:
    payload = (
        b"\x00"
        + _encode_varint(protocol)
        + _mc_string("mc.example.test")
        + struct.pack(">H", 25565)
        + _encode_varint(next_state)
    )
    return _encode_varint(len(payload)) + payload


def _read_packet(sock: socket.socket) -> bytes:
    length = _read_varint(sock)
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise AssertionError("connexion fermée en cours de paquet")
        data += chunk
    return data


def _read_prefixed_json(payload: bytes) -> dict:
    # corps = id 0x00 + varint longueur + JSON
    assert payload[0] == 0x00
    offset = 1
    length = 0
    for position in range(5):
        byte = payload[offset]
        offset += 1
        length |= (byte & 0x7F) << (7 * position)
        if not byte & 0x80:
            break
    return json.loads(payload[offset:offset + length].decode("utf-8"))


class DoormanServerTest(unittest.TestCase):
    def setUp(self):
        self.server = DoormanServer(
            "127.0.0.1", 0, "Maintenance — retour 22:30", "Reviens à 22:30 !"
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.close()
        self.thread.join(timeout=2)

    def _connect(self) -> socket.socket:
        sock = socket.create_connection(("127.0.0.1", self.server.port), timeout=5)
        sock.settimeout(5)
        return sock

    def test_status_renvoie_le_motd_et_le_protocole_du_client(self):
        with self._connect() as sock:
            sock.sendall(_handshake(next_state=1, protocol=999))
            sock.sendall(b"\x01\x00")  # status request
            status = _read_prefixed_json(_read_packet(sock))
        self.assertEqual(status["description"]["text"], "Maintenance — retour 22:30")
        self.assertEqual(status["version"]["name"], "Maintenance")
        self.assertEqual(status["version"]["protocol"], 999)  # « compatible » affiché
        self.assertEqual(status["players"], {"online": 0, "max": 0})

    def test_ping_rejoue_le_pong_a_l_identique(self):
        with self._connect() as sock:
            sock.sendall(_handshake(next_state=1))
            sock.sendall(b"\x01\x00")
            _read_packet(sock)  # status
            ping = b"\x01" + struct.pack(">q", 123456789)
            sock.sendall(_encode_varint(len(ping)) + ping)
            self.assertEqual(_read_packet(sock), ping)

    def test_login_recoit_le_message_de_refus(self):
        with self._connect() as sock:
            sock.sendall(_handshake(next_state=2))
            disconnect = _read_prefixed_json(_read_packet(sock))
        self.assertEqual(disconnect, {"text": "Reviens à 22:30 !"})

    def test_transfert_1_20_5_recoit_aussi_le_refus(self):
        with self._connect() as sock:
            sock.sendall(_handshake(next_state=3))
            disconnect = _read_prefixed_json(_read_packet(sock))
        self.assertEqual(disconnect, {"text": "Reviens à 22:30 !"})

    def test_octets_hostiles_ferment_sans_tuer_le_portier(self):
        for hostile in (b"\xfe\x01", b"\xff" * 64, b"\x05GET /"):
            with self._connect() as sock:
                sock.sendall(hostile)
                # La connexion doit se fermer (b"" = EOF) sans réponse.
                sock.settimeout(5)
                try:
                    self.assertEqual(sock.recv(64), b"")
                except (TimeoutError, ConnectionError):
                    pass  # fermeture abrupte acceptée aussi
        # Le portier répond toujours après les hostilités :
        self.test_login_recoit_le_message_de_refus()

    def test_paquet_annonce_trop_grand_est_refuse(self):
        with self._connect() as sock:
            sock.sendall(_encode_varint(10_000_000))
            try:
                self.assertEqual(sock.recv(64), b"")
            except (TimeoutError, ConnectionError):
                pass
        self.test_status_renvoie_le_motd_et_le_protocole_du_client()


class ConsigneTest(unittest.TestCase):
    def test_fichier_absent_donne_les_messages_par_defaut(self):
        motd, kick = load_consigne("/nulle/part/doorman.json")
        self.assertEqual(motd, doorman_server._DEFAULT_MOTD)
        self.assertEqual(kick, doorman_server._DEFAULT_KICK)

    def test_fichier_valide_est_lu(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "doorman.json"
            path.write_text(
                json.dumps({"motd": "Retour 23:00", "kick": "À 23:00 !"}),
                encoding="utf-8",
            )
            self.assertEqual(load_consigne(str(path)), ("Retour 23:00", "À 23:00 !"))

    def test_json_corrompu_ou_champs_vides_donnent_les_defauts(self):
        with tempfile.TemporaryDirectory() as tmp:
            corrupt = Path(tmp) / "corrupt.json"
            corrupt.write_text("{pas du json", encoding="utf-8")
            self.assertEqual(
                load_consigne(str(corrupt)),
                (doorman_server._DEFAULT_MOTD, doorman_server._DEFAULT_KICK),
            )
            empty = Path(tmp) / "empty.json"
            empty.write_text(json.dumps({"motd": "  ", "kick": ""}), encoding="utf-8")
            self.assertEqual(
                load_consigne(str(empty)),
                (doorman_server._DEFAULT_MOTD, doorman_server._DEFAULT_KICK),
            )


if __name__ == "__main__":
    unittest.main()
