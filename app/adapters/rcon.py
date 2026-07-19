"""Adapter GamePort : commandes in-game via RCON.

Implémentation MAISON du protocole Source RCON plutôt que la lib `mcrcon` :
celle-ci arme ses timeouts avec signal.SIGALRM, ce qui lève « signal only works
in main thread » hors du main thread — or FastAPI exécute les routes sync dans
un threadpool. Ici : sockets + settimeout, stdlib uniquement, thread-safe.

Une connexion est ouverte par commande (simple, évite les sockets périmés).
Le port RCON n'est jamais publié (cf. CLAUDE.md §7.2). Les tests injectent une
fabrique `connect` factice (context manager avec .command()).
"""
from __future__ import annotations

import re
import socket
import struct

from domain.errors import ServerUnavailable
from domain.model import Player

# Erreurs réseau traduites en ServerUnavailable → le status se dégrade sans planter.
_UNAVAILABLE = (ConnectionError, TimeoutError, OSError)

# Types de paquets du protocole Source RCON.
_AUTH = 3   # SERVERDATA_AUTH
_EXEC = 2   # SERVERDATA_EXECCOMMAND

# Taille max du payload d'un fragment de réponse (constatée en réel : vanilla
# découpe les réponses longues en paquets de 4096 octets portant le même id).
_MAX_PAYLOAD = 4096


def encode_packet(req_id: int, ptype: int, payload: str) -> bytes:
    """<longueur int32 LE> <id int32> <type int32> <payload utf-8> <2 octets nuls>."""
    body = struct.pack("<ii", req_id, ptype) + payload.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(body)) + body


def decode_packet(body: bytes) -> tuple[int, int, str]:
    """Décode un corps de paquet (champ longueur exclu) -> (id, type, payload)."""
    req_id, ptype = struct.unpack("<ii", body[:8])
    return req_id, ptype, body[8:-2].decode("utf-8", errors="replace")


class RconConnection:
    """Connexion RCON minimaliste (context manager). Auth à l'entrée."""

    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0) -> None:
        self._host, self._port, self._password, self._timeout = host, port, password, timeout
        self._sock: socket.socket | None = None
        self._next_id = 0

    def __enter__(self) -> "RconConnection":
        self._sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        self._sock.settimeout(self._timeout)
        req_id, _, _ = self._roundtrip(_AUTH, self._password)
        if req_id == -1:  # convention du protocole : id -1 = auth refusée
            raise ServerUnavailable("authentification RCON refusée (vérifier RCON_PASSWORD)")
        return self

    def __exit__(self, *exc) -> bool:
        if self._sock is not None:
            self._sock.close()
        return False

    def _recv_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("connexion RCON fermée par le serveur")
            data += chunk
        return data

    def _roundtrip(self, ptype: int, payload: str) -> tuple[int, int, str]:
        self._next_id += 1
        self._sock.sendall(encode_packet(self._next_id, ptype, payload))
        (length,) = struct.unpack("<i", self._recv_exact(4))
        return decode_packet(self._recv_exact(length))

    def _recv_packet(self) -> tuple[int, int, str, int]:
        """Reçoit un paquet -> (id, type, payload, taille du payload en OCTETS).

        La taille sert à détecter un fragment plein : len(payload) en caractères
        mentirait sur de l'UTF-8 multi-octets."""
        (length,) = struct.unpack("<i", self._recv_exact(4))
        body = self._recv_exact(length)
        req_id, ptype, payload = decode_packet(body)
        return req_id, ptype, payload, len(body) - 10  # 8 (id+type) + 2 nuls

    def command(self, cmd: str) -> str:
        self._next_id += 1
        self._sock.sendall(encode_packet(self._next_id, _EXEC, cmd))
        _, _, payload, size = self._recv_packet()
        if size < _MAX_PAYLOAD:
            return payload
        # Paquet plein = réponse fragmentée, la suite arrive (ex. `help
        # gamerule`, constaté tronqué en réel). Une commande sentinelle vide
        # borne la lecture : sa réponse (id suivant) marque la fin des
        # fragments. Envoyée APRÈS la première réponse : vanilla FERME la
        # connexion si deux paquets arrivent dans le même segment TCP
        # (constaté en réel).
        parts = [payload]
        self._next_id += 1
        end_id = self._next_id
        self._sock.sendall(encode_packet(end_id, _EXEC, ""))
        while True:
            req_id, _, payload, _ = self._recv_packet()
            if req_id == end_id:
                return "".join(parts)
            parts.append(payload)


