"""Restaurateur transactionnel exécuté par le conteneur mc-restore.

L'archive est extraite et vérifiée avant l'arrêt de Minecraft. Une copie
rollback (reflink sur Btrfs, copie classique ailleurs) est ensuite créée, puis
le staging est synchronisé EN PLACE afin de conserver les inodes exposés à
mc-admin par des bind mounts. Toute erreur après l'arrêt restaure la copie
rollback avant de redémarrer le serveur.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import shutil
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath

from adapters.archive_validation import (
    RESTORE_TRANSACTION_NAMES,
    inspect_archive,
    safe_archive_member_name,
)


_FICLONE = 0x40049409
_REFLINK_FALLBACK_ERRORS = {
    errno.EACCES,
    errno.EINVAL,
    errno.ENOSYS,
    errno.ENOTTY,
    errno.EOPNOTSUPP,
    errno.EPERM,
    errno.EXDEV,
}
_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

_STAGE_NAME = ".mc-restore-stage"
_ROLLBACK_NAME = ".mc-restore-rollback"
_STATE_NAME = ".mc-restore-transaction.json"
_STATE_TMP_NAME = ".mc-restore-transaction.json.tmp"
_PROTECTED_ROOT_NAMES = RESTORE_TRANSACTION_NAMES


class RestoreWorkerError(RuntimeError):
    """La transaction n'a pas pu se terminer comme demandé."""


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _archive_fingerprint(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _set_ownership(
    path: Path,
    uid: int,
    gid: int,
    *,
    follow_symlinks: bool = True,
) -> None:
    os.chown(path, uid, gid, follow_symlinks=follow_symlinks)


def _copy_metadata(
    source: Path,
    destination: Path,
    *,
    follow_symlinks: bool = True,
) -> None:
    source_stat = source.stat() if follow_symlinks else source.lstat()
    _set_ownership(
        destination,
        source_stat.st_uid,
        source_stat.st_gid,
        follow_symlinks=follow_symlinks,
    )
    shutil.copystat(source, destination, follow_symlinks=follow_symlinks)


def _ensure_archive_parents(
    stage: Path,
    parent: Path,
    uid: int,
    gid: int,
) -> None:
    missing: list[Path] = []
    current = parent
    while current != stage and not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir()
        _set_ownership(directory, uid, gid)
        os.chmod(directory, 0o755)


def _resolve_target_archive(target_file: Path, backups_root: Path) -> tuple[str, Path]:
    if target_file.stat().st_size > 4096:
        raise RestoreWorkerError("fichier de consigne anormalement volumineux")
    raw_target = target_file.read_text(encoding="utf-8")
    if "\n" in raw_target or "\r" in raw_target:
        raise RestoreWorkerError("la consigne de restauration doit tenir sur une ligne")
    target = raw_target.strip()
    relative = PurePosixPath(target)
    if (
        not target
        or relative.is_absolute()
        or any(part in ("", ".", "..") for part in relative.parts)
        or not target.endswith(".tar.gz")
    ):
        raise RestoreWorkerError("chemin d'archive cible invalide")

    root = backups_root.resolve(strict=True)
    archive = (root / Path(*relative.parts)).resolve(strict=True)
    try:
        archive.relative_to(root)
    except ValueError as exc:
        raise RestoreWorkerError("l'archive cible sort du dossier des sauvegardes") from exc
    if not archive.is_file():
        raise RestoreWorkerError("archive cible introuvable")
    return target, archive


def _extract_verified_archive(archive_path: Path, stage: Path) -> str:
    before = _archive_fingerprint(archive_path)
    inspection = inspect_archive(archive_path)
    if not inspection.ok:
        raise RestoreWorkerError(f"archive illisible ou dangereuse : {inspection.detail}")
    if not inspection.restorable:
        raise RestoreWorkerError(f"archive non restaurable : {inspection.detail}")

    _remove_path(stage)
    stage.mkdir(mode=0o700)
    directory_metadata: list[tuple[Path, tarfile.TarInfo]] = []
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            for member in archive:
                name = safe_archive_member_name(member.name)
                if member.issym() or member.islnk():
                    raise RestoreWorkerError(f"lien interdit : {member.name}")
                if member.isdev() or member.isfifo():
                    raise RestoreWorkerError(f"fichier spécial interdit : {member.name}")
                if not member.isdir() and not member.isreg():
                    raise RestoreWorkerError(f"type de fichier interdit : {member.name}")
                if name == ".":
                    continue

                destination = stage.joinpath(*PurePosixPath(name).parts)
                if member.isdir():
                    if destination.exists() and not destination.is_dir():
                        raise RestoreWorkerError(f"collision de chemin : {member.name}")
                    _ensure_archive_parents(
                        stage,
                        destination.parent,
                        member.uid,
                        member.gid,
                    )
                    destination.mkdir(exist_ok=True)
                    directory_metadata.append((destination, member))
                    continue

                _ensure_archive_parents(
                    stage,
                    destination.parent,
                    member.uid,
                    member.gid,
                )
                if destination.exists() or destination.is_symlink():
                    raise RestoreWorkerError(f"entrée dupliquée : {member.name}")
                source = archive.extractfile(member)
                if source is None:
                    raise RestoreWorkerError(f"fichier illisible : {member.name}")
                with source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                _set_ownership(destination, member.uid, member.gid)
                os.chmod(destination, member.mode & 0o777)
                os.utime(destination, (member.mtime, member.mtime))
        for destination, member in reversed(directory_metadata):
            _set_ownership(destination, member.uid, member.gid)
            os.chmod(destination, member.mode & 0o777)
            os.utime(destination, (member.mtime, member.mtime))
    except Exception:
        _remove_path(stage)
        raise

    if _archive_fingerprint(archive_path) != before:
        _remove_path(stage)
        raise RestoreWorkerError("l'archive a changé pendant son extraction")
    return inspection.world_name


def _reflink_or_copy(source: Path, destination: Path) -> None:
    try:
        with source.open("rb") as input_file, destination.open("xb") as output_file:
            try:
                fcntl.ioctl(output_file.fileno(), _FICLONE, input_file.fileno())
            except OSError as exc:
                if exc.errno not in _REFLINK_FALLBACK_ERRORS:
                    raise
                input_file.seek(0)
                shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            output_file.flush()
            os.fsync(output_file.fileno())
        _copy_metadata(source, destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _clone_entry(source: Path, destination: Path) -> None:
    if source.is_symlink():
        os.symlink(os.readlink(source), destination)
        _copy_metadata(source, destination, follow_symlinks=False)
        return
    if source.is_dir():
        destination.mkdir()
        for child in list(source.iterdir()):
            _clone_entry(child, destination / child.name)
        _copy_metadata(source, destination)
        return
    if source.is_file():
        _reflink_or_copy(source, destination)
        return
    raise RestoreWorkerError(f"type de fichier actif non pris en charge : {source}")


def _clone_live_tree(data_root: Path, rollback: Path) -> None:
    _remove_path(rollback)
    rollback.mkdir(mode=0o700)
    try:
        for child in list(data_root.iterdir()):
            if child.name in _PROTECTED_ROOT_NAMES:
                continue
            _clone_entry(child, rollback / child.name)
        _copy_metadata(data_root, rollback)
    except Exception:
        _remove_path(rollback)
        raise


def _copy_file_in_place(source: Path, destination: Path) -> None:
    with source.open("rb") as input_file, destination.open("wb") as output_file:
        shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
        output_file.flush()
        os.fsync(output_file.fileno())
    _copy_metadata(source, destination)


def _sync_entry(source: Path, destination: Path) -> None:
    if source.is_symlink():
        target = os.readlink(source)
        if destination.is_symlink() and os.readlink(destination) == target:
            _copy_metadata(source, destination, follow_symlinks=False)
            return
        _remove_path(destination)
        os.symlink(target, destination)
        _copy_metadata(source, destination, follow_symlinks=False)
        return
    if source.is_dir():
        if destination.is_symlink() or (
            destination.exists() and not destination.is_dir()
        ):
            _remove_path(destination)
        if not destination.exists():
            destination.mkdir()
        _sync_tree(source, destination)
        _copy_metadata(source, destination)
        return
    if source.is_file():
        if destination.is_file() and not destination.is_symlink():
            _copy_file_in_place(source, destination)
        else:
            _remove_path(destination)
            shutil.copy2(source, destination, follow_symlinks=False)
            _copy_metadata(source, destination)
        return
    raise RestoreWorkerError(f"type de fichier à synchroniser non pris en charge : {source}")


def _sync_tree(
    source: Path,
    destination: Path,
    protected: frozenset[str] = frozenset(),
    *,
    remove_absent: bool = True,
) -> None:
    source_names = {child.name for child in source.iterdir()}
    if source_names & protected:
        names = ", ".join(sorted(source_names & protected))
        raise RestoreWorkerError(f"noms transactionnels présents dans la source : {names}")

    if remove_absent:
        for child in list(destination.iterdir()):
            if child.name in protected:
                continue
            if child.name not in source_names:
                _remove_path(child)
    for child in list(source.iterdir()):
        _sync_entry(child, destination / child.name)


def _write_state(state_path: Path, phase: str, target: str) -> None:
    temporary = state_path.with_name(_STATE_TMP_NAME)
    payload = {"version": 1, "phase": phase, "target": target}
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=True)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, state_path)


