"""Adapter Clock : horloge système en UTC.

UTC (pas de fuseau local) : timestamps d'audit non ambigus, et aucune dépendance
à tzdata (absent des images slim). L'affichage local est un concern de l'UI.
"""
from __future__ import annotations

from datetime import datetime, timezone


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
