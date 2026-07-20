"""Relais borné de la carte web (adapters/map_fetch) : anti-évasion + verdicts."""
from __future__ import annotations

import io
import unittest
import urllib.error

from adapters.map_fetch import MapProbe, MapUnreachable, _resolve, open_map_path, probe_map


class _FakeResponse:
    def __init__(self, body=b"<html>carte</html>", content_type="text/html", status=200):
        self._buf = io.BytesIO(body)
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def read(self, n=-1):
        return self._buf.read(n)

    def close(self):
        self.closed = True


class TestResolve(unittest.TestCase):
    def test_normal_paths_stay_under_base(self):
        self.assertEqual(_resolve("http://minecraft:8100", ""), "http://minecraft:8100/")
        self.assertEqual(_resolve("http://minecraft:8100", "assets/app.js"),
                         "http://minecraft:8100/assets/app.js")
        self.assertEqual(_resolve("http://minecraft:8100/", "maps/world/x.json", "t=3"),
                         "http://minecraft:8100/maps/world/x.json?t=3")

    def test_dotdot_is_neutralised(self):
        # normpath replie les .. AVANT le collage : impossible de remonter.
        self.assertEqual(_resolve("http://minecraft:8100", "a/../b"),
                         "http://minecraft:8100/b")
        self.assertEqual(_resolve("http://minecraft:8100", "../../etc/passwd"),
                         "http://minecraft:8100/etc/passwd")

    def test_absolute_url_in_path_cannot_escape(self):
        # Même schéma / protocole-relatif : DÉSAMORCÉ en chemin sous la base
        # (part en 404 chez la carte, jamais chez le tiers).
        self.assertEqual(_resolve("http://minecraft:8100", "http://pirate.example/vole"),
                         "http://minecraft:8100/pirate.example/vole")
        self.assertEqual(_resolve("http://minecraft:8100", "//pirate.example/vole"),
                         "http://minecraft:8100/pirate.example/vole")
        # Schéma différent : refusé net.
        with self.assertRaises(MapUnreachable):
            _resolve("http://minecraft:8100", "https://pirate.example/vole")
        with self.assertRaises(MapUnreachable):
            _resolve("http://minecraft:8100", "ftp://pirate.example")


class TestProbe(unittest.TestCase):
    def test_html_response_is_ok(self):
        ok, detail = probe_map("http://minecraft:8100", opener=lambda url, timeout: _FakeResponse())
        self.assertTrue(ok)
        self.assertIn("répond", detail)

    def test_non_html_is_honest(self):
        ok, detail = probe_map("http://minecraft:8100",
                               opener=lambda url, timeout: _FakeResponse(content_type="application/json"))
        self.assertFalse(ok)
        self.assertIn("l'adresse de la carte", detail)

    def test_http_error_reports_code(self):
        def opener(url, timeout):
            raise urllib.error.HTTPError(url, 503, "nope", None, io.BytesIO(b""))
        ok, detail = probe_map("http://minecraft:8100", opener=opener)
        self.assertFalse(ok)
        self.assertIn("503", detail)

    def test_connection_error_never_raises(self):
        def opener(url, timeout):
            raise OSError("connexion refusée")
        ok, detail = MapProbe(opener=opener).probe("http://minecraft:8100")
        self.assertFalse(ok)
        self.assertIn("injoignable", detail)


class TestOpenMapPath(unittest.TestCase):
    def test_streams_body_and_content_type(self):
        response = _FakeResponse(body=b"x" * 100, content_type="image/png")
        status, headers, chunks = open_map_path("http://minecraft:8100", "tile.png",
                                                opener=lambda url, timeout: response)
        self.assertEqual((status, headers["Content-Type"]), (200, "image/png"))
        self.assertEqual(b"".join(chunks), b"x" * 100)
        self.assertTrue(response.closed)                # fermé après le stream

    def test_cache_headers_are_relayed_to_browser(self):
        response = _FakeResponse(content_type="application/json")
        response.headers.update({"ETag": "abc123", "Last-Modified": "Sun, 19 Jul 2026 05:04:16 GMT",
                                 "Cache-Control": "max-age=86400", "Server": "BlueMap/5.22"})
        _, headers, _ = open_map_path("http://minecraft:8100", "settings.json",
                                      opener=lambda url, timeout: response)
        self.assertEqual(headers.get("ETag"), "abc123")
        self.assertEqual(headers.get("Cache-Control"), "max-age=86400")
        self.assertIn("Last-Modified", headers)
        self.assertNotIn("Server", headers)             # transport/identité : jamais relayés

    def test_conditional_request_is_forwarded_upstream(self):
        seen = {}

        def opener(request, timeout):
            seen.update(request.header_items())
            return _FakeResponse()

        open_map_path("http://minecraft:8100", "tile.png", opener=opener,
                      if_none_match="abc123", if_modified_since="Sun, 19 Jul 2026 05:04:16 GMT")
        self.assertEqual(seen.get("If-none-match"), "abc123")
        self.assertEqual(seen.get("If-modified-since"), "Sun, 19 Jul 2026 05:04:16 GMT")

    def test_upstream_304_is_relayed_with_headers(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 304, "Not Modified",
                                         {"ETag": "abc123"}, io.BytesIO(b""))
        status, headers, chunks = open_map_path("http://minecraft:8100", "tile.png",
                                                opener=opener, if_none_match="abc123")
        self.assertEqual(status, 304)
        self.assertEqual(headers.get("ETag"), "abc123")
        self.assertEqual(b"".join(chunks), b"")         # 304 = pas de corps

    def test_upstream_404_is_relayed_not_raised(self):
        def opener(url, timeout):
            raise urllib.error.HTTPError("http://minecraft:8100/maps/vide.json", 404,
                                         "tuile absente", None, io.BytesIO(b""))
        status, _, chunks = open_map_path("http://minecraft:8100", "maps/vide.json", opener=opener)
        self.assertEqual(status, 404)
        self.assertEqual(b"".join(chunks), b"")

    def test_unreachable_raises_map_unreachable(self):
        def opener(url, timeout):
            raise OSError("réseau en carafe")
        with self.assertRaises(MapUnreachable):
            open_map_path("http://minecraft:8100", "", opener=opener)


if __name__ == "__main__":
    unittest.main()
