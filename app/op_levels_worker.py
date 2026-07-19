"""Application transactionnelle des niveaux OP pendant un redémarrage choisi.

Ce module s'exécute dans le conteneur one-shot ``mc-op-levels``. Minecraft est
arrêté avant toute écriture, ``ops.json`` est remplacé atomiquement, puis
conservé uniquement si le serveur redevient healthy. Sinon, l'original est
restauré et la demande retourne dans la file d'attente.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from adapters.atomic_json import atomic_write_json
from adapters.pending_op_levels import JsonPendingOpLevels
from domain.model import PendingOpLevel
from restore_worker import DockerContainerController

_OPS_NAME = "ops.json"
_ROLLBACK_NAME = ".mc-admin-ops-rollback.json"
_STATE_NAME = ".mc-admin-ops-transaction.json"
_MAX_OPS_BYTES = 1024 * 1024


class OpLevelsWorkerError(RuntimeError):
    """La transaction de niveaux OP n'a pas pu aboutir."""


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_replace_bytes(
    path: Path,
    data: bytes,
    *,
    mode: int,
    uid: int,
    gid: int,
) -> None:
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        os.fchmod(fd, mode)
        try:
            os.fchown(fd, uid, gid)
        except PermissionError:
            if uid != os.geteuid() or gid != os.getegid():
                raise
        with os.fdopen(fd, "wb") as output:
            fd = -1
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _read_ops(path: Path) -> tuple[list[dict], bytes, os.stat_result]:
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise OpLevelsWorkerError("ops.json n'est pas un fichier régulier")
        if metadata.st_size > _MAX_OPS_BYTES:
            raise OpLevelsWorkerError("ops.json est anormalement volumineux")
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except OpLevelsWorkerError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpLevelsWorkerError("ops.json est illisible") from exc
    if not isinstance(payload, list):
        raise OpLevelsWorkerError("ops.json n'est pas une liste")
    if not all(isinstance(entry, dict) for entry in payload):
        raise OpLevelsWorkerError("ops.json contient une entrée invalide")
    return payload, raw, metadata


def _encode_ops(entries: list[dict]) -> bytes:
    return (json.dumps(entries, ensure_ascii=True, indent=2) + "\n").encode("utf-8")


def _apply_requested_levels(
    entries: list[dict],
    requested: list[PendingOpLevel],
) -> tuple[int, list[PendingOpLevel]]:
    by_name = {item.name.lower(): item for item in requested}
    matched: set[str] = set()
    changed = 0
    for entry in entries:
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        key = name.lower()
        item = by_name.get(key)
        if item is None:
            continue
        matched.add(key)
        if entry.get("level") != item.level:
            entry["level"] = item.level
            changed += 1
    unresolved = [item for key, item in by_name.items() if key not in matched]
    return changed, unresolved


def _level_payload(items: list[PendingOpLevel]) -> list[dict]:
    return [
        {"name": item.name, "level": item.level}
        for item in items
    ]


def _decode_state_levels(payload: object, field: str) -> list[PendingOpLevel]:
    raw = payload.get(field, []) if isinstance(payload, dict) else []
    if not isinstance(raw, list):
        raise OpLevelsWorkerError("marqueur de transaction OP invalide")
    decoded: list[PendingOpLevel] = []
    for item in raw:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not item["name"]
            or not isinstance(item.get("level"), int)
            or isinstance(item.get("level"), bool)
            or item["level"] not in (1, 2, 3, 4)
        ):
            raise OpLevelsWorkerError("marqueur de transaction OP invalide")
        decoded.append(PendingOpLevel(item["name"], item["level"]))
    return decoded


def _write_state(
    path: Path,
    phase: str,
    requested: list[PendingOpLevel],
    unresolved: list[PendingOpLevel] | None = None,
) -> None:
    atomic_write_json(
        str(path),
        {
            "version": 1,
            "phase": phase,
            "requested": _level_payload(requested),
            "unresolved": _level_payload(unresolved or []),
        },
        ensure_ascii=True,
        mode=0o600,
    )


def _read_state(
    path: Path,
) -> tuple[str, list[PendingOpLevel], list[PendingOpLevel]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OpLevelsWorkerError("marqueur de transaction OP illisible") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(payload.get("phase"), str)
    ):
        raise OpLevelsWorkerError("marqueur de transaction OP invalide")
    return (
        payload["phase"],
        _decode_state_levels(payload, "requested"),
        _decode_state_levels(payload, "unresolved"),
    )


def _cleanup(rollback: Path, state_path: Path) -> None:
    rollback.unlink(missing_ok=True)
    state_path.unlink(missing_ok=True)
    _fsync_directory(state_path.parent)


