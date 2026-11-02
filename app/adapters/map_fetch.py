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

_CHUNK = 64 * 1024

# BlueMap 5 suit les joueurs par un FLUX d'événements permanent (SSE) et non
# par des requêtes périodiques : `…/live/sse` reste ouvert et n'émet que
# lorsqu'un joueur bouge. Deux réglages parfaits pour des fichiers le tuaient
# (constaté le 23/08/2026, joueurs figés jusqu'au rechargement de la page) :
# le délai d'attente coupait la connexion au premier silence, et la lecture
# par blocs de 64 Kio retenait des événements de quelques dizaines d'octets.
# Un flux se relaie donc ligne par ligne, avec un délai d'attente LONG.
_SUFFIXE_FLUX = "live/sse"

# …mais jamais sans délai du tout. Le relais lit le flux dans un thread du
# pool de l'application (40 en tout, partagés avec toutes les pages), et un
# thread bloqué en lecture ne peut pas être interrompu : quand le navigateur
# ferme l'onglet, le thread reste pris jusqu'au prochain événement de BlueMap.
# Or BlueMap se tait tant que personne ne bouge (mesuré le 02/11/2026 :
# aucune ligne en 150 s, serveur vide) — chaque onglet carte fermé gardait
# donc un thread, et 40 fermetures figeaient toute l'interface. Après ce
# silence, le relais termine proprement le flux : l'EventSource de BlueMap
# repasse brièvement en interrogation périodique puis se reconnecte seul
# (comportement lu dans son code). Un onglet fermé libère ainsi son thread
# en 30 s au plus.
_SILENCE_FLUX_SECONDES = 30.0


def _est_flux(path: str) -> bool:
    return (path or "").rstrip("/").endswith(_SUFFIXE_FLUX)


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
                  accept_encoding: str = "",
                  silence_flux: float = _SILENCE_FLUX_SECONDES,
                  ) -> tuple[int, dict[str, str], RelayedBody]:
    """Ouvre un fichier de la carte : (status, en-têtes à relayer, chunks).

    Le CACHE NAVIGATEUR est préservé dans les deux sens : BlueMap envoie
    ETag/Last-Modified/Cache-Control sur chaque fichier (constaté :
    max-age=86400) — on les relaie au navigateur, et ses questions
    conditionnelles (If-None-Match/If-Modified-Since) repartent vers la
    carte, dont les 304 (quasi gratuits) sont relayés tels quels. Sans ça,
    chaque déplacement re-téléchargeait toutes les tuiles déjà vues
    (constaté par Jeremy le 19/07/2026).

    La COMPRESSION est négociée de bout en bout : les tuiles hires de
    BlueMap sont pré-compressées sur disque (.prbm.gz) et ne sont servies
    compressées que si le client annonce gzip — sans ça, chaque tuile
    traverse le réseau en clair (mesuré le 20/07/2026 : 898 Ko au lieu de
    58 Ko, ×15). Seul gzip est annoncé à la carte : c'est son format natif,
    et tous les navigateurs le décodent.

    Un 404 du serveur de carte est un cas NORMAL (tuiles absentes hors des
    zones rendues) : il est relayé tel quel, pas transformé en erreur —
    mais avec un petit cache navigateur (BlueMap n'en met aucun) : sans ça,
    chaque déplacement re-demande TOUTES les tuiles vides autour de la vue.
    Au pire, une tuile fraîchement rendue apparaît avec une minute de
    retard."""
    open_fn = opener or urllib.request.urlopen
    flux = _est_flux(path)
    if flux:
        # Le silence est NORMAL sur un flux : il ne veut pas dire que la carte
        # est injoignable — il met seulement fin à ce flux-ci (cf. plus haut).
        timeout = silence_flux
    request = urllib.request.Request(_resolve(base_url, path, query))
    if if_none_match:
        request.add_header("If-None-Match", if_none_match)
    if if_modified_since:
        request.add_header("If-Modified-Since", if_modified_since)
    # Jamais de compression sur un flux : elle réintroduirait exactement la
    # mise en tampon qu'on cherche à supprimer.
    gzip_ok = (not flux) and "gzip" in accept_encoding.lower()
    if gzip_ok:
        request.add_header("Accept-Encoding", "gzip")
    try:
        res = open_fn(request, timeout=timeout)  # noqa: S310 — URL validée par le service
    except urllib.error.HTTPError as exc:
        headers = _relayed_headers(exc.headers)
        exc.close()
        if exc.code == 304:  # « rien n'a changé » : le navigateur garde sa copie
            return 304, headers, iter(())
        headers.setdefault("Content-Type", "text/plain")
        if exc.code == 404:
            headers.setdefault("Cache-Control", "max-age=60")
        return exc.code, headers, iter((b"",))
    except Exception as exc:  # noqa: BLE001
        raise MapUnreachable(f"carte injoignable ({exc})") from exc

    headers = _relayed_headers(res.headers)
    headers.setdefault("Content-Type", "application/octet-stream")
    if gzip_ok:
        # La réponse dépend d'Accept-Encoding : un cache partagé éventuel ne
        # doit jamais servir du gzip à un client qui ne l'a pas demandé.
        headers.setdefault("Vary", "Accept-Encoding")

    return res.status, headers, RelayedBody(res, flux)


class RelayedBody:
    """Corps relayé, lu morceau par morceau, et FERMABLE de l'extérieur.

    Un générateur ne suffisait pas : sa clause `finally` ne s'exécute que si
    on le ferme explicitement, ou quand Python finit par le ramasser. Quand le
    navigateur quittait la page Carte en plein flux, le serveur web abandonnait
    l'itération sans fermer le générateur : la connexion vers BlueMap restait
    ouverte, orpheline, ses données s'accumulant sans lecteur (constaté en
    production le 02/11/2026). `close()` ferme la réponse amont directement,
    même si l'itération n'a jamais commencé ; elle est sans effet la 2e fois.
    """

    def __init__(self, response, flux: bool) -> None:
        self._res = response
        self._flux = flux
        self._closed = False

    def __iter__(self) -> "RelayedBody":
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        try:
            # Un flux se lit LIGNE PAR LIGNE : le protocole SSE est découpé
            # ainsi, et chaque ligne doit partir aussitôt.
            block = self._res.readline() if self._flux else self._res.read(_CHUNK)
        except TimeoutError:
            if not self._flux:
                self.close()
                raise
            block = b""  # silence prolongé : fin propre, BlueMap se reconnecte
        except BaseException:
            self.close()
            raise
        if not block:
            self.close()
            raise StopIteration
        return block

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._res.close()


# Seuls en-têtes relayés au navigateur : le contenu, son encodage et sa
# fraîcheur — jamais les en-têtes de transport (connexion, transfer-encoding)
# ni « Server ». Content-Encoding/Content-Length restent exacts : urllib ne
# décode jamais le corps, les octets passent tels quels.
_RELAYED_HEADER_NAMES = ("Content-Type", "Content-Encoding", "Content-Length",
                         "ETag", "Last-Modified", "Cache-Control", "Vary")


def _relayed_headers(source) -> dict[str, str]:
    if source is None:
        return {}
    return {name: value for name in _RELAYED_HEADER_NAMES
            if (value := source.get(name))}
