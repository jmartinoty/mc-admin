"""Import one-shot d'archives de logs Minecraft dans l'historique joueurs.

Usage :
    python scripts/import_legacy_logs.py <dossier_logs> [--db players.db]
                                         [--tz Europe/Paris] [--apply]

Sans --apply : dry-run — affiche le bilan (sessions par joueur, commandes)
sans rien écrire. Avec --apply : insère dans les tables `sessions` et
`player_commands` (schéma de adapters/player_history.py), en UTC isoformat
comme le tailer.

Conçu pour la migration Nitroserv (PaperMC, 2025-08 → 2026-06) mais
générique : tout dossier de `*.log.gz` vanilla/Paper + `latest.log`.

Choix assumés :
- L'heure des lignes est en fuseau LOCAL du serveur d'origine (--tz,
  défaut Europe/Paris pour un hébergeur français) ; les durées ne dépendent
  pas de ce choix, seuls les horodatages absolus en dépendent.
- La date vient du NOM de fichier (AAAA-MM-JJ-N.log.gz) ; un fichier peut
  déborder sur le(s) jour(s) suivant(s) : une heure qui recule = minuit
  franchi (+1 jour). `latest.log` : date de modification du fichier.
- « Starting minecraft server » = redémarrage : toute session encore
  ouverte est fermée à cet instant (le serveur a déconnecté tout le monde).
- Idempotence : les périodes déjà couvertes par la base (une session
  existante DANS la fenêtre importée) font échouer l'--apply — cet outil
  est fait pour un historique ANTÉRIEUR au suivi mc-admin, pas pour
  fusionner des périodes qui se chevauchent.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_LINE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] \[Server thread/INFO\]: (.*)$")
_JOINED = re.compile(r"^(\S+) joined the game$")
_LEFT = re.compile(r"^(\S+) left the game$")
_COMMAND = re.compile(r"^(\S+) issued server command: (.*)$")
_FILENAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d+)\.log\.gz$")
_BOOT_MARKER = "Starting minecraft server"


def _ordered_files(folder: Path) -> list[tuple[date, int, Path]]:
    out = []
    for path in folder.iterdir():
        m = _FILENAME.match(path.name)
        if m:
            out.append((date(int(m[1]), int(m[2]), int(m[3])), int(m[4]), path))
        elif path.name == "latest.log":
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            out.append((mtime.date(), 10_000, path))  # toujours après les .gz du jour
    return sorted(out)


def _lines(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        yield from fh


def parse_folder(folder: Path, tz: ZoneInfo) -> tuple[list[dict], list[dict]]:
    """(sessions, commandes) — sessions {player, joined_at, left_at} en UTC."""
    sessions: list[dict] = []
    commands: list[dict] = []
    open_sessions: dict[str, datetime] = {}
    utc = ZoneInfo("UTC")

    def close(player: str, at: datetime) -> None:
        started = open_sessions.pop(player, None)
        if started is not None and at >= started:
            sessions.append({
                "player": player,
                "joined_at": started.astimezone(utc).isoformat(),
                "left_at": at.astimezone(utc).isoformat(),
            })

    for file_date, _seq, path in _ordered_files(folder):
        day = file_date
        previous_time: tuple[int, int, int] | None = None
        last_seen: datetime | None = None
        for raw in _lines(path):
            m = _LINE.match(raw.rstrip("\n"))
            if not m:
                continue
            clock = (int(m[1]), int(m[2]), int(m[3]))
            if previous_time is not None and clock < previous_time:
                day = day + timedelta(days=1)  # minuit franchi dans le même fichier
            previous_time = clock
            moment = datetime(day.year, day.month, day.day, *clock, tzinfo=tz)
            last_seen = moment
            message = m[4]

            if _BOOT_MARKER in message:
                for player in list(open_sessions):
                    close(player, moment)
                continue
            joined = _JOINED.match(message)
            if joined:
                close(joined[1], moment)  # jamais deux sessions ouvertes
                open_sessions[joined[1]] = moment
                continue
            left = _LEFT.match(message)
            if left:
                close(left[1], moment)
                continue
            command = _COMMAND.match(message)
            if command:
                commands.append({
                    "player": command[1],
                    "at": moment.astimezone(utc).isoformat(),
                    "command": command[2],
                })
    # Fin des archives : le serveur d'origine n'existe plus, on ferme au
    # dernier instant observé (best-effort, mêmes garanties que le tailer).
    if last_seen is not None:
        for player in list(open_sessions):
            close(player, last_seen)
    return sessions, commands


def summarize(sessions: list[dict], commands: list[dict]) -> str:
    per_player: dict[str, float] = defaultdict(float)
    for s in sessions:
        seconds = (datetime.fromisoformat(s["left_at"])
                   - datetime.fromisoformat(s["joined_at"])).total_seconds()
        per_player[s["player"]] += seconds
    lines = [f"{len(sessions)} session(s), {len(commands)} commande(s)"]
    for player, seconds in sorted(per_player.items(), key=lambda kv: -kv[1]):
        count = sum(1 for s in sessions if s["player"] == player)
        lines.append(f"  {player:<20} {seconds / 3600:6.1f} h  ({count} sessions)")
    if sessions:
        lines.append(f"période : {min(s['joined_at'] for s in sessions)[:10]}"
                     f" → {max(s['left_at'] for s in sessions)[:10]}")
    return "\n".join(lines)


def apply_to_db(db_path: Path, sessions: list[dict], commands: list[dict]) -> None:
    first = min(s["joined_at"] for s in sessions)
    last = max(s["left_at"] for s in sessions)
    with sqlite3.connect(db_path) as conn:
        overlap = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE joined_at <= ? AND joined_at >= ?",
            (last, first),
        ).fetchone()[0]
        if overlap:
            raise SystemExit(
                f"refus : {overlap} session(s) existante(s) dans la fenêtre importée "
                f"({first[:10]} → {last[:10]}) — import déjà fait ?"
            )
        conn.executemany(
            "INSERT INTO sessions (player, joined_at, left_at) VALUES (:player, :joined_at, :left_at)",
            sessions,
        )
        conn.executemany(
            "INSERT INTO player_commands (player, at, command) VALUES (:player, :at, :command)",
            commands,
        )
    print(f"écrit : {len(sessions)} sessions + {len(commands)} commandes dans {db_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--tz", default="Europe/Paris")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", type=Path, default=None,
                        help="écrit sessions+commandes en JSON (pour un apply distant)")
    args = parser.parse_args()

    sessions, commands = parse_folder(args.folder, ZoneInfo(args.tz))
    print(summarize(sessions, commands))
    if args.json:
        args.json.write_text(json.dumps(
            {"sessions": sessions, "commands": commands}, ensure_ascii=False, indent=1))
        print(f"exporté : {args.json}")
    if args.apply:
        if args.db is None:
            raise SystemExit("--apply exige --db")
        apply_to_db(args.db, sessions, commands)
    elif not args.json:
        print("(dry-run : rien n'a été écrit — ajouter --apply --db <fichier>)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