def _read_state(state_path: Path) -> dict | None:
    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RestoreWorkerError("marqueur de transaction illisible") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise RestoreWorkerError("marqueur de transaction invalide")
    return payload


def _cleanup_transaction(stage: Path, rollback: Path, state_path: Path) -> None:
    _remove_path(stage)
    _remove_path(rollback)
    state_path.unlink(missing_ok=True)
    state_path.with_name(_STATE_TMP_NAME).unlink(missing_ok=True)


def _recover_interrupted_transaction(
    data_root: Path,
    controller,
    *,
    sync_tree=_sync_tree,
) -> str | None:
    stage = data_root / _STAGE_NAME
    rollback = data_root / _ROLLBACK_NAME
    state_path = data_root / _STATE_NAME
    if not state_path.exists() and not rollback.exists():
        _remove_path(stage)
        state_path.with_name(_STATE_TMP_NAME).unlink(missing_ok=True)
        return None

    try:
        state = _read_state(state_path) if state_path.exists() else None
    except RestoreWorkerError:
        if not rollback.exists():
            raise
        state = None  # rollback présent : récupération conservatrice
    phase = str((state or {}).get("phase", "unknown"))
    target = str((state or {}).get("target", "archive inconnue"))
    if phase == "started":
        _cleanup_transaction(stage, rollback, state_path)
        return target

    rollback_phases = {
        "rollback_ready",
        "applying",
        "applied",
        "starting",
        "rolling_back",
        "rollback_failed",
    }
    if rollback.exists() and (state is None or phase in rollback_phases):
        controller.stop()
        sync_tree(rollback, data_root, _PROTECTED_ROOT_NAMES)
        controller.start()
        _cleanup_transaction(stage, rollback, state_path)
        raise RestoreWorkerError(
            "une transaction interrompue a été annulée ; l'ancien monde est redémarré"
        )

    if phase in {"staged", "stopping", "stopped", "rollback_ready"}:
        controller.start()
        _cleanup_transaction(stage, rollback, state_path)
        raise RestoreWorkerError(
            "une transaction interrompue avant application a été annulée"
        )

    if phase in {"applied", "starting"}:
        controller.start()
        _cleanup_transaction(stage, rollback, state_path)
        raise RestoreWorkerError(
            "le monde restauré a été redémarré, mais le rollback local était absent"
        )

    raise RestoreWorkerError(
        "transaction interrompue sans rollback utilisable ; "
        "intervention manuelle requise"
    )


