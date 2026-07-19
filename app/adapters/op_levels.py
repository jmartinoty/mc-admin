"""Déclenchement du worker one-shot qui applique les niveaux OP."""
from __future__ import annotations

from domain.errors import OpLevelsUnavailable, UnauthorizedContainer

from .docker_proxy import ensure_authorized


class DockerOpLevelsApply:
    def __init__(self, container_name: str, client=None) -> None:
        self._name = container_name
        self._client = client

    def _get_client(self):
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    def apply(self) -> None:
        try:
            container = self._get_client().containers.get(self._name)
            ensure_authorized(getattr(container, "name", self._name), self._name)
            if getattr(container, "status", None) == "running":
                raise OpLevelsUnavailable(
                    "l'application des niveaux OP est déjà en cours"
                )
            container.start()
        except (UnauthorizedContainer, OpLevelsUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001
            raise OpLevelsUnavailable(
                f"conteneur des niveaux OP '{self._name}' non démarrable : {exc}"
            ) from exc

    def _container(self):
        try:
            container = self._get_client().containers.get(self._name)
            ensure_authorized(getattr(container, "name", self._name), self._name)
            return container
        except UnauthorizedContainer:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OpLevelsUnavailable(
                f"état du conteneur des niveaux OP '{self._name}' illisible : {exc}"
            ) from exc

    def is_running(self) -> bool:
        return getattr(self._container(), "status", None) == "running"

    def exit_code(self) -> int | None:
        container = self._container()
        if getattr(container, "status", None) == "running":
            return None
        state = (getattr(container, "attrs", None) or {}).get("State", {})
        value = state.get("ExitCode") if isinstance(state, dict) else None
        return int(value) if value is not None else None
