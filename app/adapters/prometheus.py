"""Adapter MetricsPort : évalue des requêtes PromQL PRÉDÉFINIES via Prometheus.

Les requêtes viennent de la configuration (config/metrics.yml) chargée au
démarrage — jamais de PromQL arbitraire depuis l'UI (cf. CLAUDE.md §8/V2.2).
Client HTTP en stdlib (urllib) : pas de dépendance supplémentaire, thread-safe.

Politique d'erreur :
  - Prometheus injoignable (connexion/timeout)  -> ServerUnavailable (panneau
    entier « indisponible », dégradé proprement par l'UI) ;
  - requête individuelle en erreur (PromQL invalide, HTTP 4xx/5xx) ou sans
    résultat -> value=None pour CETTE métrique, les autres restent affichées.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from domain.errors import ServerUnavailable
from domain.model import EntityTop, MetricReading, PerformanceSnapshot, WorldChunks

# Fenêtre des sparklines : 24 h par pas de 30 min (48 points).
_SPARK_RANGE_SECONDS = 24 * 3600
_SPARK_STEP_SECONDS = 1800


class PrometheusMetrics:
    def __init__(self, base_url: str, specs: list[dict], fetch=None, timeout: float = 3.0,
                 range_timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._specs = list(specs)
        self._timeout = timeout
        # Les query_range (historique) coûtent plus cher qu'une requête
        # instantanée : sous charge (rendu BlueMap, sauvegarde), 3 s ne
        # suffisaient pas et la courbe disparaissait silencieusement
        # (constaté le 19/07/2026). Timeout dédié, plus généreux.
        self._range_timeout = range_timeout
        self._uses_default_fetch = fetch is None
        self._fetch = fetch or self._default_fetch  # injectable pour les tests

    def _default_fetch(self, url: str, timeout: float | None = None) -> dict:
        with urllib.request.urlopen(url, timeout=timeout or self._timeout) as res:  # noqa: S310 — URL interne configurée
            return json.load(res)

    def _fetch_range(self, url: str) -> dict:
        """Comme _fetch mais avec le timeout des query_range (un fetch injecté
        par les tests est appelé tel quel)."""
        if self._uses_default_fetch:
            return self._default_fetch(url, timeout=self._range_timeout)
        return self._fetch(url)

    def _query_value(self, promql: str) -> float | None:
        url = f"{self._base}/api/v1/query?query={urllib.parse.quote(promql)}"
        try:
            data = self._fetch(url)
        except urllib.error.HTTPError:
            # Requête refusée (ex. PromQL invalide) : n'invalide que cette métrique.
            return None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as exc:
            # ValueError : urllib sur une URL sans schéma (PROMETHEUS_URL vide,
            # cf. patch-notes.md 20/07/2026) — même dégradation que injoignable.
            raise ServerUnavailable(f"Prometheus injoignable ({self._base})") from exc
        result = (data.get("data") or {}).get("result") or []
        if data.get("status") != "success" or not result:
            return None
        try:
            return float(result[0]["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def _query_history(self, promql: str, range_seconds: int = _SPARK_RANGE_SECONDS,
                       step_seconds: int = _SPARK_STEP_SECONDS) -> tuple[float, ...] | None:
        """Série sur `range_seconds` (défaut 24 h — sparklines). Best-effort :
        TOUTE erreur -> None, la valeur instantanée reste affichée (si
        Prometheus est vraiment down, _query_value lève déjà pour le panneau
        entier — inutile de lever une seconde fois ici)."""
        end = time.time()
        params = urllib.parse.urlencode({
            "query": promql,
            "start": f"{end - range_seconds:.0f}",
            "end": f"{end:.0f}",
            "step": str(step_seconds),
        })
        try:
            data = self._fetch_range(f"{self._base}/api/v1/query_range?{params}")
            result = (data.get("data") or {}).get("result") or []
            if data.get("status") != "success" or not result:
                return None
            values = tuple(float(v) for _, v in result[0]["values"])
        except Exception:  # noqa: BLE001
            return None
        return values if len(values) >= 2 else None

    def _query_vector(self, promql: str) -> list[tuple[dict, float]]:
        """Résultat instantané AVEC labels (topk, groupements par monde).
        Même politique d'erreur que _query_value."""
        url = f"{self._base}/api/v1/query?query={urllib.parse.quote(promql)}"
        try:
            data = self._fetch(url)
        except urllib.error.HTTPError:
            return []
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as exc:
            # ValueError : urllib sur une URL sans schéma (PROMETHEUS_URL vide,
            # cf. patch-notes.md 20/07/2026) — même dégradation que injoignable.
            raise ServerUnavailable(f"Prometheus injoignable ({self._base})") from exc
        if data.get("status") != "success":
            return []
        out: list[tuple[dict, float]] = []
        for entry in (data.get("data") or {}).get("result") or []:
            try:
                out.append((entry.get("metric") or {}, float(entry["value"][1])))
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        return out

    def _query_range_by(self, promql: str, label: str) -> dict[str, tuple[float, ...]]:
        """Séries 24 h groupées par un label (ex. chunks par monde). Best-effort."""
        end = time.time()
        params = urllib.parse.urlencode({
            "query": promql,
            "start": f"{end - _SPARK_RANGE_SECONDS:.0f}",
            "end": f"{end:.0f}",
            "step": str(_SPARK_STEP_SECONDS),
        })
        try:
            data = self._fetch_range(f"{self._base}/api/v1/query_range?{params}")
            if data.get("status") != "success":
                return {}
            out: dict[str, tuple[float, ...]] = {}
            for entry in (data.get("data") or {}).get("result") or []:
                key = (entry.get("metric") or {}).get(label)
                if key is None:
                    continue
                values = tuple(float(v) for _, v in entry.get("values") or [])
                if len(values) >= 2:
                    out[str(key)] = values
            return out
        except Exception:  # noqa: BLE001
            return {}

    # Requêtes de la page Performances : FIGÉES ici (jamais depuis l'UI).
    # `> 0` sur le top : sur un serveur vide, FabricExporter publie chaque
    # type d'entité à 0 — un topk brut renverrait des zéros arbitraires.
    _PERF_TOP_ENTITIES = "topk(10, sum by (world, type) (minecraft_entities) > 0)"
    _PERF_ENTITIES_TOTAL = "sum(minecraft_entities)"
    _PERF_CHUNKS = "sum by (world) (minecraft_loaded_chunks)"

    # Pas d'échantillonnage du graphe MSPT par plage demandée : la fenêtre du
    # max_over_time ÉGALE le pas — chaque point est le PIRE de sa fenêtre, un
    # pic ne peut plus passer entre deux échantillons (demande Jeremy 19/07).
    _MSPT_WINDOWS = {24: (900, "15m"), 72: (1800, "30m"), 168: (3600, "1h")}

    def performance(self, history_hours: int = 24) -> PerformanceSnapshot:
        tps = self._query_value("minecraft_tps")
        mspt_mean = self._query_value('minecraft_mspt{type="mean"}')
        mspt_max = self._query_value('minecraft_mspt{type="max"}')
        step, window = self._MSPT_WINDOWS.get(history_hours, self._MSPT_WINDOWS[24])
        mspt_history = self._query_history(
            f'max_over_time(minecraft_mspt{{type="mean"}}[{window}])',
            range_seconds=history_hours * 3600, step_seconds=step)
        total = self._query_value(self._PERF_ENTITIES_TOTAL)
        top = tuple(
            EntityTop(world=str(labels.get("world", "?")), type=str(labels.get("type", "?")),
                      count=int(value))
            for labels, value in self._query_vector(self._PERF_TOP_ENTITIES)
        )
        chunks_now = {str(labels.get("world", "?")): int(value)
                      for labels, value in self._query_vector(self._PERF_CHUNKS)}
        chunks_hist = self._query_range_by(self._PERF_CHUNKS, "world")
        chunks = tuple(
            WorldChunks(world=world, count=chunks_now.get(world), history=chunks_hist.get(world))
            for world in sorted(set(chunks_now) | set(chunks_hist))
        )
        return PerformanceSnapshot(
            tps=tps, mspt_mean=mspt_mean, mspt_max=mspt_max, mspt_history=mspt_history,
            entities_total=int(total) if total is not None else None,
            entity_top=top, chunks=chunks,
        )

    def snapshot(self) -> list[MetricReading]:
        return [
            MetricReading(
                key=spec["key"],
                label=spec["label"],
                unit=spec.get("unit", ""),
                value=self._query_value(spec["query"]),
                history=self._query_history(spec["query"]) if spec.get("spark") else None,
            )
            for spec in self._specs
        ]