def parse_name_list(output: str) -> list[str]:
    """Parse une sortie « préfixe: nom1, nom2 » (list, whitelist list…).

    Exemples :
      "There are 2 of a max of 1000 players online: alice, bob"  -> [alice, bob]
      "There are 0 of a max of 20 players online:"               -> []
      "There are no whitelisted players"                          -> []  (pas de ':')
    Le préfixe ne contient pas de ':' → on coupe sur le premier ':'.
    """
    if not output or ":" not in output:
        return []
    _, _, names = output.partition(":")
    return [n.strip() for n in names.split(",") if n.strip()]


def parse_player_list(output: str) -> list[Player]:
    """Parse la sortie de la commande `list` en objets Player."""
    return [Player(name=n) for n in parse_name_list(output)]


class RconGame:
    def __init__(self, host: str, port: int, password: str, connect=None) -> None:
        self._host = host
        self._port = int(port)
        self._password = password
        self._connect = connect or self._default_connect

    def _default_connect(self) -> RconConnection:
        return RconConnection(self._host, self._port, self._password)

    def _command(self, command: str) -> str:
        try:
            with self._connect() as conn:
                return conn.command(command)
        except _UNAVAILABLE as exc:
            raise ServerUnavailable(f"RCON injoignable ({self._host}:{self._port})") from exc

    def list_players(self) -> list[Player]:
        return parse_player_list(self._command("list"))

    def save_all(self) -> None:
        self._command("save-all")

    def say(self, message: str) -> None:
        self._command(f"say {message}")

    def raw(self, command: str) -> str:
        return self._command(command)

    def stop(self) -> None:
        self._command("stop")

    # Whitelist : les pseudos sont déjà validés par le domaine ([A-Za-z0-9_]{3,16}).
    def whitelist_list(self) -> list[str]:
        return parse_name_list(self._command("whitelist list"))

    def whitelist_add(self, player_name: str) -> str:
        return self._command(f"whitelist add {player_name}")

    def whitelist_remove(self, player_name: str) -> str:
        return self._command(f"whitelist remove {player_name}")

    def op(self, player_name: str) -> str:
        return self._command(f"op {player_name}")

    def deop(self, player_name: str) -> str:
        return self._command(f"deop {player_name}")

    def kick(self, player_name: str, reason: str = "") -> str:
        cmd = f"kick {player_name} {reason}" if reason else f"kick {player_name}"
        return self._command(cmd)

    def ban(self, player_name: str, reason: str = "") -> str:
        cmd = f"ban {player_name} {reason}" if reason else f"ban {player_name}"
        return self._command(cmd)

    def pardon(self, player_name: str) -> str:
        return self._command(f"pardon {player_name}")

    # Réglages de jeu (V5). Les valeurs arrivent déjà validées par le domaine.
    def get_difficulty(self) -> str | None:
        # "The difficulty is Normal" -> "normal"
        output = self._command("difficulty")
        _, _, level = output.rpartition(" ")
        level = level.strip().strip(".").lower()
        return level if level in ("peaceful", "easy", "normal", "hard") else None

    def set_difficulty(self, level: str) -> str:
        return self._command(f"difficulty {level}")

    def get_gamerule(self, name: str) -> bool | None:
        # "Gamerule keepInventory is currently set to: false" -> False
        output = self._command(f"gamerule {name}").strip().lower()
        _, _, value = output.rpartition(" ")
        if value in ("true", "false"):
            return value == "true"
        return None

    def set_gamerule(self, name: str, value) -> str:
        if isinstance(value, bool):
            value = "true" if value else "false"
        return self._command(f"gamerule {name} {value}")

    def list_gamerule_names(self) -> list[str]:
        """Noms des gamerules supportées par CE serveur (source : help gamerule).
        Sert de liste blanche dynamique au domaine."""
        output = self._command("help gamerule")
        names = re.findall(r"/gamerule (?!minecraft:)([a-z0-9_]+)", output)
        return sorted(set(names))

    def get_gamerules(self, names: list[str]) -> dict[str, str]:
        """Valeurs brutes de plusieurs gamerules sur UNE SEULE connexion RCON
        (la page Réglages en lit ~50 : une connexion par lecture serait lent)."""
        values: dict[str, str] = {}
        try:
            with self._connect() as conn:
                for name in names:
                    output = conn.command(f"gamerule {name}").strip()
                    _, _, value = output.rpartition(" ")
                    values[name] = value
        except _UNAVAILABLE as exc:
            raise ServerUnavailable(f"RCON injoignable ({self._host}:{self._port})") from exc
        return values

    def set_weather(self, kind: str) -> str:
        return self._command(f"weather {kind}")

    def set_time(self, moment: str) -> str:
        return self._command(f"time set {moment}")
