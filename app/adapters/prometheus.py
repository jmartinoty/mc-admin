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
from domain.model import (
    EntityTop,
    MetricReading,
    MsptSeries,
    PerformanceSnapshot,
    WorldChunks,
)

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

    def _query_series(self, promql: str, range_seconds: int = _SPARK_RANGE_SECONDS,
                      step_seconds: int = _SPARK_STEP_SECONDS,
                      ) -> tuple[tuple[float, float], ...] | None:
        """Série HORODATÉE sur `range_seconds` : couples (instant, valeur).
        Best-effort : TOUTE erreur -> None, la valeur instantanée reste
        affichée (si Prometheus est vraiment down, _query_value lève déjà
        pour le panneau entier — inutile de lever une seconde fois ici).

        Prometheus n'émet PAS de point là où il n'a pas de mesure : la série
        se contente d'être plus courte. Sans les instants, impossible de
        savoir OÙ manquent les points — d'où les couples."""
        fin = time.time()
        return self._fetch_series(promql, fin - range_seconds, fin, step_seconds)

    def _fetch_series(self, promql: str, start: float, end: float, step_seconds: int,
                      ) -> tuple[tuple[float, float], ...] | None:
        """Une fenêtre EXPLICITE [start, end]. C'est ce qui permet de remonter
        dans le temps : `_query_series` s'ancre sur maintenant, celle-ci non."""
        params = urllib.parse.urlencode({
            "query": promql,
            "start": f"{start:.0f}",
            "end": f"{end:.0f}",
            "step": str(step_seconds),
        })
        try:
            data = self._fetch_range(f"{self._base}/api/v1/query_range?{params}")
            result = (data.get("data") or {}).get("result") or []
            if data.get("status") != "success" or not result:
                return None
            points = tuple((float(at), float(v)) for at, v in result[0]["values"])
        except Exception:  # noqa: BLE001
            return None
        return points if len(points) >= 2 else None

    def _query_history(self, promql: str, range_seconds: int = _SPARK_RANGE_SECONDS,
                       step_seconds: int = _SPARK_STEP_SECONDS) -> tuple[float, ...] | None:
        """Valeurs nues, sans les instants — pour les SPARKLINES, qui n'ont
        ni axe ni repères : un trou y est invisible et sans conséquence. Le
        graphe MSPT, lui, passe par `_query_series`."""
        points = self._query_series(promql, range_seconds, step_seconds)
        return tuple(v for _, v in points) if points else None

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


    # Navigation libre : le pas suit la largeur de fenêtre. On prend le plus
    # petit pas donnant au plus _CIBLE_POINTS points. Le plancher à 60 s reste
    # au-dessus de l'intervalle de collecte (15 s) — descendre en dessous
    # ferait répéter des points sans rien apprendre.
    _PAS_ECHELLE = ((60, "1m"), (120, "2m"), (300, "5m"), (600, "10m"), (900, "15m"),
                    (1800, "30m"), (3600, "1h"), (7200, "2h"), (21600, "6h"),
                    (43200, "12h"), (86400, "1d"))
    _CIBLE_POINTS = 150

    @classmethod
    def _pas_pour(cls, largeur_s: float) -> tuple[int, str]:
        for pas, libelle in cls._PAS_ECHELLE:
            if largeur_s / pas <= cls._CIBLE_POINTS:
                return pas, libelle
        return cls._PAS_ECHELLE[-1]

    # Joueurs en ligne (mc-monitor). max_over_time : un passage éclair entre
    # deux points ne disparaît pas — même garantie que pour le MSPT.
    _PLAYERS_QUERY = "minecraft_status_players_online_count"

    def mspt_range(self, start: float, end: float) -> MsptSeries:
        """Séries MSPT + joueurs sur une fenêtre arbitraire — le cœur du
        défilement.

        La fenêtre du `max_over_time` ÉGALE le pas à TOUS les niveaux de
        zoom : la garantie « chaque point est le pire de sa fenêtre, un
        à-coup ne peut pas se cacher entre deux points » (demande Jeremy
        19/07) ne se dégrade donc pas quand on dézoome — elle s'exprime
        seulement sur une fenêtre plus large.

        Les joueurs voyagent AVEC le MSPT (même fenêtre, même pas) : la
        question réelle n'est jamais « quel était le MSPT ? » mais « qui
        était là quand ça a ramé ? » — les deux courbes doivent partager
        leur axe pour y répondre (review du 24/08/2026)."""
        pas, fenetre = self._pas_pour(max(end - start, 1.0))
        points = self._fetch_series(
            f'max_over_time(minecraft_mspt{{type="mean"}}[{fenetre}])', start, end, pas)
        players = self._fetch_series(
            f"max_over_time({self._PLAYERS_QUERY}[{fenetre}])", start, end, pas)
        return MsptSeries(start=start, end=end, step=pas,
                          points=points or (), players=players or ())

    def performance(self, history_hours: int = 24) -> PerformanceSnapshot:
        tps = self._query_value("minecraft_tps")
        mspt_mean = self._query_value('minecraft_mspt{type="mean"}')
        mspt_max = self._query_value('minecraft_mspt{type="max"}')
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
            tps=tps, mspt_mean=mspt_mean, mspt_max=mspt_max,
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