def restore_transaction(
    target_file: Path,
    backups_root: Path,
    data_root: Path,
    controller,
    *,
    extract_archive=_extract_verified_archive,
    clone_tree=_clone_live_tree,
    sync_tree=_sync_tree,
) -> str:
    if not data_root.is_dir() or data_root.is_symlink():
        raise RestoreWorkerError("dossier de données Minecraft invalide")
    recovered_target = _recover_interrupted_transaction(
        data_root,
        controller,
        sync_tree=sync_tree,
    )
    if recovered_target is not None:
        return recovered_target

    target, archive_path = _resolve_target_archive(target_file, backups_root)
    stage = data_root / _STAGE_NAME
    rollback = data_root / _ROLLBACK_NAME
    state_path = data_root / _STATE_NAME

    print(f"[restore] extraction et vérification de {target}", flush=True)
    world_name = extract_archive(archive_path, stage)
    print(f"[restore] staging prêt, monde={world_name}", flush=True)
    _write_state(state_path, "staged", target)

    stopped = False
    rollback_ready = False
    try:
        _write_state(state_path, "stopping", target)
        stopped = True
        controller.stop()
        _write_state(state_path, "stopped", target)

        print("[restore] création du rollback local", flush=True)
        clone_tree(data_root, rollback)
        rollback_ready = True
        _write_state(state_path, "rollback_ready", target)

        _write_state(state_path, "applying", target)
        # Une archive de profil peut être partielle et les sauvegardes
        # complètes excluent logs/cache. On conserve donc les entrées racine
        # absentes, tout en remplaçant exactement chaque dossier présent.
        sync_tree(
            stage,
            data_root,
            _PROTECTED_ROOT_NAMES,
            remove_absent=False,
        )
        _write_state(state_path, "applied", target)

        _write_state(state_path, "starting", target)
        controller.start()
        stopped = False
        _write_state(state_path, "started", target)
    except Exception as exc:
        if rollback_ready:
            try:
                _write_state(state_path, "rolling_back", target)
                controller.stop()
                sync_tree(rollback, data_root, _PROTECTED_ROOT_NAMES)
                controller.start()
                stopped = False
                _cleanup_transaction(stage, rollback, state_path)
            except Exception as rollback_exc:
                try:
                    _write_state(state_path, "rollback_failed", target)
                except Exception:
                    pass
                raise RestoreWorkerError(
                    "restauration et rollback en échec ; intervention manuelle requise"
                ) from rollback_exc
            raise RestoreWorkerError(
                f"restauration annulée, rollback appliqué : {exc}"
            ) from exc

        if stopped:
            try:
                controller.start()
                stopped = False
            except Exception as start_exc:
                raise RestoreWorkerError(
                    "échec avant application et redémarrage impossible"
                ) from start_exc
        _cleanup_transaction(stage, rollback, state_path)
        raise RestoreWorkerError(f"restauration annulée avant application : {exc}") from exc

    try:
        _cleanup_transaction(stage, rollback, state_path)
    except Exception as exc:
        print(f"[restore] avertissement nettoyage : {exc}", file=sys.stderr, flush=True)
    print(f"[restore] transaction appliquée : {target}", flush=True)
    return target


