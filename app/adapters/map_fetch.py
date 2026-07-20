"""Récupération BORNÉE de la carte web (BlueMap, Dynmap…) — page Carte.

mc-admin sert la carte SOUS SA PROPRE ORIGINE : le navigateur ne parle
jamais directement au serveur de carte. Conséquences voulues :
  - plus de contenu mixte (la carte suit le HTTPS de mc-admin, en LAN
    comme via Tailscale) ;
  - l'adresse configurée peut être INTERNE au réseau Docker
    (ex. http://minecraft:8100) — rien à exposer sur l'hôte.

Anti-SSRF : la seule base atteignable est l'URL configurée par l'owner
(validée http/https par le service) ; le chemin relayé est normalisé et
DOIT rester sous cette base ; GET uniquement ; aucun en-tête du client
n'est retransmis.
"""
from __future__ import annotations

import posixpath
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator

_CHUNK = 64 * 1024


class MapUnreachable(Exception):
    """Carte injoignable ou chemin refusé (verdict en langage utilisateur)."""


def _resolve(base_url: str, path: str, query: str = "") -> str:
    """Colle `path` sous la base, en refusant toute évasion (.., URL absolue)."""
    base = base_url.rstrip("/") + "/"
    clean = posixpath.normpath("/" + (path or "")).lstrip("/")
    if clean == ".":
        clean = ""
    target = urllib.parse.urljoin(base, clean)
    if not target.startswith(base) and target != base.rstrip("/"):
        raise MapUnreachable(f"chemin hors de la carte : {path!r}")
    if query:
        target += "?" + query
    return target


def probe_map(base_url: str, timeout: float = 5.0, opener=None) -> tuple[bool, str]:
    """GET la racine de la carte — (ok, verdict court). Ne lève JAMAIS."""
    open_fn = opener or urllib.request.urlopen
    try:
        res = open_fn(_resolve(base_url, ""), timeout=timeout)  # noqa: S310 — URL validée par le service
    except MapUnreachable as exc:
        return False, str(exc)
    except urllib.error.HTTPError as exc:
        exc.close()
        return False, f"la carte répond avec le code {exc.code}"
    except Exception as exc:  # noqa: BLE001 — verdict honnête plutôt qu'une 500
        return False, f"injoignable ({exc})"
    try:
        content_type = res.headers.get("Content-Type", "")
        if "html" not in content_type:
            return False, (f"la réponse n'est pas une page web ({content_type or 'type inconnu'}) "
                           "— est-ce bien l'adresse de la carte ?")
        return True, "la carte répond"
    finally:
        res.close()


class MapProbe:
    """Adapter MapProbePort (urllib stdlib, injectable pour les tests)."""

    def __init__(self, timeout: float = 5.0, opener=None) -> None:
        self._timeout = timeout
        self._opener = opener

    def probe(self, url: str) -> tuple[bool, str]:
        return probe_map(url, self._timeout, self._opener)


def open_map_path(base_url: str, path: str, query: str = "",
                  timeout: float = 10.0, opener=None,
                  if_none_match: str = "", if_modified_since: str = "",
                  ) -> tuple[int, dict[str, str], Iterator[bytes]]:
    """Ouvre un fichier de la carte : (status, en-têtes à relayer, chunks).

    Le CACHE NAVIGATEUR est préservé dans les deux sens : BlueMap envoie
    ETag/Last-Modified/Cache-Control sur chaque fichier (constaté :
    max-age=86400) — on les relaie au navigateur, et ses questions
    conditionnelles (If-None-Match/If-Modified-Since) repartent vers la
    carte, dont les 304 (quasi gratuits) sont relayés tels quels. Sans ça,
    chaque déplacement re-téléchargeait toutes les tuiles déjà vues
    (constaté par Jeremy le 19/07/2026).

    Un 404 du serveur de carte est un cas NORMAL (tuiles absentes hors des
    zones rendues) : il est relayé tel quel, pas transformé en erreur."""
    open_fn = opener or urllib.request.urlopen
    request = urllib.request.Request(_resolve(base_url, path, query))
    if if_none_match:
        request.add_header("If-None-Match", if_none_match)
    if if_modified_since:
        request.add_header("If-Modified-Since", if_modified_since)
    try:
        res = open_fn(request, timeout=timeout)  # noqa: S310 — URL validée par le service
    except urllib.error.HTTPError as exc:
        headers = _relayed_headers(exc.headers)
        exc.close()
        if exc.code == 304:  # « rien n'a changé » : le navigateur garde sa copie
            return 304, headers, iter(())
        return exc.code, {"Content-Type": "text/plain"}, iter((b"",))
    except Exception as exc:  # noqa: BLE001
        raise MapUnreachable(f"carte injoignable ({exc})") from exc

    headers = _relayed_headers(res.headers)
    headers.setdefault("Content-Type", "application/octet-stream")

    def chunks() -> Iterator[bytes]:
        try:
            while True:
                block = res.read(_CHUNK)
                if not block:
                    break
                yield block
        finally:
            res.close()

    return res.status, headers, chunks()


# Seuls en-têtes relayés au navigateur : le contenu et sa fraîcheur — jamais
# les en-têtes de transport (connexion, transfer-encoding) ni « Server ».
_RELAYED_HEADER_NAMES = ("Content-Type", "ETag", "Last-Modified", "Cache-Control")


def _relayed_headers(source) -> dict[str, str]:
    if source is None:
        return {}
    return {name: value for name in _RELAYED_HEADER_NAMES
            if (value := source.get(name))}
