"""Portier de maintenance exécuté par le conteneur one-shot mc-doorman.

Pendant qu'un serveur Minecraft est volontairement arrêté (maintenance,
restauration), ce mini-serveur stdlib occupe son endpoint réseau — ici l'IP
statique que le tunnel playit cible en dur (spike du 19/07/2026 : playit
appelle 172.29.0.10:25565, pas le nom docker) — et parle juste assez le
protocole Minecraft pour rester honnête avec les joueurs :

  - état « status » (liste des serveurs) : MOTD de maintenance ;
  - état « login » : refus de connexion avec le message de la consigne.

La consigne (MOTD + message de refus) est écrite par mc-admin dans
`DOORMAN_CONFIG_FILE` AVANT le démarrage du conteneur (pattern
backup-profile.env / restore-target : le one-shot ne reçoit jamais de
paramètre par l'API Docker). Fichier absent ou illisible = messages par
défaut : le portier ne refuse jamais de démarrer pour une consigne.

Sûr par construction : si ce processus meurt, l'IP est libérée et les
joueurs voient « connection refused » — jamais un serveur bloqué. Et si le
vrai serveur tourne encore, Docker refuse le démarrage du portier (conflit
d'adresse) : le portier ne peut pas voler l'IP d'un serveur vivant.

Vérifié au spike : playit n'appelle la cible locale qu'après avoir sniffé un
handshake valide, puis le REJOUE intégralement — le portier reçoit donc le
même premier paquet qu'un vrai serveur, en tunnel comme en LAN.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import struct
import sys
import threading

# Bornes anti-abus : le portier lit des octets hostiles par définition
# (port exposé aux joueurs). Tout dépassement = déconnexion silencieuse.
_MAX_PACKET_BYTES = 32768
_MAX_STRING_BYTES = 1024
_CLIENT_TIMEOUT_SECONDS = 10.0

_DEFAULT_MOTD = "Serveur en maintenance"
_DEFAULT_KICK = "Le serveur est en maintenance. Reviens un peu plus tard !"


class ProtocolError(ValueError):
    """Octets reçus incompatibles avec le protocole attendu."""


def load_consigne(path: str) -> tuple[str, str]:
    """Retourne (motd, kick). Ne lève jamais : une consigne illisible ne doit
    pas empêcher le portier d'occuper l'endpoint (c'est sa vraie mission)."""
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        motd = str(payload.get("motd") or "").strip()
        kick = str(payload.get("kick") or "").strip()
        return motd or _DEFAULT_MOTD, kick or _DEFAULT_KICK
    except Exception:  # noqa: BLE001 — défauts assumés, cf. docstring
        return _DEFAULT_MOTD, _DEFAULT_KICK


# ---- primitives protocole (varint + chaînes préfixées) ----

def _read_exact(sock: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ProtocolError("connexion fermée en cours de paquet")
        data += chunk
    return data


def _read_varint(sock: socket.socket) -> int:
    value = 0
    for position in range(5):  # varint MC = 32 bits, 5 octets max
        byte = _read_exact(sock, 1)[0]
        value |= (byte & 0x7F) << (7 * position)
        if not byte & 0x80:
            return value
    raise ProtocolError("varint trop long")


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _read_packet(sock: socket.socket) -> bytes:
    length = _read_varint(sock)
    if not 0 < length <= _MAX_PACKET_BYTES:
        raise ProtocolError(f"taille de paquet hors bornes : {length}")
    return _read_exact(sock, length)


def _send_packet(sock: socket.socket, payload: bytes) -> None:
    sock.sendall(_encode_varint(len(payload)) + payload)


class _PacketReader:
    """Lecture séquentielle du CORPS d'un paquet déjà reçu."""

    def __init__(self, payload: bytes) -> None:
        self._data = payload
        self._offset = 0

    def varint(self) -> int:
        value = 0
        for position in range(5):
            if self._offset >= len(self._data):
                raise ProtocolError("paquet tronqué (varint)")
            byte = self._data[self._offset]
            self._offset += 1
            value |= (byte & 0x7F) << (7 * position)
            if not byte & 0x80:
                return value
        raise ProtocolError("varint trop long")

    def string(self) -> str:
        length = self.varint()
        if not 0 <= length <= _MAX_STRING_BYTES:
            raise ProtocolError(f"chaîne hors bornes : {length}")
        if self._offset + length > len(self._data):
            raise ProtocolError("paquet tronqué (chaîne)")
        raw = self._data[self._offset:self._offset + length]
        self._offset += length
        return raw.decode("utf-8", errors="replace")

    def unsigned_short(self) -> int:
        if self._offset + 2 > len(self._data):
            raise ProtocolError("paquet tronqué (port)")
        value = struct.unpack_from(">H", self._data, self._offset)[0]
        self._offset += 2
        return value


def _chat_json(text: str) -> bytes:
    encoded = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    return _encode_varint(len(encoded)) + encoded


def _status_payload(motd: str, client_protocol: int) -> bytes:
    # Le protocole du CLIENT est renvoyé tel quel : le serveur apparaît
    # « compatible » dans la liste et le joueur lit le MOTD au lieu d'un
    # « version incompatible » — c'est au login qu'il reçoit l'explication.
    status = {
        "version": {"name": "Maintenance", "protocol": client_protocol},
        "players": {"online": 0, "max": 0},
        "description": {"text": motd},
    }
    encoded = json.dumps(status, ensure_ascii=False).encode("utf-8")
    return b"\x00" + _encode_varint(len(encoded)) + encoded


def handle_client(sock: socket.socket, motd: str, kick: str) -> None:
    """Une conversation complète avec un client. Toute anomalie ferme la
    connexion sans bruit : un portier ne plante jamais pour un octet hostile."""
    try:
        sock.settimeout(_CLIENT_TIMEOUT_SECONDS)
        first = _read_packet(sock)
        reader = _PacketReader(first)
        if reader.varint() != 0x00:  # seul le handshake ouvre une session
            return
        client_protocol = reader.varint()
        reader.string()  # adresse annoncée par le client — sans intérêt ici
        reader.unsigned_short()
        next_state = reader.varint()

        if next_state == 1:  # status : MOTD + ping/pong
            request = _PacketReader(_read_packet(sock))
            if request.varint() != 0x00:
                return
            _send_packet(sock, _status_payload(motd, client_protocol))
            ping = _read_packet(sock)  # id 0x01 + horodatage int64, renvoyé tel quel
            if ping[:1] == b"\x01" and len(ping) == 9:
                _send_packet(sock, ping)
            return

        if next_state in (2, 3):  # login (ou transfert 1.20.5+) : refus expliqué
            _send_packet(sock, b"\x00" + _chat_json(kick))
            return
    except (ProtocolError, OSError):
        pass
    finally:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()


class DoormanServer:
    def __init__(self, host: str, port: int, motd: str, kick: str) -> None:
        self._motd = motd
        self._kick = kick
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((host, port))
        self._listener.listen(16)
        self._closed = threading.Event()

    @property
    def port(self) -> int:
        return self._listener.getsockname()[1]

    def serve_forever(self) -> None:
        while not self._closed.is_set():
            try:
                client, _peer = self._listener.accept()
            except OSError:
                return  # listener fermé (arrêt demandé)
            threading.Thread(
                target=handle_client,
                args=(client, self._motd, self._kick),
                daemon=True,
            ).start()

    def close(self) -> None:
        self._closed.set()
        try:
            self._listener.close()
        except OSError:
            pass


def main() -> int:
    config_file = os.environ.get("DOORMAN_CONFIG_FILE", "/consigne/doorman.json")
    port = int(os.environ.get("DOORMAN_PORT", "25565"))
    motd, kick = load_consigne(config_file)

    server = DoormanServer("0.0.0.0", port, motd, kick)

    def _terminate(_signum, _frame):
        server.close()

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)

    print(f"[doorman] en poste sur :{port} — motd={motd!r}", flush=True)
    server.serve_forever()
    print("[doorman] relevé de sa garde", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