class DockerContainerController:
    def __init__(
        self,
        container_name: str,
        client=None,
        *,
        startup_timeout: float = 300,
        poll_interval: float = 2,
        sleep=None,
        monotonic=None,
    ) -> None:
        if not _CONTAINER_NAME_RE.fullmatch(container_name):
            raise RestoreWorkerError("nom de conteneur Minecraft invalide")
        if startup_timeout <= 0 or poll_interval <= 0:
            raise RestoreWorkerError("délais de démarrage Minecraft invalides")
        self._name = container_name
        if client is None:
            import docker

            client = docker.from_env()
        self._client = client
        self._startup_timeout = startup_timeout
        self._poll_interval = poll_interval
        self._sleep = sleep or time.sleep
        self._monotonic = monotonic or time.monotonic

    def _container(self):
        container = self._client.containers.get(self._name)
        actual = str(getattr(container, "name", "")).removeprefix("/")
        if actual != self._name:
            raise RestoreWorkerError("le conteneur Docker résolu ne correspond pas à la cible")
        return container

    def stop(self) -> None:
        container = self._container()
        container.reload()
        if getattr(container, "status", None) == "running":
            container.stop(timeout=120)

    @staticmethod
    def _readiness(container) -> tuple[str, str | None, bool]:
        attrs = getattr(container, "attrs", None) or {}
        state = attrs.get("State", {}) if isinstance(attrs, dict) else {}
        config = attrs.get("Config", {}) if isinstance(attrs, dict) else {}
        healthcheck = config.get("Healthcheck", {}) if isinstance(config, dict) else {}
        test = healthcheck.get("Test") if isinstance(healthcheck, dict) else None
        health_expected = bool(test and test != ["NONE"])
        health = state.get("Health", {}) if isinstance(state, dict) else {}
        health_status = health.get("Status") if isinstance(health, dict) else None
        status = state.get("Status") if isinstance(state, dict) else None
        return str(status or getattr(container, "status", "")), health_status, health_expected

    def start(self) -> None:
        container = self._container()
        container.reload()
        if getattr(container, "status", None) != "running":
            container.start()
        deadline = self._monotonic() + self._startup_timeout
        running_since: float | None = None
        while True:
            container.reload()
            status, health, health_expected = self._readiness(container)
            now = self._monotonic()
            if status == "running":
                if health_expected:
                    if health == "healthy":
                        return
                    if health == "unhealthy":
                        raise RestoreWorkerError(
                            "Minecraft est devenu unhealthy après son démarrage"
                        )
                else:
                    if running_since is None:
                        running_since = now
                    elif now - running_since >= min(2.0, self._startup_timeout):
                        return
            else:
                running_since = None
                if status in {"dead", "exited", "removing"}:
                    raise RestoreWorkerError(
                        f"Minecraft s'est arrêté pendant son démarrage ({status})"
                    )
            if now >= deadline:
                raise RestoreWorkerError(
                    "Minecraft n'est pas devenu healthy dans le délai prévu"
                )
            self._sleep(self._poll_interval)


