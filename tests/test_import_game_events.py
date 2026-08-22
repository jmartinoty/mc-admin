"""Import rétroactif des faits de jeu depuis les archives.

Ce script ÉCRIT dans la base de production : il mérite un filet, même si les
autres scripts du dossier n'en ont pas. Le parseur et le stockage étant déjà
couverts ailleurs, on teste ici ce qui lui est propre — datation, résolution
des joueurs, idempotence, refus d'écrire sans --apply.
"""
from __future__ import annotations

import gzip
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from adapters.game_events import SqliteGameEvents

import_game_events = __import__("import_game_events")


def archive(folder: Path, name: str, lines: list[str]) -> None:
    with gzip.open(folder / name, "wt", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


def entry(clock: str, message: str) -> str:
    return f"[{clock}] [Server thread/INFO]: {message}"


class ImportTests(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.folder = Path(holder.name)
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db)

    def _run(self, *extra: str) -> int:
        return import_game_events.main(
            [str(self.folder), "--db", self.db, *extra])

    def test_dry_run_writes_nothing(self):
        archive(self.folder, "2026-07-01-1.log.gz",
                [entry("21:00:00", "Trappeur drowned")])
        self.assertEqual(self._run(), 0)
        self.assertEqual(SqliteGameEvents(self.db).recent(), [])

    def test_apply_writes_and_dates_from_the_filename(self):
        archive(self.folder, "2026-07-01-1.log.gz",
                [entry("21:00:00", "Trappeur drowned")])
        self._run("--apply")
        (event,) = SqliteGameEvents(self.db).recent()
        self.assertEqual(event.player, "Trappeur")
        # 21h à Paris en juillet (UTC+2) = 19h UTC : les logs ne portent pas
        # de fuseau, celui du serveur d'origine est passé en paramètre.
        self.assertEqual(event.at.hour, 19)
        self.assertEqual(event.at.strftime("%Y-%m-%d"), "2026-07-01")

    def test_midnight_inside_a_file_rolls_the_day(self):
        """Une heure qui recule dans un même fichier = minuit franchi. Sans ce
        traitement, la seconde mort serait datée 24 h AVANT la première.

        On compare les instants plutôt que les dates : le stockage est en UTC,
        où 00:01 à Paris appartient encore à la veille."""
        archive(self.folder, "2026-07-01-1.log.gz", [
            entry("23:59:00", "Trappeur drowned"),
            entry("00:01:00", "Trappeur was slain by Creeper"),  # lendemain
        ])
        self._run("--apply")
        events = SqliteGameEvents(self.db).recent()  # plus récent d'abord
        self.assertEqual([e.key for e in events], ["slain_by", "drowned"])
        ecart = events[0].at - events[1].at
        self.assertEqual(ecart.total_seconds(), 120)

    def test_a_name_that_joined_the_game_is_a_player(self):
        archive(self.folder, "2026-07-01-1.log.gz", [
            entry("20:00:00", "Bucheron joined the game"),
            entry("21:00:00", "Trappeur was slain by Bucheron"),
            entry("21:05:00", "Trappeur was slain by Creeper"),
        ])
        self._run("--apply")
        verdicts = {e.killer: e.killer_type for e in SqliteGameEvents(self.db).recent()}
        self.assertEqual(verdicts, {"Bucheron": "player", "Creeper": "mob"})

    def test_papermc_async_chat_is_imported(self):
        """Non-régression : le script filtrait les lignes avec le motif de
        import_legacy_logs, qui exige « Server thread » — 7 messages réels
        étaient écartés avant même d'atteindre le parseur. C'est à `parse_line`
        de juger des threads, pas à l'extracteur d'heure."""
        archive(self.folder, "2026-07-01-1.log.gz", [
            "[21:00:00] [Async Chat Thread - #4/INFO]: <Trappeur> hello",
            entry("21:01:00", "Trappeur drowned"),
        ])
        self._run("--apply")
        events = SqliteGameEvents(self.db).recent()
        self.assertEqual(sorted(e.kind for e in events), ["chat", "death"])

    def test_running_twice_does_not_duplicate(self):
        archive(self.folder, "2026-07-01-1.log.gz",
                [entry("21:00:00", "Trappeur drowned"),
                 entry("21:30:00", "<Trappeur> coucou")])
        self._run("--apply")
        self._run("--apply")
        self.assertEqual(len(SqliteGameEvents(self.db).recent()), 2)

    def test_missing_folder_is_reported(self):
        self.assertEqual(
            import_game_events.main(["/dossier/inexistant", "--db", self.db]), 2)


if __name__ == "__main__":
    unittest.main()
