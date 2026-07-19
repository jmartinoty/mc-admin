"""Adapter AuditPort : journal d'audit append-only au format JSONL.

Choix MVP : un fichier JSONL (une entrée JSON par ligne) plutôt qu'une base de
données — simple, greppable, append-only. Chaque écriture est un append en fin
de fichier ; aucune ligne existante n'est modifiée (traçabilité).

Rotation MENSUELLE (V5.x) : les écritures vont dans `<nom>-AAAA-MM.jsonl`
(mois courant) ; la lecture parcourt les fichiers de mois du plus récent au
plus ancien (l'ancien fichier unique `<nom>.jsonl` est lu en dernier, comme
archive historique — jamais réécrit). Le journal reste rapide même après des
années d'événements, et rien n'est supprimé.
"""
from __future__ import annotations

import glob
import json
import logging
import os
from collections import deque
from datetime import datetime, timezone

from domain.model import AuditEntry

logger = logging.getLogger("mc_admin")


class FileAuditLog:
    def __init__(self, path: str, clock=None) -> None:
        self._path = path
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        base, ext = os.path.splitext(path)
        self._base, self._ext = base, ext or ".jsonl"
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _month_path(self) -> str:
        return f"{self._base}-{self._clock().strftime('%Y-%m')}{self._ext}"

    def _read_paths(self) -> list[str]:
        """Fichiers à lire, du plus RÉCENT au plus ancien."""
        monthly = sorted(glob.glob(f"{self._base}-[0-9][0-9][0-9][0-9]-[0-9][0-9]{self._ext}"),
                         reverse=True)
        legacy = [self._path] if os.path.exists(self._path) else []
        return monthly + legacy

    def record(self, entry: AuditEntry) -> None:
        payload = {
            "timestamp": entry.timestamp.isoformat(),
            "username": entry.username,
            "role": entry.role,
            "action": entry.action,
            "target": entry.target,
            "outcome": entry.outcome,
            "detail": entry.detail,
        }
        with open(self._month_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def recent(self, limit: int = 50) -> list[AuditEntry]:
        # Remonte les mois (du plus récent au plus ancien) jusqu'à la limite :
        # on ne relit pas des années d'historique pour afficher une page.
        collected: list[str] = []
        for path in self._read_paths():
            try:
                with open(path, encoding="utf-8") as fh:
                    tail = list(deque(fh, maxlen=limit))
            except FileNotFoundError:
                continue
            missing = limit - len(collected)
            collected = tail[-missing:] + collected  # ce fichier est PLUS ANCIEN que l'acquis
            if len(collected) >= limit:
                break
        last = collected
        if not last:
            return []
        entries: list[AuditEntry] = []
        skipped = 0
        for raw in last:
            raw = raw.strip()
            if not raw:
                continue
            # Une ligne corrompue (écriture interrompue, édition manuelle…) ne
            # doit JAMAIS priver de tout le journal : on l'ignore en la
            # comptant — le fichier brut reste la source de vérité pour
            # investiguer.
            try:
                data = json.loads(raw)
                entries.append(
                    AuditEntry(
                        timestamp=datetime.fromisoformat(data["timestamp"]),
                        username=data["username"],
                        role=data["role"],
                        action=data["action"],
                        target=data["target"],
                        outcome=data["outcome"],
                        detail=data.get("detail", ""),
                    )
                )
            except (ValueError, KeyError, TypeError):
                skipped += 1
        if skipped:
            logger.warning(
                "journal d'audit : %d ligne(s) illisible(s) ignorée(s)", skipped
            )
        return entries