class DoormanController:
    """Pilote le conteneur portier `mc-doorman` via docker-py, DANS le worker.

    Pendant que Minecraft est arrêté par la restauration, le portier tient son
    endpoint réseau et répond aux joueurs (MOTD « restauration », refus de
    connexion expliqué) au lieu d'un « connexion refusée » brut. La consigne a
    été écrite par mc-admin AVANT le lancement de mc-restore (pattern
    restore-target) : le worker ne fait que démarrer/arrêter le conteneur.

    Contrainte structurante (compose) : `minecraft` et `mc-doorman` se disputent
    la même IP statique et ne peuvent JAMAIS tourner ensemble. `release()`
    attend donc la libération EFFECTIVE de l'adresse avant de rendre la main —
    sans quoi le redémarrage de Minecraft échouerait sur un conflit d'adresse.
    """

    def __init__(
        self,
        container_name: str,
        client=None,
        *,
        release_timeout: float = 15.0,
        poll_interval: float = 0.5,
        sleep=None,
        monotonic=None,
    ) -> None:
        if not _CONTAINER_NAME_RE.fullmatch(container_name):
            raise RestoreWorkerError("nom de conteneur portier invalide")
        if release_timeout <= 0 or poll_interval <= 0:
            raise RestoreWorkerError("délais du portier invalides")
        self._name = container_name
        if client is None:
            import docker

            client = docker.from_env()
        self._client = client
        self._release_timeout = release_timeout
        self._poll_interval = poll_interval
        self._sleep = sleep or time.sleep
        self._monotonic = monotonic or time.monotonic

    def _container(self):
        container = self._client.containers.get(self._name)
        actual = str(getattr(container, "name", "")).removeprefix("/")
        if actual != self._name:
            raise RestoreWorkerError("le conteneur portier résolu ne correspond pas à la cible")
        return container

    def engage(self) -> None:
        container = self._container()
        container.reload()
        if getattr(container, "status", None) != "running":
            container.start()

    def release(self) -> None:
        try:
            container = self._container()
        except RestoreWorkerError:
            raise
        except Exception:  # noqa: BLE001 — aucun conteneur portier = rien ne tient l'adresse
            return
        container.reload()
        if getattr(container, "status", None) == "running":
            container.stop(timeout=10)
        deadline = self._monotonic() + self._release_timeout
        while True:
            try:
                container = self._container()
                container.reload()
            except RestoreWorkerError:
                raise
            except Exception:  # noqa: BLE001 — disparu entre-temps = adresse libérée
                return
            if getattr(container, "status", None) != "running":
                return
            if self._monotonic() >= deadline:
                raise RestoreWorkerError(
                    "le portier ne libère pas l'adresse du serveur — "
                    "redémarrage de Minecraft impossible sans intervention"
                )
            self._sleep(self._poll_interval)