def _restore_rollback(ops_path: Path, rollback: Path) -> None:
    metadata = rollback.stat()
    _atomic_replace_bytes(
        ops_path,
        rollback.read_bytes(),
        mode=stat.S_IMODE(metadata.st_mode),
        uid=metadata.st_uid,
        gid=metadata.st_gid,
    )


def _recover_interrupted(
    store: JsonPendingOpLevels,
    ops_path: Path,
    rollback: Path,
    state_path: Path,
    controller,
) -> None:
    if not rollback.exists() and not state_path.exists():
        return

    try:
        phase, requested, unresolved = (
            _read_state(state_path)
            if state_path.exists()
            else ("unknown", [], [])
        )
    except OpLevelsWorkerError:
        phase, requested, unresolved = "unknown", [], []
    if phase == "committed":
        store.complete_claim(unresolved)
        _cleanup(rollback, state_path)
        return

    try:
        if rollback.exists():
            controller.stop()
            _restore_rollback(ops_path, rollback)
        controller.start()
        store.restore_claim(requested)
        _cleanup(rollback, state_path)
    except Exception as exc:
        raise OpLevelsWorkerError(
            "transaction OP interrompue et récupération impossible"
        ) from exc
    raise OpLevelsWorkerError(
        "transaction OP interrompue annulée ; niveaux remis en attente"
    )


def apply_pending_levels(
    store: JsonPendingOpLevels,
    data_root: Path,
    controller,
) -> tuple[int, int]:
    if not data_root.is_dir() or data_root.is_symlink():
        raise OpLevelsWorkerError("dossier de données Minecraft invalide")

    ops_path = data_root / _OPS_NAME
    rollback = data_root / _ROLLBACK_NAME
    state_path = data_root / _STATE_NAME
    _recover_interrupted(store, ops_path, rollback, state_path, controller)

    requested = store.claim()
    server_may_be_stopped = False
    rollback_ready = False
    try:
        _write_state(state_path, "claimed", requested)
        server_may_be_stopped = True
        controller.stop()
        _write_state(state_path, "stopped", requested)

        entries, original, metadata = _read_ops(ops_path)
        _atomic_replace_bytes(
            rollback,
            original,
            mode=stat.S_IMODE(metadata.st_mode),
            uid=metadata.st_uid,
            gid=metadata.st_gid,
        )
        rollback_ready = True
        _write_state(state_path, "rollback_ready", requested)

        changed, unresolved = _apply_requested_levels(entries, requested)
        if changed:
            _atomic_replace_bytes(
                ops_path,
                _encode_ops(entries),
                mode=stat.S_IMODE(metadata.st_mode),
                uid=metadata.st_uid,
                gid=metadata.st_gid,
            )
        _write_state(state_path, "applied", requested)

        controller.start()
        server_may_be_stopped = False
        _write_state(state_path, "committed", requested, unresolved)
        store.complete_claim(unresolved)
    except Exception as exc:
        if rollback_ready:
            try:
                controller.stop()
                _restore_rollback(ops_path, rollback)
                controller.start()
                server_may_be_stopped = False
                store.restore_claim(requested)
                _cleanup(rollback, state_path)
            except Exception as rollback_exc:
                raise OpLevelsWorkerError(
                    "application et rollback des niveaux OP en échec"
                ) from rollback_exc
            raise OpLevelsWorkerError(
                f"niveaux OP annulés, original restauré : {exc}"
            ) from exc

        if server_may_be_stopped:
            try:
                controller.start()
                server_may_be_stopped = False
            except Exception as start_exc:
                raise OpLevelsWorkerError(
                    "échec avant écriture et redémarrage Minecraft impossible"
                ) from start_exc
        store.restore_claim(requested)
        _cleanup(rollback, state_path)
        raise OpLevelsWorkerError(
            f"niveaux OP annulés avant écriture : {exc}"
        ) from exc

    try:
        _cleanup(rollback, state_path)
    except Exception as exc:
        print(f"[op-levels] avertissement nettoyage : {exc}", file=sys.stderr, flush=True)
    print(
        f"[op-levels] transaction terminée : {changed} modifié(s), "
        f"{len(unresolved)} encore en attente",
        flush=True,
    )
    return changed, len(unresolved)


def main() -> int:
    try:
        controller = DockerContainerController(
            os.environ.get("MC_CONTAINER_NAME", "minecraft"),
            startup_timeout=float(
                os.environ.get("MC_STARTUP_TIMEOUT_SECONDS", "300")
            ),
        )
        apply_pending_levels(
            JsonPendingOpLevels(
                os.environ.get(
                    "PENDING_OP_LEVELS_FILE",
                    "/state/pending_op_levels.json",
                )
            ),
            Path(os.environ.get("MC_DATA_ROOT", "/worlddata")),
            controller,
        )
    except Exception as exc:  # noqa: BLE001 - frontière du conteneur one-shot
        print(f"[op-levels] ÉCHEC : {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
