"""Sauvegardes : liste, téléchargement, déclenchement, planification,
rétention, restauration."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from api.activity import activity_payload
from api.routes.common import (
    _current,
    _nav_context,
    _redirect_target,
    _require_csrf,
    flash_error,
    templates,
)
from domain.errors import DomainError, PermissionDenied
from domain.model import Permission

router = APIRouter()


# Onglets de filtre : « Toutes » + un par profil (V6) + « Sécurité ».
# Les archives racine pré-migration restent visibles dans « Toutes »,
# chip « historique ».
_FIXED_TYPES = {"all": "Toutes", "safety": "Sécurité"}

_MONTHS_FR = (
    "",
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

# Catégories proposées par la modale de création (liste FERMÉE, cf. domaine).
_PROFILE_CATEGORY_LABELS = (
    ("all", "Tout le serveur", "monde, mods, configurations, listes — recommandé"),
    ("world", "Monde", "le terrain, les constructions, les coffres, les joueurs"),
    ("mods", "Mods", "les fichiers des mods (.jar)"),
    ("config", "Configurations", "server.properties et le dossier config — réglages du serveur et des mods"),
    ("admin", "Listes & joueurs", "whitelist, opérateurs, bannissements"),
)


def _int_param(request: Request, name: str, default: int, minimum: int, maximum: int) -> int:
    raw = request.query_params.get(name, "")
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _archive_type(filename: str, profiles=()) -> str:
    if filename.startswith("restore-safety/"):
        return "safety"
    for profile in profiles:
        if filename.startswith(f"{profile.dest}/"):
            return profile.id
    return "legacy"


def _archive_view(archive, profiles=()) -> dict[str, object]:
    kind = _archive_type(archive.filename, profiles)
    label = {"safety": "sécurité", "legacy": "historique"}.get(kind)
    if label is None:
        label = next((p.name for p in profiles if p.id == kind), "archive")
    return {
        "archive": archive,
        "kind": kind,
        "label": label,
        "title": f"{label.capitalize()} · {archive.created_at.astimezone().strftime('%d/%m/%Y %H:%M')}",
    }


def _archive_day_label(day: date, today: date) -> str:
    if day == today:
        return "Aujourd’hui"
    if day == today - timedelta(days=1):
        return "Hier"
    return f"{day.day} {_MONTHS_FR[day.month]} {day.year}"


def _archive_timeline(
    archive_views: list[dict[str, object]],
    today: date | None = None,
) -> list[dict[str, object]]:
    local_today = today or datetime.now().astimezone().date()
    groups: list[dict[str, object]] = []
    for item in sorted(
        archive_views,
        key=lambda view: view["archive"].created_at,
        reverse=True,
    ):
        archive = item["archive"]
        day = archive.created_at.astimezone().date()
        if not groups or groups[-1]["date"] != day:
            groups.append({
                "date": day,
                "date_iso": day.isoformat(),
                "label": _archive_day_label(day, local_today),
                "items": [],
                "total_bytes": 0,
            })
        groups[-1]["items"].append(item)
        groups[-1]["total_bytes"] += archive.size_bytes
    return groups


@router.get("/backups", response_class=HTMLResponse)
def backups_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    ctx = _nav_context(request, user, "backups")
    ctx["can_restore"] = user.can(Permission.BACKUP_RESTORE)
    ctx["can_manage"] = user.can(Permission.BACKUP_MANAGE)
    try:
        profiles = request.app.state.service.backup_profiles_list(user)
        archives = request.app.state.service.list_backups(user)
        # Onglets : Toutes + un par profil + Sécurité.
        type_labels = {"all": "Toutes"}
        type_labels.update({p.id: p.name for p in profiles})
        type_labels["safety"] = "Sécurité"
        archive_filter = request.query_params.get("type", "all").strip()
        if archive_filter not in type_labels:
            archive_filter = "all"
        archive_counts = {key: 0 for key in type_labels}
        archive_counts["all"] = len(archives)
        for archive in archives:
            kind = _archive_type(archive.filename, profiles)
            archive_counts[kind] = archive_counts.get(kind, 0) + 1
        filtered_archives = (
            archives if archive_filter == "all"
            else [a for a in archives if _archive_type(a.filename, profiles) == archive_filter]
        )
        # Vue par profil pour les cartes : dernière archive, volume, verdicts.
        checks = request.app.state.service.archive_checks(user)
        profile_views = []
        for profile in profiles:
            own = [a for a in archives if a.filename.startswith(f"{profile.dest}/")]
            last = own[0] if own else None
            for candidate in own:
                if last is None or candidate.created_at > last.created_at:
                    last = candidate
            last_check = checks.get(last.filename) if last else None
            profile_views.append({
                "profile": profile,
                "count": len(own),
                "total_bytes": sum(a.size_bytes for a in own),
                "last": last,
                "last_ok": bool(last_check and last_check.ok),
                "deletable_dest": profile.dest.startswith("profiles/") or profile.dest == "scheduled",
            })
        ctx["profiles"] = profile_views
        ctx["archive_filter"] = archive_filter
        ctx["archive_type_labels"] = type_labels
        ctx["operation_locked"] = bool(
            ctx["activity"]
            and ctx["activity"]["active"]
            and ctx["activity"]["kind"] in ("backup", "restore")
        )
        ctx["archives"] = filtered_archives
        ctx["archive_views"] = [_archive_view(archive, profiles) for archive in filtered_archives]
        ctx["archive_groups"] = _archive_timeline(ctx["archive_views"])
        ctx["archive_counts"] = archive_counts
        ctx["archive_checks"] = checks
        ctx["archive_checking"] = request.app.state.service.archive_checking(user)
        ctx["backup_total_bytes"] = sum(a.size_bytes for a in archives)
        ctx["backup_free_bytes"] = request.app.state.service.backup_free_bytes(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    ctx["profile_categories"] = _PROFILE_CATEGORY_LABELS
    return templates.TemplateResponse(request, "backups.html", ctx)


@router.get("/backups/restore-preflight")
def backup_restore_preflight(request: Request, filename: str):
    user = _current(request)
    if user is None:
        return {"authenticated": False, "ok": False, "issues": ["non authentifié"]}
    try:
        issues = request.app.state.service.restore_preflight(user, filename)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        return {"authenticated": True, "ok": False, "issues": [str(exc)]}
    return {"authenticated": True, "ok": not issues, "issues": list(issues)}


@router.get("/backups/{filename:path}/download")
def backup_download(request: Request, filename: str):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        path = request.app.state.service.backup_download_path(user, filename)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="application/gzip", filename=filename)


@router.get("/backups/activity-status")
def activity_status(request: Request):
    user = _current(request)
    if user is None:
        return {"authenticated": False, "active": False, "pending": False}
    try:
        return activity_payload(request, user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/activity-status")
def global_activity_status(request: Request):
    return activity_status(request)


@router.get("/backups/restore-status")
def restore_status(request: Request):
    return activity_status(request)


@router.get("/backups/archive-checks")
def archive_checks_status(request: Request):
    """Verdicts d'intégrité pour rafraîchir les badges sans recharger la page."""
    user = _current(request)
    if user is None:
        return {"authenticated": False}
    try:
        checks = request.app.state.service.archive_checks(user)
        checking = request.app.state.service.archive_checking(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {
        "authenticated": True,
        "checking": checking,
        "checks": {
            name: {"ok": check.ok, "restorable": check.restorable}
            for name, check in checks.items()
        },
    }


@router.post("/actions/activity-result/dismiss")
def action_dismiss_activity_result(
    request: Request,
    csrf_token: str = Form(...),
    redirect_to: str = Form("/backups"),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.dismiss_activity_result(user)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return RedirectResponse(_redirect_target(redirect_to, "/backups"), status_code=303)


def _profile_form_fields(include, hours, time_hhmm, retention_daily_days, retention_monthly_months):
    """Normalise les champs du formulaire de profil (create/update)."""
    categories = tuple(include) if include else ()
    daily_at = None
    parsed_hours = 0.0
    if hours == "daily":
        daily_at = (time_hhmm or "").strip()
    elif hours not in ("", "0", None):
        parsed_hours = float(hours)
    return categories, parsed_hours, daily_at, int(retention_daily_days or 0), int(retention_monthly_months or 0)


@router.post("/actions/backup-profiles")
def action_profile_create(
    request: Request,
    name: str = Form(...),
    include: list[str] = Form([]),
    hours: str = Form("0"),
    time_hhmm: str = Form("04:00"),
    retention_daily_days: str = Form("0"),
    retention_monthly_months: str = Form("0"),
    csrf_token: str = Form(...),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        cats, h, daily, days, months = _profile_form_fields(
            include, hours, time_hhmm, retention_daily_days, retention_monthly_months)
        profile = request.app.state.service.create_backup_profile(
            user, name, cats, hours=h, daily_at=daily,
            retention_daily_days=days, retention_monthly_months=months)
        request.session["flash"] = f"Configuration « {profile.name} » créée."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DomainError, ValueError) as exc:
        flash_error(request, "La configuration de sauvegarde n'a pas pu être créée", code="BKP-01", details=str(exc))
    return RedirectResponse("/backups", status_code=303)


@router.post("/actions/backup-profiles/{profile_id}/update")
def action_profile_update(
    request: Request,
    profile_id: str,
    name: str = Form(...),
    include: list[str] = Form([]),
    hours: str = Form("0"),
    time_hhmm: str = Form("04:00"),
    retention_daily_days: str = Form("0"),
    retention_monthly_months: str = Form("0"),
    csrf_token: str = Form(...),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        cats, h, daily, days, months = _profile_form_fields(
            include, hours, time_hhmm, retention_daily_days, retention_monthly_months)
        profile = request.app.state.service.update_backup_profile(
            user, profile_id, name, cats, hours=h, daily_at=daily,
            retention_daily_days=days, retention_monthly_months=months)
        request.session["flash"] = f"Configuration « {profile.name} » mise à jour."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DomainError, ValueError) as exc:
        flash_error(request, "La configuration de sauvegarde n'a pas pu être modifiée", code="BKP-02", details=str(exc))
    return RedirectResponse("/backups", status_code=303)


@router.post("/actions/backup-profiles/{profile_id}/run")
def action_profile_run(request: Request, profile_id: str, csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.trigger_profile_backup(user, profile_id)
        request.session["flash"] = "Sauvegarde déclenchée."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "La sauvegarde n'a pas pu démarrer", code="BKP-03", details=str(exc))
    return RedirectResponse("/backups", status_code=303)


@router.post("/actions/backup-profiles/{profile_id}/toggle")
def action_profile_toggle(request: Request, profile_id: str,
                          enabled: str = Form("1"), csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.toggle_backup_profile(user, profile_id, enabled == "1")
        request.session["flash"] = "Configuration reprise." if enabled == "1" else "Configuration suspendue."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "La configuration n'a pas pu être suspendue ou reprise", code="BKP-04", details=str(exc))
    return RedirectResponse("/backups", status_code=303)


@router.post("/actions/backup-profiles/{profile_id}/delete")
def action_profile_delete(request: Request, profile_id: str, csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        request.app.state.service.delete_backup_profile(user, profile_id)
        request.session["flash"] = "Configuration supprimée — ses archives sont conservées."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "La configuration n'a pas pu être supprimée", code="BKP-05", details=str(exc))
    return RedirectResponse("/backups", status_code=303)


@router.post("/actions/backup-profiles/{profile_id}/retention")
def action_profile_retention(request: Request, profile_id: str, csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        profile = next((p for p in request.app.state.service.backup_profiles_list(user)
                        if p.id == profile_id), None)
        if profile is None or not profile.retention_daily_days:
            request.session["flash"] = "Aucune rétention configurée pour cette configuration."
        else:
            deleted = request.app.state.service.apply_backup_retention(
                user, daily_days=profile.retention_daily_days,
                monthly_months=profile.retention_monthly_months, prefix=f"{profile.dest}/")
            request.session["flash"] = (
                f"Rétention appliquée : {deleted} archive(s) supprimée(s)." if deleted
                else "Rétention appliquée : rien à supprimer.")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DomainError, ValueError) as exc:
        flash_error(request, "La rétention n'a pas pu être enregistrée", code="BKP-06", details=str(exc))
    return RedirectResponse("/backups", status_code=303)


@router.post("/actions/retention/apply")
def action_retention_apply(
    request: Request,
    daily_days: str = Form("30"),
    monthly_months: str = Form("12"),
    csrf_token: str = Form(...),
):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        deleted = request.app.state.service.apply_backup_retention(
            user, daily_days=int(daily_days), monthly_months=int(monthly_months)
        )
        request.session["flash"] = (
            f"Rétention appliquée : {deleted} archive(s) supprimée(s)." if deleted
            else "Rétention appliquée : rien à supprimer."
        )
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (DomainError, ValueError) as exc:
        flash_error(request, "La rétention n'a pas pu être appliquée", code="BKP-07", details=str(exc))
    return RedirectResponse("/backups", status_code=303)


@router.post("/actions/restore")
def action_restore(request: Request, filename: str = Form(...), csrf_token: str = Form(...)):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    _require_csrf(request, csrf_token)
    try:
        outcome = request.app.state.service.restore_backup(user, filename)
        if outcome == "pending":
            request.session["flash"] = (
                "Sauvegarde de sécurité du monde actuel en cours — la restauration "
                f"depuis {filename} démarrera automatiquement dès qu'elle sera terminée."
            )
        else:
            request.session["flash"] = f"Restauration lancée depuis {filename} — le serveur va redémarrer."
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DomainError as exc:
        flash_error(request, "La restauration n'a pas pu démarrer", code="RST-01", details=str(exc))
    return RedirectResponse("/backups", status_code=303)
