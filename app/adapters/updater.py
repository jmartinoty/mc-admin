"""Adapter UpdatePort : vérification de version + déclenchement de mc-updater.

check() — AUCUN élargissement du socket-proxy :
  - version actuelle : label `server_version` de la métrique mc-monitor, lue via
    l'API Prometheus (déjà accessible en lecture seule) ;
  - dernière release : manifeste officiel Mojang (piston-meta) ;
  - changelog : lien minecraft.wiki de la release.
  Petit cache TTL (60 s) pour ne pas marteler Mojang à chaque affichage.

apply() — même pattern que le backup : (re)démarre le conteneur one-shot
`mc-updater` via le proxy. C'est CE conteneur qui porte les droits Docker
étendus (pull + recreate du serveur), jamais mc-admin. Garde-fou de cible
(`ensure_authorized`) comme partout.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from domain.errors import ServerUnavailable, UnauthorizedContainer, UpdateUnavailable
from domain.model import UpdateStatus

from .docker_proxy import ensure_authorized

MOJANG_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
CHANGELOG_URL_TEMPLATE = "https://minecraft.wiki/w/Java_Edition_{version}"
_CACHE_TTL_SECONDS = 60


def _parse_version(version: str) -> tuple[int, ...] | None:
    """"26.1" -> (26, 1). None si non numérique (snapshot…) -> comparaison par égalité."""
    try:
        return tuple(int(part) for part in version.split("."))
    except (ValueError, AttributeError):
        return None


def is_newer(latest: str, current: str) -> bool:
    """Vrai si `latest` est strictement plus récente que `current`.

    Comparaison numérique quand possible (26.2 > 26.1) ; sinon repli sur la
    différence stricte (un serveur en snapshot restera signalé différent).
    """
    lv, cv = _parse_version(latest), _parse_version(current)
    if lv is not None and cv is not None:
        return lv > cv
    return latest != current


def current_server_version(prometheus_url: str, fetch=None) -> str | None:
    """Version MC courante (label server_version de mc-monitor via Prometheus).
    None si indisponible — jamais d'erreur."""
    promql = 'minecraft_status_healthy{server_host="minecraft"}'
    url = f"{prometheus_url.rstrip('/')}/api/v1/query?{urllib.parse.urlencode({'query': promql})}"
    try:
        if fetch is None:
            with urllib.request.urlopen(url, timeout=5) as res:  # noqa: S310
                data = json.load(res)
        else:
            data = fetch(url)
        result = (data.get("data") or {}).get("result") or []
        return result[0]["metric"].get("server_version") if result else None
    except Exception:  # noqa: BLE001
        return None


class MinecraftUpdater:
    def __init__(
        self,
        prometheus_url: str,
        updater_container: str,
        fetch=None,
        client=None,
        clock=time.monotonic,
    ) -> None:
        self._prom = prometheus_url.rstrip("/")
        self._name = updater_container
        self._fetch = fetch or self._default_fetch
        self._client = client  # None -> résolu paresseusement via DOCKER_HOST (=proxy)
        self._clock = clock
        self._cache: tuple[float, UpdateStatus] | None = None

    def _default_fetch(self, url: str) -> dict:
        with urllib.request.urlopen(url, timeout=5) as res:  # noqa: S310 — URLs fixes (Prometheus interne, Mojang)
            return json.load(res)

    # ---- check ----

    def _current_version(self) -> str | None:
        """Label server_version exposé par mc-monitor (None si indisponible)."""
        promql = 'minecraft_status_healthy{server_host="minecraft"}'
        url = f"{self._prom}/api/v1/query?query={urllib.parse.quote(promql)}"
        try:
            data = self._fetch(url)
            result = (data.get("data") or {}).get("result") or []
            return result[0]["metric"].get("server_version") if result else None
        except Exception:  # noqa: BLE001 — version courante inconnue = dégradation, pas erreur
            return None

    def _latest_release(self) -> str:
        try:
            data = self._fetch(MOJANG_MANIFEST_URL)
            return data["latest"]["release"]
        except Exception as exc:  # noqa: BLE001
            raise ServerUnavailable("manifeste Mojang injoignable (vérification de version impossible)") from exc

    def check(self) -> UpdateStatus:
        if self._cache is not None:
            stamp, cached = self._cache
            if self._clock() - stamp < _CACHE_TTL_SECONDS:
                return cached
        current = self._current_version()
        latest = self._latest_release()
        status = UpdateStatus(
            current_version=current,
            latest_version=latest,
            update_available=bool(current and latest and is_newer(latest, current)),
            changelog_url=CHANGELOG_URL_TEMPLATE.format(version=latest) if latest else None,
        )
        self._cache = (self._clock(), status)
        return status

    # ---- apply ----

    def _get_client(self):
        if self._client is None:
            import docker  # import paresseux
            self._client = docker.from_env()
        return self._client

    def apply(self) -> None:
        try:
            container = self._get_client().containers.get(self._name)
            ensure_authorized(getattr(container, "name", self._name), self._name)
            if getattr(container, "status", None) == "running":
                raise UpdateUnavailable("une mise à jour est déjà en cours")
            container.start()
        except (UnauthorizedContainer, UpdateUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001 — traduit en erreur métier
            raise UpdateUnavailable(
                f"conteneur '{self._name}' non démarrable (créé via `docker compose --profile tools create` ?) : {exc}"
            ) from exc
