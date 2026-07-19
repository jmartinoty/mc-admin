"""Projection UI des opérations longues visibles dans le shell.

Le domaine reste la source de vérité. Ce module ne persiste rien et n'invente
aucun progrès : il assemble seulement les états déjà exposés par AdminService.
"""
from __future__ import annotations

from domain.errors import DomainError
from domain.model import Permission


def _idle_payload() -> dict[str, object]:
    return {
        "authenticated": True,
        "active": False,
        "pending": False,
        "kind": "idle",
        "phase": "idle",
        "title": "Aucune opération",
        "message": "Aucune opération en cours.",
        "progress_percent": 100,
        "archive": None,
        "detail_url": None,
        "detail_label": None,
    }


def activity_payload(request, user) -> dict[str, object]:
    """Retourne l'activité la plus importante que ``user`` peut consulter.

    Priorité : restauration active, sauvegarde active, redémarrage programmé,
    résultat de restauration, résultat de sauvegarde, repos.
    """
    service = request.app.state.service
    can_backups = user.can(Permission.BACKUP_TRIGGER)
    can_restore = user.can(Permission.BACKUP_RESTORE)
    can_restart = user.can(Permission.RESTART)

    backup = None
    restore = None
    restore_result = None
    if can_backups:
        try:
            backup = service.backup_progress(user)
        except DomainError:
            backup = None
    if can_restore:
        try:
            restore = service.restore_progress(user)
        except DomainError:
            restore = None

    if restore is not None and restore.phase != "idle":
        if restore.phase in ("success", "failed"):
            restore_result = restore
        else:
            pending = restore.pending
            return {
                "authenticated": True,
                "active": True,
                "pending": pending is not None,
                "kind": "restore",
                "phase": restore.phase,
                "level": restore.level,
                "title": "Restauration en cours",
                "message": restore.message,
                "filename": pending.filename if pending is not None else restore.filename,
                "requested_by": pending.requested_by if pending is not None else None,
                "progress_percent": restore.progress_percent,
                "archive": (
                    restore.security_archive.filename
                    if restore.security_archive
                    else restore.filename
                ),
                "operation_id": restore.operation_id,
                "detail_url": "/backups",
                "detail_label": "Voir les sauvegardes",
            }

    if backup is not None:
        if backup.label:
            label = f"« {backup.label} »"
        elif backup.kind == "scheduled":
            label = "automatique"
        elif backup.kind == "safety":
            label = "de sécurité"
        else:
            label = "manuelle"
        return {
            "authenticated": True,
            "active": True,
            "pending": False,
            "kind": "backup",
            "phase": "backup",
            "title": f"Sauvegarde {label} en cours",
            "message": "",
            "filename": None,
            "requested_by": None,
            "progress_percent": backup.progress_percent,
            "archive": backup.archive.filename if backup.archive else None,
            "detail_url": "/backups",
            "detail_label": "Voir les sauvegardes",
        }

    if can_restart:
        try:
            restart = service.scheduled_restart_status(user)
        except DomainError:
            restart = None
        if restart is not None:
            local_restart = restart.restart_at.astimezone()
            return {
                "authenticated": True,
                "active": True,
                "pending": True,
                "kind": "restart",
                "phase": "restart_scheduled",
                "title": "Redémarrage programmé",
                "message": (
                    f"Prévu le {local_restart.strftime('%d/%m à %H:%M:%S')} "
                    f"par {restart.scheduled_by}."
                ),
                "filename": None,
                "requested_by": restart.scheduled_by,
                "progress_percent": None,
                "archive": None,
                "detail_url": "/",
                "detail_label": "Voir le serveur",
                "action_url": "/actions/cancel-restart",
                "action_label": "Annuler le redémarrage",
            }

    if restore_result is not None:
        return {
            "authenticated": True,
            "active": False,
            "pending": False,
            "kind": "restore",
            "phase": restore_result.phase,
            "level": restore_result.level,
            "title": (
                "Restauration réussie"
                if restore_result.phase == "success"
                else "Restauration échouée"
            ),
            "message": restore_result.message,
            "filename": restore_result.filename,
            "progress_percent": 100,
            "archive": restore_result.filename,
            "operation_id": restore_result.operation_id,
            "detail_url": (
                f"/audit?q={restore_result.operation_id}"
                if restore_result.operation_id
                else "/backups"
            ),
            "detail_label": (
                "Voir le journal" if restore_result.operation_id else "Voir les sauvegardes"
            ),
        }

    if can_backups:
        try:
            outcome = service.last_backup_outcome(user)
        except DomainError:
            outcome = None
        if outcome is not None:
            if outcome.label:
                label = f"« {outcome.label} »"
            else:
                label = "automatique" if outcome.kind == "scheduled" else "manuelle"
            return {
                "authenticated": True,
                "active": False,
                "pending": False,
                "kind": "backup",
                "phase": "success" if outcome.ok else "failed",
                "title": (
                    f"Sauvegarde {label} terminée"
                    if outcome.ok
                    else f"Sauvegarde {label} échouée"
                ),
                "message": "" if outcome.ok else "Aucune archive n'a été produite.",
                "filename": outcome.archive,
                "progress_percent": 100,
                "archive": outcome.archive,
                "operation_id": None,
                "detail_url": "/backups",
                "detail_label": "Voir les sauvegardes",
            }

    return _idle_payload()
