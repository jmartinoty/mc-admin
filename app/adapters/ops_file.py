"""Adapter OpsPort : lecture seule des opérateurs depuis `ops.json`.

Aucune commande RCON « op list » n'existe (vanilla ni Fabric) : la lecture
vient du fichier généré par le serveur. Minecraft reste son unique écrivain
tant qu'il tourne ; les niveaux individuels sont appliqués hors ligne par le
worker transactionnel dédié.
"""
from __future__ import annotations

import json

from domain.model import OpEntry


class OpsFile:
    def __init__(self, path: str) -> None:
        self._path = path

    def _read(self) -> list[dict]:
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return []  # serveur jamais démarré / aucun op défini
        except IsADirectoryError:
            # Piège Docker connu : bind-mounter un fichier hôte absent crée un
            # DOSSIER vide côté conteneur plutôt qu'un FileNotFoundError.
            return []
        except json.JSONDecodeError:
            return []  # écriture concurrente par le serveur : dégrade proprement
        return data if isinstance(data, list) else []

    def list_ops(self) -> list[OpEntry]:
        return [
            OpEntry(name=entry["name"], level=entry.get("level", 4))
            for entry in self._read()
            if isinstance(entry, dict) and "name" in entry
        ]
