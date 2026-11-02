"""Relais de la carte : un navigateur qui s'en va libère la connexion amont.

Test de bout en bout avec de VRAIS sockets et un VRAI uvicorn (même version
d'ASGI que la production) : c'est le seul moyen d'observer ce qui se passe
quand un client se déconnecte au milieu d'un flux. Constaté en production le
02/11/2026 : en quittant la page Carte pendant qu'un joueur était en ligne
(BlueMap émet alors en continu, une cinquantaine d'événements en 20 s), mc-admin
relâchait bien son thread mais laissait la connexion vers BlueMap ouverte,
orpheline, ses données s'accumulant sans lecteur.
"""
from __future__ import annotations

import http.server
import socket
import threading
import time
import unittest

try:
    import uvicorn
    from fastapi import FastAPI

    HAS_HTTP_STACK = True
except ImportError:  # pragma: no cover — image sans dépendances web
    HAS_HTTP_STACK = False


class _CarteBavarde(http.server.BaseHTTPRequestHandler):
    """Faux BlueMap : un flux qui émet sans arrêt, comme pendant un rendu.
    Note l'instant où le relais coupe la connexion (l'écriture échoue)."""

    ferme = threading.Event()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for _ in range(400):  # 20 s au plus
                self.wfile.write(b"event: tile\ndata: {}\n\n")
                self.wfile.flush()
                time.sleep(0.05)
        except OSError:
            type(self).ferme.set()

    def log_message(self, *args):
        pass


@unittest.skipUnless(HAS_HTTP_STACK, "fastapi/uvicorn requis")
class TestDeconnexionDuNavigateur(unittest.TestCase):
    def setUp(self):
        _CarteBavarde.ferme = threading.Event()
        self.carte = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CarteBavarde)
        self.carte.daemon_threads = True
        threading.Thread(target=self.carte.serve_forever, daemon=True).start()
        self.addCleanup(self.carte.server_close)
        self.addCleanup(self.carte.shutdown)

        from adapters.map_fetch import open_map_path
        from api.routes.dashboard import relayed_map_response

        base = f"http://127.0.0.1:{self.carte.server_address[1]}"
        app = FastAPI()

        @app.get("/flux")
        def flux():  # même forme que la route réelle : `def`, donc pool de threads
            status, headers, corps = open_map_path(base, "maps/monde/live/sse")
            return relayed_map_response(status, headers, corps)

        self.serveur = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="error", lifespan="off"))
        thread = threading.Thread(target=self.serveur.run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(setattr, self.serveur, "should_exit", True)
        fin = time.monotonic() + 5
        while not self.serveur.started and time.monotonic() < fin:
            time.sleep(0.02)
        self.assertTrue(self.serveur.started, "uvicorn n'a pas démarré")
        self.port = self.serveur.servers[0].sockets[0].getsockname()[1]

    def test_quitter_la_page_ferme_la_connexion_vers_la_carte(self):
        client = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        client.sendall(b"GET /flux HTTP/1.1\r\nHost: test\r\n\r\n")
        recu = b""
        while recu.count(b"event: tile") < 3:
            recu += client.recv(4096)
        client.close()  # l'onglet se ferme, en plein flux

        self.assertTrue(
            _CarteBavarde.ferme.wait(5),
            "la connexion vers la carte est restée ouverte après le départ du navigateur",
        )


if __name__ == "__main__":
    unittest.main()