class DoormanAwareController:
    """Enveloppe le contrôleur Minecraft pour que le portier couvre CHAQUE
    fenêtre où Minecraft est volontairement arrêté par la transaction — sans
    toucher à la logique de restauration : les ~8 appels `stop()`/`start()` du
    worker (chemins nominal, rollback ET reprise) sont instrumentés d'un seul
    geste.

    L'ORDRE est imposé par l'IP partagée :
      - `stop()`  : on arrête Minecraft, PUIS le portier prend le poste. Sa
        prise de poste est BEST-EFFORT — un portier absent dégrade seulement le
        message vu par les joueurs, il ne doit jamais faire échouer une
        restauration en cours (ni son rollback).
      - `start()` : on relève le portier (OBLIGATOIRE — sinon conflit
        d'adresse), PUIS on démarre Minecraft. Relever un portier absent est un
        no-op : c'est aussi ce qui nettoie un portier resté en poste après une
        interruption, rendant la reprise plus robuste qu'avant.
    """

    def __init__(self, minecraft, doorman) -> None:
        self._minecraft = minecraft
        self._doorman = doorman

    def stop(self) -> None:
        self._minecraft.stop()
        try:
            self._doorman.engage()
        except Exception as exc:  # noqa: BLE001 — portier = confort, jamais bloquant
            print(
                f"[restore] portier non pris en poste (sans gravité) : {exc}",
                file=sys.stderr,
                flush=True,
            )

    def start(self) -> None:
        self._doorman.release()
        self._minecraft.start()


def main() -> int:
    try:
        startup_timeout = float(
            os.environ.get("MC_STARTUP_TIMEOUT_SECONDS", "300")
        )
        controller = DockerContainerController(
            os.environ.get("MC_CONTAINER_NAME", "minecraft"),
            startup_timeout=startup_timeout,
        )
        doorman_name = os.environ.get("MC_DOORMAN_CONTAINER", "").strip()
        if doorman_name:
            # Portier présent : il couvre les fenêtres d'arrêt de la
            # restauration. Absent (env vide) = comportement V5 inchangé.
            controller = DoormanAwareController(
                controller, DoormanController(doorman_name)
            )
        restore_transaction(
            Path(os.environ.get("RESTORE_TARGET_FILE", "/restore/restore-target")),
            Path(os.environ.get("BACKUPS_ROOT", "/backups")),
            Path(os.environ.get("MC_DATA_ROOT", "/worlddata")),
            controller,
        )
    except Exception as exc:  # noqa: BLE001 - frontière du conteneur one-shot
        print(f"[restore] ÉCHEC : {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
