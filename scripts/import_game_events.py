"""Import one-shot des faits de jeu (morts, succès, chat) depuis des archives.

Usage :
    python scripts/import_game_events.py <dossier_logs> [--db players.db]
                                         [--tz Europe/Paris] [--apply]
                                         [--usercache usercache.json]

Sans --apply : dry-run — affiche le bilan sans rien écrire.

Pourquoi ce script : le tailer se positionne volontairement en FIN de fichier
au démarrage et ne relit jamais le passé (cf. player_watcher.py). Sans import,
le suivi ne commencerait qu'au jour du déploiement — alors que les archives de
Jeremy contiennent 64 morts, 68 succès et 288 messages.

REJOUABLE : l'écriture passe par SqliteGameEvents, dont la contrainte
d'unicité (date, genre, joueur, ligne brute) ignore les doublons. Relancer
l'import ne double donc pas l'historique — contrairement à
import_legacy_logs.py, qui refuse les périodes déjà couvertes.

La datation (date issue du nom de fichier, minuit franchi en cours de fichier,
fuseau local du serveur) est REPRISE de import_legacy_logs.py : cette logique
est subtile et déjà éprouvée sur les 10 mois d'archives Nitroserv.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from import_legacy_logs import _LINE, _lines, _ordered_files  # noqa: E402

from adapters.game_events import SqliteGameEvents  # noqa: E402
from domain.game_events import parse_line  # noqa: E402
from domain.game_events_fr import cause_label, describe  # noqa: E402

_JOINED = re.compile(r"^(\S+) joined the game$")

# L'heure SEULE, sans rien exiger du thread : c'est `parse_line` qui décide
# quels threads sont dignes de confiance (PaperMC écrit le chat depuis un
# thread asynchrone). Filtrer ici sur « Server thread », comme le fait le motif
# de import_legacy_logs, écartait 7 messages réels avant même l'analyse.
_CLOCK = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] ")


def known_players(folder: Path, usercache: Path | None, db: Path | None) -> set[str]:
    """Pseudos connus, en minuscules — c'est ce qui distingue « tué par
    Bucheron » de « tué par un Drowned ».

    Trois sources cumulées, de la plus sûre à la plus large :
      - tout nom ayant « joined the game » dans les archives importées : un
        joueur, sans discussion possible ;
      - `usercache.json` du serveur, si fourni ;
      - les sessions déjà enregistrées par mc-admin dans la base.
    """
    names: set[str] = set()
    for _date, _seq, path in _ordered_files(folder):
        for raw in _lines(path):
            match = _LINE.match(raw.rstrip("\n"))
            if match and (joined := _JOINED.match(match[4])):
                names.add(joined[1].lower())
    if usercache is not None and usercache.exists():
        try:
            names.update(str(e["name"]).lower()
                         for e in json.loads(usercache.read_text(encoding="utf-8"))
                         if e.get("name"))
        except (ValueError, TypeError, KeyError):
            pass  # usercache illisible : les deux autres sources suffisent
    if db is not None and db.exists():
        import sqlite3
        with sqlite3.connect(db) as conn:
            try:
                names.update(row[0].lower() for row in
                             conn.execute("SELECT DISTINCT player FROM sessions"))
            except sqlite3.DatabaseError:
                pass  # base neuve, sans table sessions
    return names


def parse_folder(folder: Path, tz: ZoneInfo, players: set[str]) -> list[tuple]:
    """(événement, date UTC, type de tueur), du plus ancien au plus récent."""
    utc = ZoneInfo("UTC")
    found: list[tuple] = []
    for file_date, _seq, path in _ordered_files(folder):
        day = file_date
        previous: tuple[int, int, int] | None = None
        for raw in _lines(path):
            match = _CLOCK.match(raw)
            if not match:
                continue
            clock = (int(match[1]), int(match[2]), int(match[3]))
            if previous is not None and clock < previous:
                day = day + timedelta(days=1)  # minuit franchi dans le fichier
            previous = clock
            event = parse_line(raw)
            if event is None:
                continue
            moment = datetime(day.year, day.month, day.day, *clock, tzinfo=tz)
            killer_type = ""
            if event.kind == "death" and event.killer:
                killer_type = "player" if event.killer.lower() in players else "mob"
            found.append((event, moment.astimezone(utc), killer_type))
    return found


def summarize(found: list[tuple]) -> str:
    kinds = Counter(event.kind for event, _at, _t in found)
    causes = Counter(event.cause for event, _at, _t in found if event.kind == "death")
    pvp = [(event.player, event.killer) for event, _at, t in found if t == "player"]
    lines = [
        f"{len(found)} faits de jeu : "
        + ", ".join(f"{n} {k}" for k, n in kinds.most_common()),
    ]
    if found:
        first, last = found[0][1], found[-1][1]
        lines.append(f"période : {first:%d/%m/%Y} -> {last:%d/%m/%Y}")
    if causes:
        lines.append("morts par cause : "
                     + ", ".join(f"{cause_label(c)} {n}" for c, n in causes.most_common()))
    lines.append(f"duels entre joueurs : {len(pvp)}"
                 + ("" if not pvp else " — " + ", ".join(f"{k} a tué {v}" for v, k in pvp)))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="dossier de logs (*.log.gz, latest.log)")
    parser.add_argument("--db", type=Path, default=Path("players.db"))
    parser.add_argument("--tz", default="Europe/Paris",
                        help="fuseau du serveur d'origine (les logs n'en portent pas)")
    parser.add_argument("--usercache", type=Path, default=None)
    parser.add_argument("--apply", action="store_true",
                        help="écrit réellement ; sans lui, simple bilan")
    args = parser.parse_args(argv)

    if not args.folder.is_dir():
        print(f"dossier introuvable : {args.folder}", file=sys.stderr)
        return 2

    players = known_players(args.folder, args.usercache, args.db)
    found = parse_folder(args.folder, ZoneInfo(args.tz), players)
    print(f"joueurs reconnus : {len(players)}")
    print(summarize(found))
    if not args.apply:
        print("\n(dry-run — relancer avec --apply pour écrire)")
        for event, at, killer_type in found[-8:]:
            print(f"   {at:%d/%m %H:%M}  {event.player} "
                  f"{describe(event, killer_type == 'player')}")
        return 0

    store = SqliteGameEvents(str(args.db))
    before = len(store.recent(limit=10_000_000))
    for event, at, killer_type in found:
        store.record(event, at, killer_type)
    after = len(store.recent(limit=10_000_000))
    print(f"\nécrit dans {args.db} : {after - before} nouveaux "
          f"({len(found) - (after - before)} déjà présents, ignorés)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
