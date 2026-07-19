"""Adapter BansPort : lit la liste des bannissements depuis `banned-players.json`.

Même politique que `ops_file.py` : pas de commande RCON fiable pour lister
(`/banlist` existe mais son format de sortie dépend du nombre de bannis, à
parser sur du texte libre — le fichier JSON est une source bien plus sûre),
donc lecture d'un fichier monté en LECTURE SEULE. Les mutations (ban/pardon)
passent par RCON (`GamePort`), qui met à jour ce fichier côté serveur.

Format de `banned-players.json` (confirmé) : liste d'objets avec `name`,
`reason`, `created` ("yyyy-MM-dd HH:mm:ss Z"), `expires` ("forever" pour tout
ban posé par la commande vanilla `/ban`, qui ne supporte pas de durée).
"""
from __future__ import annotations

import json
from datetime import datetime

from domain.model import BanEntry

_CREATED_FORMAT = "%Y-%m-%d %H:%M:%S %z"


def _parse_created(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, _CREATED_FORMAT)
    except ValueError:
        return None  # format inattendu -> dégrade proprement, pas d'exception


class FileBans:
    def __init__(self, path: str) -> None:
        self._path = path

    def list_bans(self) -> list[BanEntry]:
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return []
        except IsADirectoryError:
            # Piège Docker connu : bind-mounter un fichier hôte absent crée un
            # DOSSIER vide côté conteneur plutôt qu'un FileNotFoundError.
            return []
        except json.JSONDecodeError:
            return []  # écriture concurrente par le serveur : dégrade proprement
        if not isinstance(data, list):
            return []
        return [
            BanEntry(
                player=entry["name"],
                reason=entry.get("reason") or "",
                created_at=_parse_created(entry.get("created")),
                expires=entry.get("expires") or "forever",
            )
            for entry in data
            if isinstance(entry, dict) and "name" in entry
        ]
