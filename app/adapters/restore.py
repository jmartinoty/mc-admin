"""Adapter RestorePort.

- `NotConfiguredRestore` : refus explicite si aucun conteneur mc-restore n'est
  configuré (MC_RESTORE_CONTAINER absent).

- `DockerRestoreTrigger` : écrit l'archive cible (chemin relatif dans /backups)
  dans un fichier partagé, puis (re)démarre le conteneur `mc-restore` one-shot
  via le docker-socket-proxy. C'est CE conteneur (privilégié : il monte le
  socket Docker) qui arrête minecraft, extrait l'archive dans le monde, puis
  redémarre — jamais mc-admin, qui ne touche pas au monde.

Garde-fou de nom dédié (`ensure_authorized`), comme DockerBackupTrigger.
"""
from __future__ import annotations

import os

from domain.errors import RestoreUnavailable, UnauthorizedContainer

from .docker_proxy import ensure_authorized


class NotConfiguredRestore:
    def restore(self, archive_relpath: str) -> None:
        raise RestoreUnavailable(
            "restauration indisponible : aucun conteneur configuré (MC_RESTORE_CONTAINER)"
        )

    def is_running(self) -> bool:
        return False

    def exit_code(self) -> int | None:
        return None


class DockerRestoreTrigger:
    def __init__(self, restore_container: str, target_file: str, client=None) -> None:
        self._name = restore_container
        self._target_file = target_file
        self._client = client  # None -> résolu paresseusement via DOCKER_HOST (=proxy)

    def _get_client(self):
        if self._client is None:
            import docker  # import paresseux
            self._client = docker.from_env()
        return self._client

    def _write_target(self, archive_relpath: str) -> None:
        parent = os.path.dirname(self._target_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self._target_file, "w", encoding="utf-8") as fh:
            fh.write(archive_relpath)

    def restore(self, archive_relpath: str) -> None:
        try:
            container = self._get_client().containers.get(self._name)
            ensure_authorized(getattr(container, "name", self._name), self._name)
            if getattr(container, "status", None) == "running":
                raise RestoreUnavailable("une restauration est déjà en cours")
            # La cible est écrite AVANT le start : le conteneur la lit au démarrage.
            self._write_target(archive_relpath)
            container.start()
        except (UnauthorizedContainer, RestoreUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001 — traduit en erreur métier
            raise RestoreUnavailable(
                f"conteneur de restauration '{self._name}' non démarrable : {exc}"
            ) from exc

    def is_running(self) -> bool:
        try:
            container = self._get_client().containers.get(self._name)
            ensure_authorized(getattr(container, "name", self._name), self._name)
        except UnauthorizedContainer:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RestoreUnavailable(
                f"état du conteneur de restauration '{self._name}' illisible : {exc}"
            ) from exc
        return getattr(container, "status", None) == "running"

    def exit_code(self) -> int | None:
        try:
            container = self._get_client().containers.get(self._name)
            ensure_authorized(getattr(container, "name", self._name), self._name)
            if getattr(container, "status", None) == "running":
                return None
            state = (getattr(container, "attrs", None) or {}).get("State", {})
            value = state.get("ExitCode") if isinstance(state, dict) else None
            return int(value) if value is not None else None
        except UnauthorizedContainer:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RestoreUnavailable(
                f"résultat du conteneur de restauration '{self._name}' illisible : {exc}"
            ) from exc
