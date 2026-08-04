"""Tests de l'adapter PrometheusMetrics (fetch HTTP factice — pas de réseau)."""
from __future__ import annotations

import unittest
import urllib.error
import urllib.parse

from adapters.prometheus import PrometheusMetrics
from domain.errors import ServerUnavailable

SPECS = [
    {"key": "players", "label": "Joueurs en ligne", "unit": "", "query": 'minecraft_status_players_online_count{server_host="minecraft"}'},
    {"key": "ram", "label": "RAM serveur", "unit": "Mio", "query": 'container_memory_working_set_bytes{name="minecraft"} / 1048576'},
]


def _ok(value):
    return {"status": "success", "data": {"result": [{"value": [1751500000, str(value)]}]}}


def _empty():
    return {"status": "success", "data": {"result": []}}


def _range_ok(*values):
    return {"status": "success",
            "data": {"result": [{"values": [[1751500000 + i * 1800, str(v)] for i, v in enumerate(values)]}]}}


class TestSparkHistory(unittest.TestCase):
    """`spark: true` déclenche une requête query_range (24 h) ; best-effort."""

    SPARK_SPECS = [{"key": "ram", "label": "RAM", "unit": "Mio", "query": "ram_q", "spark": True}]

    def test_spark_spec_gets_history(self):
        def fetch(url):
            if "query_range" in url:
                self.assertIn("step=1800", url)
                return _range_ok(4100, 4300, 4500)
            return _ok(4500)
        (reading,) = PrometheusMetrics("http://prom:9090", self.SPARK_SPECS, fetch=fetch).snapshot()
        self.assertEqual(reading.history, (4100.0, 4300.0, 4500.0))
        self.assertEqual(reading.value, 4500.0)

    def test_non_spark_spec_makes_no_range_query(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return _ok(3)
        PrometheusMetrics("http://prom:9090", SPECS, fetch=fetch).snapshot()
        self.assertFalse([u for u in urls if "query_range" in u])

    def test_range_failure_keeps_instant_value(self):
        def fetch(url):
            if "query_range" in url:
                raise urllib.error.HTTPError(url, 500, "boom", None, None)
            return _ok(4500)
        (reading,) = PrometheusMetrics("http://prom:9090", self.SPARK_SPECS, fetch=fetch).snapshot()
        self.assertIsNone(reading.history)
        self.assertEqual(reading.value, 4500.0)

    def test_single_point_history_is_discarded(self):
        # Une polyline à 1 point n'affiche rien : autant dégrader en None.
        def fetch(url):
            if "query_range" in url:
                return _range_ok(4500)
            return _ok(4500)
        (reading,) = PrometheusMetrics("http://prom:9090", self.SPARK_SPECS, fetch=fetch).snapshot()
        self.assertIsNone(reading.history)


class TestPrometheusMetrics(unittest.TestCase):
    def test_snapshot_parses_values(self):
        responses = iter([_ok(3), _ok(4521.7)])
        adapter = PrometheusMetrics("http://prom:9090", SPECS, fetch=lambda url: next(responses))
        readings = adapter.snapshot()
        self.assertEqual([r.key for r in readings], ["players", "ram"])
        self.assertEqual(readings[0].value, 3.0)
        self.assertAlmostEqual(readings[1].value, 4521.7)
        self.assertEqual(readings[1].unit, "Mio")

    def test_query_is_url_encoded(self):
        urls = []
        adapter = PrometheusMetrics("http://prom:9090/", SPECS[:1], fetch=lambda u: urls.append(u) or _ok(1))
        adapter.snapshot()
        self.assertTrue(urls[0].startswith("http://prom:9090/api/v1/query?query="))
        self.assertNotIn('"', urls[0].split("query=")[1])  # encodé
        decoded = urllib.parse.unquote(urls[0].split("query=")[1])
        self.assertEqual(decoded, SPECS[0]["query"])

    def test_empty_result_gives_none_value(self):
        adapter = PrometheusMetrics("http://prom:9090", SPECS[:1], fetch=lambda u: _empty())
        self.assertIsNone(adapter.snapshot()[0].value)

    def test_http_error_invalidates_only_that_metric(self):
        def fetch(url):
            if "players" in url:
                raise urllib.error.HTTPError(url, 400, "bad query", {}, None)
            return _ok(4500)
        adapter = PrometheusMetrics("http://prom:9090", SPECS, fetch=fetch)
        readings = adapter.snapshot()
        self.assertIsNone(readings[0].value)       # requête refusée -> None
        self.assertEqual(readings[1].value, 4500)  # l'autre survit

    def test_connection_error_raises_server_unavailable(self):
        def fetch(url):
            raise urllib.error.URLError("connexion refusée")
        adapter = PrometheusMetrics("http://prom:9090", SPECS, fetch=fetch)
        with self.assertRaises(ServerUnavailable):
            adapter.snapshot()

    def test_malformed_payload_gives_none(self):
        adapter = PrometheusMetrics("http://prom:9090", SPECS[:1], fetch=lambda u: {"status": "success", "data": {"result": [{"value": []}]}})
        self.assertIsNone(adapter.snapshot()[0].value)

    def test_empty_base_url_raises_server_unavailable(self):
        # PROMETHEUS_URL="" (facultatif, non configuré) : urllib lève
        # ValueError sur l'URL sans schéma AVANT toute tentative réseau —
        # doit dégrader comme un Prometheus injoignable, jamais un 500 brut
        # (bug réel constaté le 20/07/2026, cf. patch-notes.md). Pas de
        # fetch injecté : on exerce le vrai urllib.request.urlopen.
        adapter = PrometheusMetrics("", SPECS)
        with self.assertRaises(ServerUnavailable):
            adapter.snapshot()


class TestPerformance(unittest.TestCase):
    """performance() (V6.7) : vecteurs labellisés + séries par monde."""

    @staticmethod
    def _fetch(url):
        import urllib.parse as up
        query = up.parse_qs(up.urlsplit(url).query)["query"][0]
        if "query_range" in url:
            if "minecraft_loaded_chunks" in query:
                return {"status": "success", "data": {"result": [
                    {"metric": {"world": "overworld"}, "values": [[0, "250"], [1, "289"]]}]}}
            return {"status": "success", "data": {"result": [
                {"metric": {}, "values": [[0, "2.5"], [1, "61.0"]]}]}}
        if query.startswith("topk"):
            return {"status": "success", "data": {"result": [
                {"metric": {"world": "overworld", "type": "zombie"}, "value": [0, "120"]},
                {"metric": {"world": "the_nether", "type": "ghast"}, "value": [0, "14"]}]}}
        if query.startswith("sum by (world)"):
            return {"status": "success", "data": {"result": [
                {"metric": {"world": "overworld"}, "value": [0, "289"]}]}}
        if query.startswith("sum("):
            return {"status": "success", "data": {"result": [{"metric": {}, "value": [0, "245"]}]}}
        return {"status": "success", "data": {"result": [{"metric": {}, "value": [0, "20"]}]}}

    def test_parses_full_snapshot(self):
        adapter = PrometheusMetrics("http://prom:9090", [], fetch=self._fetch)
        perf = adapter.performance()
        self.assertEqual(perf.tps, 20)
        self.assertEqual(perf.entities_total, 245)
        self.assertEqual((perf.entity_top[0].type, perf.entity_top[0].count), ("zombie", 120))
        self.assertEqual(perf.chunks[0].world, "overworld")
        self.assertEqual(perf.chunks[0].count, 289)
        self.assertEqual(perf.chunks[0].history, (250.0, 289.0))
        self.assertEqual(perf.mspt_history, (2.5, 61.0))

    def test_unreachable_raises_server_unavailable(self):
        def fetch(url):
            raise urllib.error.URLError("down")
        adapter = PrometheusMetrics("http://prom:9090", [], fetch=fetch)
        with self.assertRaises(ServerUnavailable):
            adapter.performance()

    def test_empty_base_url_raises_server_unavailable(self):
        # Même bug que TestPrometheusMetrics, via _query_vector (topk) —
        # vrai urllib, pas de fetch injecté.
        adapter = PrometheusMetrics("", [])
        with self.assertRaises(ServerUnavailable):
            adapter.performance()

    def test_mspt_history_uses_worst_of_window_and_requested_range(self):
        """Le graphe échantillonne le PIRE de chaque fenêtre (un pic ne se
        cache pas entre deux points) et suit la plage demandée."""
        urls = []

        def fetch(url):
            urls.append(url)
            return self._fetch(url)

        adapter = PrometheusMetrics("http://prom:9090", [], fetch=fetch)
        adapter.performance(72)
        range_urls = [u for u in urls if "query_range" in u and "max_over_time" in u]
        self.assertTrue(range_urls)
        import urllib.parse as up
        query = up.parse_qs(up.urlsplit(range_urls[0]).query)
        self.assertIn("max_over_time", query["query"][0])
        self.assertIn("[30m]", query["query"][0])                  # fenêtre = pas (3 j)
        self.assertEqual(query["step"][0], "1800")
        start, end = float(query["start"][0]), float(query["end"][0])
        self.assertAlmostEqual(end - start, 72 * 3600, delta=5)


if __name__ == "__main__":
    unittest.main()
