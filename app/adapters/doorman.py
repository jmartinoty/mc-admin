"""Adapter DoormanPort.

- `NotConfiguredDoorman` : refus explicite si aucun conteneur mc-doorman n'est
  configuré (MC_DOORMAN_CONTAINER absent) — la maintenance reste possible
  « sans portier » côté service, mais jamais en silence.

- `DockerDoorman` : écrit la consigne (MOTD + message de refus) dans un fichier
  partagé, puis démarre le conteneur `mc-doorman` one-shot via le
  docker-socket-proxy. Le conteneur reprend l'IP statique du serveur (compose)
  et la consigne est lue à SON démarrage — pattern mc-restore/backup-profile :
  aucun paramètre ne transite par l'API Docker.

  `release()` attend la libération effective de l'endpoint : tant que le
  portier tient l'IP, redémarrer minecraft échouerait sur un conflit
  d'adresse — l'attente bornée transforme ce conflit en erreur claire.

Garde-fou de nom dédié (`ensure_authorized`), comme DockerRestoreTrigger.
"""
from __future__ import annotations

import time

from domain.errors import MaintenanceUnavailable, UnauthorizedContainer

from .atomic_json import atomic_write_json, shared_path_lock
from .docker_proxy import ensure_authorized


class NotConfiguredDoorman:
    def engage(self, motd: str, kick: str) -> None:
        raise MaintenanceUnavailable(
            "portier indisponible : aucun conteneur configuré (MC_DOORMAN_CONTAINER)"
        )

    def release(self) -> None:
        return None  # rien à relever : idempotent

    def is_running(self) -> bool:
        return False

    def prime(self, motd: str, kick: str) -> None:
        return None  # aucun portier à amorcer : best-effort, pas d'erreur


class DockerDoorman:
    def __init__(
        self,
        doorman_container: str,
        config_file: str,
        client=None,
        *,
        release_timeout: float = 15.0,
        poll_interval: float = 0.5,
        sleep=None,
        monotonic=None,
    ) -> None:
        self._name = doorman_container
        self._config_file = config_file
        self._client = client  # None -> résolu paresseusement via DOCKER_HOST (=proxy)
        self._release_timeout = release_timeout
        self._poll_interval = poll_interval
        self._sleep = sleep or time.sleep
        self._monotonic = monotonic or time.monotonic

    def _get_client(self):
        if self._client is None:
            import docker  # import paresseux
            self._client = docker.from_env()
        return self._client

    def _container(self):
        container = self._get_client().containers.get(self._name)
        ensure_authorized(getattr(container, "name", self._name), self._name)
        return container

    def _write_consigne(self, motd: str, kick: str) -> None:
        with shared_path_lock(self._config_file):
            atomic_write_json(self._config_file, {"motd": motd, "kick": kick})

    def engage(self, motd: str, kick: str) -> None:
        try:
            container = self._container()
            if getattr(container, "status", None) == "running":
                raise MaintenanceUnavailable("le portier est déjà en place")
            # La consigne est écrite AVANT le start : le portier la lit au démarrage.
            self._write_consigne(motd, kick)
            container.start()
        except (UnauthorizedContainer, MaintenanceUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001 — traduit en erreur métier
            raise MaintenanceUnavailable(
                f"portier '{self._name}' non démarrable : {exc}"
            ) from exc

    def release(self) -> None:
        try:
            container = self._container()
            if getattr(container, "status", None) == "running":
                container.stop(timeout=10)
            deadline = self._monotonic() + self._release_timeout
            while True:
                container = self._container()
                if getattr(container, "status", None) != "running":
                    return
                if self._monotonic() >= deadline:
                    raise MaintenanceUnavailable(
                        f"le portier '{self._name}' ne s'arrête pas — "
                        "ne pas redémarrer le serveur tant qu'il tient l'adresse"
                    )
                self._sleep(self._poll_interval)
        except (UnauthorizedContainer, MaintenanceUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001
            raise MaintenanceUnavailable(
                f"portier '{self._name}' non relevable : {exc}"
            ) from exc

    def is_running(self) -> bool:
        try:
            container = self._container()
        except UnauthorizedContainer:
            raise
        except Exception:  # noqa: BLE001 — conteneur absent/proxy muet = pas en place
            return False
        return getattr(container, "status", None) == "running"

    def prime(self, motd: str, kick: str) -> None:
        """Écrit la consigne sans démarrer le conteneur : le worker mc-restore
        prendra le poste lui-même pendant l'arrêt de Minecraft (V7b). Écrire la
        consigne côté mc-admin reste le pattern one-shot (aucun paramètre par
        l'API Docker). Ne touche PAS au proxy — simple écriture de fichier."""
        self._write_consigne(motd, kick)
