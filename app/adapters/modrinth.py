"""Adapter : interrogation Modrinth par empreintes SHA-1 (mods enrichis).

Deux appels par passe, quel que soit le nombre de mods :
- POST /v2/version_files          -> quel jar installé = quelle version Modrinth ;
- POST /v2/version_files/update   -> pour chaque jar, la DERNIÈRE version
  compatible (loader + version du serveur), directement côté Modrinth.

L'identification par empreinte est EXACTE : aucune devinette sur les noms de
fichiers. Un jar absent des réponses = inconnu de Modrinth (mod perso,
CurseForge-only) — verdict honnête, pas une erreur. Les erreurs réseau
remontent au checker de fond, qui garde les derniers verdicts connus.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

from domain.model import ModUpdate

_API = "https://api.modrinth.com/v2"
# Modrinth demande un User-Agent identifiant (politique d'API publique).
_HEADERS = {"Content-Type": "application/json",
            "User-Agent": "mc-admin (github.com/jmartinoty/mc-admin)"}


class ModrinthCatalog:
    def __init__(self, fetch=None, timeout: float = 10.0) -> None:
        self._timeout = timeout
        self._fetch = fetch or self._default_fetch  # injectable pour les tests

    def _default_fetch(self, url: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=_HEADERS, method="POST")
        with urllib.request.urlopen(req, timeout=self._timeout) as res:  # noqa: S310
            return json.load(res)

    def check(self, hashes: list[str], game_version: str,
              loader: str = "fabric") -> dict[str, ModUpdate]:
        """Verdicts par empreinte. Lève en cas d'erreur réseau/API (le
        checker décide quoi en faire) ; un hash inconnu donne known=False."""
        if not hashes:
            return {}
        installed = self._fetch(f"{_API}/version_files",
                                {"hashes": hashes, "algorithm": "sha1"})
        latest = self._fetch(f"{_API}/version_files/update",
                             {"hashes": hashes, "algorithm": "sha1",
                              "loaders": [loader], "game_versions": [game_version]})
        now = datetime.now(timezone.utc)
        out: dict[str, ModUpdate] = {}
        for sha1 in hashes:
            cur = installed.get(sha1) if isinstance(installed, dict) else None
            if not isinstance(cur, dict):
                out[sha1] = ModUpdate(sha1=sha1, known=False, checked_at=now)
                continue
            new = latest.get(sha1) if isinstance(latest, dict) else None
            new = new if isinstance(new, dict) else {}
            project = str(cur.get("project_id") or "")
            out[sha1] = ModUpdate(
                sha1=sha1,
                known=True,
                project_url=f"https://modrinth.com/project/{project}" if project else "",
                installed_version=str(cur.get("version_number") or ""),
                latest_version=str(new.get("version_number") or ""),
                # Même version Modrinth = à jour ; une version DIFFÉRENTE
                # compatible plus récente = màj dispo. (L'endpoint /update ne
                # renvoie que la plus récente compatible.)
                update_available=bool(new) and new.get("id") != cur.get("id"),
                checked_at=now,
            )
        return out
