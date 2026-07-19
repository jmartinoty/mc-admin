"""Adapter PlayerHistoryPort : sessions de jeu persistées en SQLite.

Exception ASSUMÉE au choix « pas de base de données » de la V1 (CLAUDE.md §5) :
strictement scopée à l'historique des joueurs (temps de jeu, dernier vu).
Rien d'autre ne migre vers une DB — rôles/config restent YAML, audit reste
JSONL. SQLite se justifie ici par l'agrégation (somme de durées, tri par
dernière connexion) qu'un fichier plat rendrait pénible à calculer.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from domain.model import PlayerSession, PlayerSummary

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player TEXT NOT NULL,
    joined_at TEXT NOT NULL,
    left_at TEXT
)
"""

# Commandes lancées EN JEU par les joueurs (V5.x, « issued server command »
# dans latest.log) — affichées dans la famille « En jeu » du Journal.
_SCHEMA_COMMANDS = """
CREATE TABLE IF NOT EXISTS player_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player TEXT NOT NULL,
    at TEXT NOT NULL,
    command TEXT NOT NULL
)
"""


class SqlitePlayerHistory:
    def __init__(self, db_path: str, clock=None) -> None:
        self._path = db_path
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        with closing(self._connect()) as conn, conn:
            conn.execute(_SCHEMA)
            conn.execute(_SCHEMA_COMMANDS)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def record_command(self, player: str, command: str, at: datetime) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT INTO player_commands (player, at, command) VALUES (?, ?, ?)",
                         (player, at.isoformat(), command))

    def recent_commands(
        self,
        limit: int = 100,
        since: datetime | None = None,
    ) -> list[tuple[str, datetime, str]]:
        """(joueur, horodatage, commande), plus récent d'abord.

        `since` filtre EN SQL : avec l'historique importé (10 mois Nitroserv),
        tronquer d'abord puis filtrer effacerait les périodes anciennes."""
        query = "SELECT player, at, command FROM player_commands"
        params: list = []
        if since is not None:
            query += " WHERE at >= ?"
            params.append(since.isoformat())
        query += " ORDER BY at DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [(p, datetime.fromisoformat(a), c) for p, a, c in rows]

    def is_known(self, player: str) -> bool:
        """Le joueur a-t-il DÉJÀ une session enregistrée ? (filtre
        « nouveau joueur » des notifications — à interroger AVANT
        record_join.)"""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE player = ? LIMIT 1", (player,)
            ).fetchone()
        return row is not None

    def record_join(self, player: str, at: datetime) -> None:
        with closing(self._connect()) as conn, conn:
            # Ferme toute session restée ouverte pour ce joueur (ex. mc-admin
            # redémarré entre un join et son leave -> jamais fermée) avant
            # d'en ouvrir une nouvelle : jamais deux sessions ouvertes en //.
            conn.execute(
                "UPDATE sessions SET left_at = ? WHERE player = ? AND left_at IS NULL",
                (at.isoformat(), player),
            )
            conn.execute(
                "INSERT INTO sessions (player, joined_at, left_at) VALUES (?, ?, NULL)",
                (player, at.isoformat()),
            )

    def record_leave(self, player: str, at: datetime) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """UPDATE sessions SET left_at = ? WHERE id = (
                       SELECT id FROM sessions WHERE player = ? AND left_at IS NULL
                       ORDER BY joined_at DESC LIMIT 1
                   )""",
                (at.isoformat(), player),
            )

    def summary(self) -> list[PlayerSummary]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT player, joined_at, left_at FROM sessions").fetchall()

        now = self._clock()
        by_player: dict[str, dict] = {}
        for player, joined_raw, left_raw in rows:
            joined = datetime.fromisoformat(joined_raw)
            online = left_raw is None
            ended = now if online else datetime.fromisoformat(left_raw)
            duration = max((ended - joined).total_seconds(), 0.0)

            acc = by_player.setdefault(player, {"total": 0.0, "last_seen": ended, "online": False})
            acc["total"] += duration
            if online:
                acc["online"] = True
                acc["last_seen"] = now
            elif not acc["online"] and ended > acc["last_seen"]:
                acc["last_seen"] = ended

        summaries = [
            PlayerSummary(player=name, total_seconds=data["total"], last_seen=data["last_seen"], online=data["online"])
            for name, data in by_player.items()
        ]
        summaries.sort(key=lambda s: s.last_seen, reverse=True)
        return summaries

    def recent_sessions(
        self,
        limit: int = 100,
        since: datetime | None = None,
    ) -> list[PlayerSession]:
        query = "SELECT player, joined_at, left_at FROM sessions"
        params: list = []
        if since is not None:
            # Une session ENCORE OUVERTE compte toujours (elle déborde sur la
            # période) ; une session fermée compte si elle a fini dedans.
            query += " WHERE (left_at IS NULL OR left_at >= ?)"
            params.append(since.isoformat())
        query += " ORDER BY joined_at DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            PlayerSession(
                player=player,
                joined_at=datetime.fromisoformat(joined_raw),
                left_at=datetime.fromisoformat(left_raw) if left_raw else None,
            )
            for player, joined_raw, left_raw in rows
        ]
