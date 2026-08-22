"""Adapter GameEventsPort : faits de jeu persistés en SQLite.

Même base que l'historique des sessions (`players.db`) et même exception
assumée au « pas de base de données » de la V1 (CLAUDE.md §5) : c'est de
l'observation de joueurs, agrégée (morts par cause, duels), ce qu'un fichier
plat rendrait pénible.

Ce que la table stocke est TOUJOURS langue-neutre : une clé de message, une
catégorie de cause, un identifiant d'entité. Le français vit dans
`domain/game_events_fr.py` et n'entre jamais en base — c'est ce qui rendra
l'historique traduisible le jour de la version anglaise.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime

from domain.game_events import GameEvent, PlayerGameRecord, RecordedEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS game_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    player TEXT NOT NULL,
    message_key TEXT NOT NULL DEFAULT '',
    cause TEXT NOT NULL DEFAULT '',
    killer TEXT NOT NULL DEFAULT '',
    killer_id TEXT NOT NULL DEFAULT '',
    killer_type TEXT NOT NULL DEFAULT '',
    weapon TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    raw TEXT NOT NULL DEFAULT '',
    UNIQUE (at, kind, player, raw)
)
"""
# La contrainte d'unicité rend l'import rétroactif des archives REJOUABLE :
# relancer le script ne double pas l'historique. Rançon assumée : deux
# événements rigoureusement identiques du même joueur dans la même seconde
# n'en font qu'un — inatteignable en pratique (le tailer, lui, horodate à la
# microseconde).

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_game_events_at ON game_events (at)",
    "CREATE INDEX IF NOT EXISTS idx_game_events_player ON game_events (player)",
    "CREATE INDEX IF NOT EXISTS idx_game_events_kind ON game_events (kind)",
)

_COLUMNS = ("at, kind, player, message_key, cause, killer, killer_id, "
            "killer_type, weapon, detail, raw")


