"""Adapter BackupPort.

- `DockerBackupTrigger` : déclenche une sauvegarde en (re)démarrant un conteneur
  `itzg/mc-backup` dédié via le docker-socket-proxy. Ce conteneur est configuré
  en one-shot (`BACKUP_INTERVAL=0` -> une passe puis exit) et gère lui-même la
  cohérence (RCON save-off / save-all / save-on) + l'archive. L'adapter ne fait
  qu'un `start` : PAS de docker exec, et le port reste ignorant de Docker.

Garde-fou : comme pour ContainerPort, l'adapter est lié à UN conteneur et refuse
toute autre cible (`ensure_authorized`, cf. risque résiduel du proxy — CLAUDE.md §7.1).
"""
from __future__ import annotations

import os

from domain.errors import BackupUnavailable, UnauthorizedContainer
from domain.model import BackupResult

from .docker_proxy import ensure_authorized


class DockerBackupTrigger:
    def __init__(self, backup_container: str, client=None) -> None:
        self._name = backup_container
        self._client = client  # None -> résolu paresseusement via DOCKER_HOST (=proxy)

    def _get_client(self):
        if self._client is None:
            import docker  # import paresseux
            self._client = docker.from_env()
        return self._client

    def trigger(self) -> BackupResult:
        try:
            container = self._get_client().containers.get(self._name)
            ensure_authorized(getattr(container, "name", self._name), self._name)
            if getattr(container, "status", None) == "running":
                # Une sauvegarde one-shot est déjà en cours : on n'en empile pas.
                return BackupResult(started=False, detail="une sauvegarde est déjà en cours")
            container.start()
        except UnauthorizedContainer:
            raise
        except Exception as exc:  # noqa: BLE001 — traduit en erreur métier
            raise BackupUnavailable(
                f"conteneur de backup '{self._name}' non démarrable : {exc}"
            ) from exc
        return BackupResult(started=True, detail=f"sauvegarde déclenchée ({self._name})")

    def is_running(self) -> bool:
        try:
            container = self._get_client().containers.get(self._name)
            ensure_authorized(getattr(container, "name", self._name), self._name)
        except UnauthorizedContainer:
            raise
        except Exception as exc:  # noqa: BLE001 — état inconnu = erreur explicite, jamais un faux "fini"
            raise BackupUnavailable(
                f"état du conteneur de backup '{self._name}' illisible : {exc}"
            ) from exc
        return getattr(container, "status", None) == "running"

    def exit_code(self) -> int | None:
        """Code de fin du dernier passage one-shot.

        None signifie que le conteneur tourne encore ou que Docker ne fournit
        pas de résultat exploitable. L'appelant ne doit jamais l'assimiler à un
        succès.
        """
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
            raise BackupUnavailable(
                f"résultat du conteneur de backup '{self._name}' illisible : {exc}"
            ) from exc


class DockerProfileBackup(DockerBackupTrigger):
    """Déclencheur du conteneur générique des PROFILS (V6).

    Écrit la consigne (fichier env sourcé par l'entrypoint du conteneur)
    AVANT le start — même pattern que DockerRestoreTrigger. Les valeurs
    arrivent déjà validées par le domaine (dest = sous-dossier contrôlé,
    motifs issus d'une liste fermée) ; on refuse par défense en profondeur
    tout caractère de contrôle qui casserait le format ligne à ligne."""

    def __init__(self, backup_container: str, env_file: str, client=None) -> None:
        super().__init__(backup_container, client)
        self._env_file = env_file

    def trigger_profile(self, dest: str, name: str, includes: str, excludes: str) -> BackupResult:
        for value in (dest, name, includes, excludes):
            if "\n" in value or "'" in value:
                raise BackupUnavailable(f"consigne de sauvegarde invalide : {value!r}")
        parent = os.path.dirname(self._env_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = f"{self._env_file}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(
                f"DEST_DIR='/backups/{dest}'\n"
                f"BACKUP_NAME='{name}'\n"
                f"INCLUDES='{includes}'\n"
                f"EXCLUDES='{excludes}'\n"
            )
        os.replace(tmp, self._env_file)
        return self.trigger()
