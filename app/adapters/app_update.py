"""Mise à jour de mc-admin LUI-MÊME (bouton MAJ) — adapters.

Quatre pièces, toutes bornées :
  - GithubReleases : dernière release publiée du dépôt public (API GitHub,
    non authentifiée — la cadence est tenue par le checker, 1 passe / 24 h) ;
  - JsonAppUpdate : verdict persisté (/data/app_update.json) ;
  - DockerAppUpdater : démarre le one-shot privilégié `mc-admin-updater`
    via le proxy (droit start déjà autorisé — c'est LUI qui a le socket,
    jamais mc-admin, même modèle que mc-updater/mc-restore) ;
  - DockerSelfImage : image du conteneur mc-admin courant (hostname = id
    de conteneur), pour distinguer install ghcr (bouton applicable) et
    build git local (mise à jour par le dépôt).
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime

from adapters.atomic_json import atomic_write_json, load_json, shared_path_lock
from adapters.docker_proxy import ensure_authorized
from domain.errors import UnauthorizedContainer, UpdateUnavailable
from domain.model import AppRelease, AppUpdateSnooze

# Le corps des notes est borné : la carte n'affiche qu'un résumé, et le
# fichier de verdict ne doit pas grossir avec des notes fleuves.
_NOTES_MAX_CHARS = 4000
USER_AGENT = "mc-admin (https://github.com/jmartinoty/mc-admin)"


class GithubReleases:
    """AppReleaseCatalogPort — GET releases/latest, None sur toute erreur."""

    def __init__(self, repo: str, fetch=None, timeout: float = 10.0) -> None:
        self._url = f"https://api.github.com/repos/{repo}/releases/latest"
        self._fetch = fetch or self._default_fetch
        self._timeout = timeout

    def _default_fetch(self, url: str) -> dict:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=self._timeout) as res:  # noqa: S310 — URL fixe GitHub
            return json.load(res)

    def latest(self) -> AppRelease | None:
        try:
            data = self._fetch(self._url)
            version = str(data.get("tag_name") or "").lstrip("v").strip()
            if not version:
                return None
            return AppRelease(
                version=version,
                notes=str(data.get("body") or "")[:_NOTES_MAX_CHARS],
                url=str(data.get("html_url") or ""),
            )
        except Exception:  # noqa: BLE001 — le checker garde le dernier verdict
            return None


class JsonAppUpdate:
    """AppUpdateStatePort — verdict du checker, écritures atomiques."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)

    def get(self) -> tuple[AppRelease, datetime] | None:
        data = load_json(self._path, expected_type=dict, default_factory=dict)
        version = data.get("version")
        checked = data.get("checked_at")
        if not isinstance(version, str) or not version or not isinstance(checked, str):
            return None
        try:
            checked_at = datetime.fromisoformat(checked)
        except ValueError:
            return None
        release = AppRelease(version=version,
                             notes=str(data.get("notes") or ""),
                             url=str(data.get("url") or ""))
        return release, checked_at

    def set(self, release: AppRelease, checked_at: datetime) -> None:
        with self._lock:
            atomic_write_json(self._path, {
                "version": release.version,
                "notes": release.notes,
                "url": release.url,
                "checked_at": checked_at.isoformat(),
            }, ensure_ascii=False, indent=2)


class JsonAppUpdateSnooze:
    """AppUpdateSnoozePort — « me le rappeler plus tard », écritures atomiques."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)

    def get(self) -> AppUpdateSnooze | None:
        data = load_json(self._path, expected_type=dict, default_factory=dict)
        version = data.get("version")
        until = data.get("until")
        if not isinstance(version, str) or not version or not isinstance(until, str):
            return None
        try:
            return AppUpdateSnooze(version=version, until=datetime.fromisoformat(until))
        except ValueError:
            return None

    def set(self, snooze: AppUpdateSnooze) -> None:
        with self._lock:
            atomic_write_json(self._path, {
                "version": snooze.version,
                "until": snooze.until.isoformat(),
            }, ensure_ascii=False, indent=2)


class DockerAppUpdater:
    """AppUpdaterPort — démarre `mc-admin-updater` (pré-créé, profil tools)."""

    def __init__(self, container_name: str = "mc-admin-updater", client=None) -> None:
        self._name = container_name
        self._client = client  # None -> résolu paresseusement via DOCKER_HOST (=proxy)

    def _get_client(self):
        if self._client is None:
            import docker  # import paresseux
            self._client = docker.from_env()
        return self._client

    def start(self) -> None:
        try:
            container = self._get_client().containers.get(self._name)
            ensure_authorized(getattr(container, "name", self._name), self._name)
            if getattr(container, "status", None) == "running":
                raise UpdateUnavailable("une mise à jour de mc-admin est déjà en cours")
            container.start()
        except (UnauthorizedContainer, UpdateUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001 — traduit en erreur métier
            raise UpdateUnavailable(
                f"conteneur '{self._name}' non démarrable (créé via `docker compose --profile tools create` ?) : {exc}"
            ) from exc


class DockerSelfImage:
    """SelfImagePort — image du conteneur courant. Le hostname d'un conteneur
    est son id : l'inspection passe par le proxy (CONTAINERS=1 l'autorise
    déjà). Chaîne vide si indéterminable (hors Docker, proxy down…)."""

    def __init__(self, client=None, hostname: str | None = None) -> None:
        self._client = client
        self._hostname = hostname

    def _get_client(self):
        if self._client is None:
            import docker  # import paresseux
            self._client = docker.from_env()
        return self._client

    def image(self) -> str:
        try:
            container_id = self._hostname or os.uname().nodename
            container = self._get_client().containers.get(container_id)
            return str(container.attrs.get("Config", {}).get("Image") or "")
        except Exception:  # noqa: BLE001 — indéterminable = ""
            return ""