class SqliteGameEvents:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        with closing(self._connect()) as conn, conn:
            conn.execute(_SCHEMA)
            for index in _INDEXES:
                conn.execute(index)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def record(self, event: GameEvent, at: datetime, killer_type: str = "") -> None:
        """Enregistre un fait de jeu. `killer_type` ("player" / "mob" / "")
        est tranché par l'APPELANT, qui seul connaît la liste des joueurs :
        le parseur, lui, ne voit qu'un nom."""
        # Un tueur joueur ne porte PAS d'identifiant d'entité : le parseur en
        # dérive un dès que le nom a la forme d'un mob (« Bucheron » ->
        # `minecraft:bucheron`), ce qui polluerait les agrégats par créature de
        # la page joueur. Le verdict joueur/créature n'étant connu qu'ici, la
        # correction se fait ici.
        killer_id = "" if killer_type == "player" else event.killer_id
        with closing(self._connect()) as conn, conn:
            conn.execute(
                f"INSERT OR IGNORE INTO game_events ({_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (at.isoformat(), event.kind, event.player, event.key, event.cause,
                 event.killer, killer_id, killer_type, event.weapon,
                 event.detail, event.raw),
            )

    def recent(
        self,
        limit: int = 100,
        since: datetime | None = None,
        kinds: tuple[str, ...] | None = None,
    ) -> list[RecordedEvent]:
        """Faits les plus récents d'abord. `since` et `kinds` filtrent EN SQL :
        comme pour les commandes, tronquer avant de filtrer masquerait les
        périodes anciennes une fois l'historique importé."""
        query = f"SELECT {_COLUMNS} FROM game_events"
        conditions: list[str] = []
        params: list = []
        if since is not None:
            conditions.append("at >= ?")
            params.append(since.isoformat())
        if kinds:
            conditions.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY at DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            RecordedEvent(
                at=datetime.fromisoformat(row[0]), kind=row[1], player=row[2],
                key=row[3], cause=row[4], killer=row[5], killer_id=row[6],
                killer_type=row[7], weapon=row[8], detail=row[9], raw=row[10],
            )
            for row in rows
        ]

    def purge_chat(self, before: datetime) -> int:
        """Supprime les messages de chat antérieurs à `before` et rend leur
        nombre. Le chat est la seule famille purgée : morts et succès sont des
        faits de jeu, les messages sont de la conversation entre joueurs et
        n'ont pas forcément à être conservés indéfiniment. Inactive par défaut
        (GAME_CHAT_RETENTION_DAYS=0) : Jeremy conserve tout, pour pouvoir
        remonter les messages d'un joueur depuis sa fiche en cas d'insulte."""
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "DELETE FROM game_events WHERE kind = 'chat' AND at < ?",
                (before.isoformat(),),
            )
            return cursor.rowcount or 0

    # ---- agrégats de la fiche joueur ----

    def player_record(self, player: str) -> PlayerGameRecord:
        """Bilan d'un joueur. Les pseudos sont comparés SANS tenir compte de la
        casse : les archives importées et le tailer n'ont pas forcément la même
        graphie qu'aujourd'hui."""
        with closing(self._connect()) as conn:
            def one(query: str, *params):
                return conn.execute(query, params).fetchone()

            deaths = one("SELECT COUNT(*) FROM game_events "
                         "WHERE kind = 'death' AND player = ? COLLATE NOCASE",
                         player)[0]
            kills = one("SELECT COUNT(*) FROM game_events WHERE kind = 'death' "
                        "AND killer_type = 'player' AND killer = ? COLLATE NOCASE",
                        player)[0]
            advancements = one("SELECT COUNT(*) FROM game_events "
                               "WHERE kind = 'advancement' AND player = ? COLLATE NOCASE",
                               player)[0]
            messages = one("SELECT COUNT(*) FROM game_events "
                           "WHERE kind = 'chat' AND player = ? COLLATE NOCASE",
                           player)[0]
            bounds = one("SELECT MIN(at), MAX(at) FROM game_events "
                         "WHERE player = ? COLLATE NOCASE", player)
            top_cause = one(
                "SELECT cause, COUNT(*) AS n FROM game_events WHERE kind = 'death' "
                "AND player = ? COLLATE NOCASE AND cause <> '' "
                "GROUP BY cause ORDER BY n DESC, cause LIMIT 1", player)
            nemesis_mob = one(
                "SELECT killer_id, killer, COUNT(*) AS n FROM game_events "
                "WHERE kind = 'death' AND player = ? COLLATE NOCASE "
                "AND killer_type = 'mob' GROUP BY killer ORDER BY n DESC, killer LIMIT 1",
                player)
            nemesis_player = one(
                "SELECT killer, COUNT(*) AS n FROM game_events WHERE kind = 'death' "
                "AND player = ? COLLATE NOCASE AND killer_type = 'player' "
                "GROUP BY killer COLLATE NOCASE ORDER BY n DESC, killer LIMIT 1",
                player)
            victims = conn.execute(
                "SELECT player, COUNT(*) AS n FROM game_events WHERE kind = 'death' "
                "AND killer_type = 'player' AND killer = ? COLLATE NOCASE "
                "GROUP BY player COLLATE NOCASE ORDER BY n DESC, player", (player,)
            ).fetchall()
        return PlayerGameRecord(
            deaths=deaths, kills=kills, advancements=advancements, messages=messages,
            top_cause=(top_cause[0], top_cause[1]) if top_cause else None,
            nemesis_mob=(nemesis_mob[0], nemesis_mob[1], nemesis_mob[2]) if nemesis_mob else None,
            nemesis_player=(nemesis_player[0], nemesis_player[1]) if nemesis_player else None,
            victims=tuple((name, count) for name, count in victims),
            first_seen=datetime.fromisoformat(bounds[0]) if bounds and bounds[0] else None,
            last_seen=datetime.fromisoformat(bounds[1]) if bounds and bounds[1] else None,
        )

    def for_player(
        self,
        player: str,
        limit: int = 50,
        kinds: tuple[str, ...] | None = None,
    ) -> list[RecordedEvent]:
        """Faits d'un joueur, plus récents d'abord. Sert notamment à relire ses
        messages depuis sa fiche, en cas d'insulte signalée."""
        query = f"SELECT {_COLUMNS} FROM game_events WHERE player = ? COLLATE NOCASE"
        params: list = [player]
        if kinds:
            query += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        query += " ORDER BY at DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            RecordedEvent(
                at=datetime.fromisoformat(row[0]), kind=row[1], player=row[2],
                key=row[3], cause=row[4], killer=row[5], killer_id=row[6],
                killer_type=row[7], weapon=row[8], detail=row[9], raw=row[10],
            )
            for row in rows
        ]

    def pvp_pairs(self, limit: int = 50) -> list[tuple[str, str, int]]:
        """(tueur, victime, nombre) — matière du tableau des duels."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT killer, player, COUNT(*) AS n FROM game_events "
                "WHERE kind = 'death' AND killer_type = 'player' "
                "GROUP BY killer COLLATE NOCASE, player COLLATE NOCASE "
                "ORDER BY n DESC, killer LIMIT ?", (limit,)
            ).fetchall()
        return [(killer, victim, count) for killer, victim, count in rows]
