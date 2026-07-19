"""Adapter PlayerStatsPort : statistiques vanilla depuis les fichiers du monde.

Sources (montées en LECTURE SEULE, même politique que ops.json) :
  - `<monde>/players/stats/<uuid>.json` : compteurs tenus par le serveur
    depuis la création du monde (play_time en TICKS, distances en CM…) ;
  - `usercache.json` : correspondance uuid -> pseudo (le serveur en expire
    les entrées au bout de ~1 mois) ;
  - fichier d'alias OPTIONNEL (`legacy-player-names.json`, import de
    l'ancien serveur Nitroserv 18/07/2026) : correspondances d'époque pour
    les joueurs expirés du usercache — le usercache, plus frais, prime.

Best-effort intégral : répertoire absent = liste vide, fichier corrompu =
joueur ignoré, uuid inconnu de partout = pseudo de repli (uuid tronqué).
La page Joueurs ne doit jamais casser à cause d'un fichier de stats.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from domain.model import PlayerStats


def _custom_sum(counts: dict, suffix: str) -> int:
    return sum(v for k, v in counts.items() if isinstance(v, int) and k.endswith(suffix))


class FilePlayerStats:
    def __init__(self, stats_dir: str, usercache_file: str, aliases_file: str = "") -> None:
        self._stats_dir = stats_dir
        self._usercache = usercache_file
        self._aliases = aliases_file

    def _names_by_uuid(self) -> dict[str, str]:
        names: dict[str, str] = {}
        if self._aliases:
            try:
                with open(self._aliases, encoding="utf-8") as fh:
                    payload = json.load(fh)
                aliases = payload.get("aliases") if isinstance(payload, dict) else None
                if isinstance(aliases, dict):
                    names.update({
                        str(uuid): str(name)
                        for uuid, name in aliases.items() if uuid and name
                    })
            except (OSError, ValueError, TypeError):
                pass  # alias illisibles : on retombe sur le seul usercache
        try:
            with open(self._usercache, encoding="utf-8") as fh:
                entries = json.load(fh)
            names.update({
                e["uuid"]: e["name"] for e in entries if e.get("uuid") and e.get("name")
            })
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return names

    def _parse(self, path: str, player: str, uuid: str) -> PlayerStats | None:
        try:
            last_played = datetime.fromtimestamp(os.stat(path).st_mtime, tz=timezone.utc)
            with open(path, encoding="utf-8") as fh:
                stats = (json.load(fh) or {}).get("stats") or {}
            custom = stats.get("minecraft:custom") or {}
            return PlayerStats(
                player=player,
                play_time_seconds=custom.get("minecraft:play_time", 0) / 20,  # ticks -> secondes
                deaths=custom.get("minecraft:deaths", 0),
                mob_kills=custom.get("minecraft:mob_kills", 0),
                player_kills=custom.get("minecraft:player_kills", 0),
                distance_cm=_custom_sum(custom, "_one_cm"),
                jumps=custom.get("minecraft:jump", 0),
                blocks_mined=sum((stats.get("minecraft:mined") or {}).values()),
                items_crafted=sum((stats.get("minecraft:crafted") or {}).values()),
                uuid=uuid,
                last_played=last_played,
            )
        except (OSError, ValueError, TypeError):
            return None  # fichier illisible : joueur ignoré, jamais d'erreur

    def all_stats(self) -> list[PlayerStats]:
        try:
            files = sorted(f for f in os.listdir(self._stats_dir) if f.endswith(".json"))
        except OSError:
            return []
        names = self._names_by_uuid()
        result: list[PlayerStats] = []
        for filename in files:
            uuid = filename[:-5]
            player = names.get(uuid, uuid[:8])  # repli : uuid tronqué si inconnu partout
            parsed = self._parse(os.path.join(self._stats_dir, filename), player, uuid)
            if parsed is not None:
                result.append(parsed)
        return result
