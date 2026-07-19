"""Journal d'audit (tableau filtrable, paginé) et timeline unifiée."""
from __future__ import annotations

import re
import shlex
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.routes.common import _current, _nav_context, templates
from domain.errors import PermissionDenied

router = APIRouter()

# Familles d'événements : chip colorée + filtre rapide. Un refus RBAC est
# toujours classé « sécurité », quelle que soit l'action refusée.
_FAMILY_BY_ACTION = {
    "RESTART": "server", "START": "server", "STOP": "server",
    "UPDATE": "server", "RCON_RAW": "server",
    "BACKUP_TRIGGER": "backup", "BACKUP_DOWNLOAD": "backup",
    "BACKUP_RESTORE": "backup", "BACKUP_RETENTION": "backup",
    "KICK": "players", "BAN_MANAGE": "players",
    "WHITELIST_MANAGE": "players", "OP_MANAGE": "players",
    "GAME_SETTINGS": "game",
}
_FAMILY_LABELS = {
    "all": "Tout",
    "server": "Serveur",
    "backup": "Sauvegardes",
    "players": "Joueurs",
    "ingame": "En jeu",
    "game": "Réglages",
    "security": "Sécurité",
    "other": "Autre",
}

# Libellés français des actions (le nom technique reste visible au survol).
_ACTION_LABELS = {
    "RESTART": "Redémarrage", "START": "Démarrage", "STOP": "Arrêt",
    "UPDATE": "Mise à jour", "RCON_RAW": "Console",
    "BACKUP_TRIGGER": "Sauvegarde", "BACKUP_DOWNLOAD": "Téléchargement",
    "BACKUP_RESTORE": "Restauration", "BACKUP_RETENTION": "Rétention",
    "KICK": "Expulsion", "BAN_MANAGE": "Bannissement",
    "WHITELIST_MANAGE": "Whitelist", "OP_MANAGE": "Opérateurs",
    "GAME_SETTINGS": "Réglages du jeu", "AUDIT_VIEW": "Journal",
    "LOGS_VIEW": "Logs", "STATUS": "État",
}

# Comptes système (threads de fond) : badge « auto » dans la colonne Qui.
_SYSTEM_USERS = {"scheduler", "auto-unban", "auto-restore", "scheduled-restart"}

_MONTHS_FR = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
              "août", "septembre", "octobre", "novembre", "décembre")


def _family(entry) -> str:
    if entry.outcome == "denied":
        return "security"
    return _FAMILY_BY_ACTION.get(entry.action, "other")


def _day_label(ts: datetime) -> str:
    """« Aujourd'hui » / « Hier » / « 12 juillet 2026 » (heure locale)."""
    local = ts.astimezone().date()
    today = datetime.now().astimezone().date()
    if local == today:
        return "Aujourd'hui"
    if (today - local).days == 1:
        return "Hier"
    return f"{local.day} {_MONTHS_FR[local.month - 1]} {local.year}"


# Traduction uniforme des détails d'audit en français (couche présentation :
# le journal BRUT reste la source de vérité, on ne réécrit pas l'historique).
_KICK_RE = re.compile(r"^kick (\w+)(?: \((.*)\))?$")
_BAN_RE = re.compile(r"^ban (\w+)(?: pendant ([\d.]+)h)?(?: \((.*)\))?$")
_PARDON_RE = re.compile(r"^pardon (\w+)$")
_WL_RE = re.compile(r"^(add|remove) (\w+)$")
_OP_RE = re.compile(r"^op (\w+) niveau ([1-4])(?: \((.*)\))?$")
_DEOP_RE = re.compile(r"^deop (\w+)$")


def _operation_line(entry) -> str:
    """Une phrase française par action — repli : le détail brut tel quel."""
    d = (entry.detail or "").strip()
    a = entry.action
    fields = _parse_detail(d)
    if fields.get("phase"):
        phase = fields["phase"]
        line = _PHASE_LABELS.get(phase, phase.replace("_", " "))
        if fields.get("archive"):
            line += f" · {fields['archive']}"
        return line
    if a == "KICK" and (m := _KICK_RE.match(d)):
        return f"{m.group(1)} expulsé" + (f" — {m.group(2)}" if m.group(2) else "")
    if a == "BAN_MANAGE":
        if m := _BAN_RE.match(d):
            line = f"{m.group(1)} banni"
            if m.group(2):
                line += f" pour {m.group(2)} h"
            if m.group(3):
                line += f" — {m.group(3)}"
            return line
        if m := _PARDON_RE.match(d):
            return f"{m.group(1)} débanni"
    if a == "WHITELIST_MANAGE" and (m := _WL_RE.match(d)):
        return f"{m.group(2)} {'ajouté à' if m.group(1) == 'add' else 'retiré de'} la whitelist"
    if a == "OP_MANAGE":
        if m := _OP_RE.match(d):
            suffix = (
                " (niveau au prochain redémarrage)"
                if m.group(3) and "prochain redémarrage" in m.group(3)
                else ""
            )
            return f"{m.group(1)} opérateur niveau {m.group(2)}{suffix}"
        if m := _DEOP_RE.match(d):
            return f"{m.group(1)} n'est plus opérateur"
    if a == "GAME_SETTINGS":
        return d.replace(" -> ", " → ")
    if a == "RCON_RAW":
        return f"commande : {d}" if d else ""
    return d


# Statut affiché (pastille) par issue technique.
# Vocabulaire de statut STANDARD, partagé par toutes les lignes (« fait » ne
# disait pas si ça avait marché) : réussi / échec / refusé / en cours.
_OUTCOME_PILLS = {"allowed": ("réussi", "ok"), "denied": ("refusé", "ko"), "error": ("échec", "ko")}
_GROUP_PILLS = {"success": ("réussi", "ok"), "error": ("échec", "ko"), "running": ("en cours", "info")}
_GROUP_TITLES = {
    "BACKUP_RESTORE": "Restauration du serveur",
    "RESTART": "Redémarrage programmé",
    "START": "Démarrage du serveur",
}
_BACKUP_KIND_TITLES = {"manual": "Sauvegarde manuelle", "scheduled": "Sauvegarde automatique"}
# Phases qui concluent une opération en succès (le statut d'un groupe encore
# ouvert reste « en cours »).
_SUCCESS_PHASES = {
    "restore_completed",
    "minecraft_healthy",
    "backup_done",
    "restart_done",
    "op_levels_applied",
}


def _build_rows(entries) -> list[dict]:
    """Transforme le flux d'événements (plus récent d'abord) en lignes
    d'OPÉRATIONS : les événements partageant un operation_id sont repliés en
    une ligne dépliable (étapes en mini-timeline), positionnée au plus récent."""
    rows: list[dict] = []
    groups: dict[str, dict] = {}
    for e in entries:
        fields = _parse_detail(e.detail)
        op_id = fields.get("operation_id", "")
        if not op_id:
            line = _operation_line(e)
            label, kind = _OUTCOME_PILLS.get(e.outcome, (e.outcome, "ko"))
            rows.append({"type": "entry", "e": e, "ts": e.timestamp, "line": line,
                         "status_label": label, "status_kind": kind,
                         "username": e.username, "raw": e.detail})
            continue
        group = groups.get(op_id)
        if group is None:
            title = _GROUP_TITLES.get(e.action, "Opération")
            if e.action == "RESTART" and op_id.startswith("op-levels::"):
                title = "Redémarrage avec niveaux OP"
            if e.action == "BACKUP_TRIGGER":
                title = _BACKUP_KIND_TITLES.get(fields.get("kind", ""), "Sauvegarde")
            group = {"type": "group", "ts": e.timestamp, "action": e.action,
                     "title": title, "family": _family(e),
                     "archive": "", "username": e.username, "events": [],
                     "phases": set(), "failed_step": ""}
            groups[op_id] = group
            rows.append(group)
        if fields.get("requested_by"):
            group["username"] = fields["requested_by"]
        if not group["archive"] and fields.get("archive"):
            group["archive"] = fields["archive"]
        phase = fields.get("phase", "")
        group["phases"].add(phase)
        group["events"].append({
            "ts": e.timestamp,
            "label": _PHASE_LABELS.get(phase, phase.replace("_", " ") if phase else e.action),
            "outcome": e.outcome,
            "extra": fields.get("delay") or fields.get("detail") or "",
        })
    for group in groups.values():
        if group["phases"] & _SUCCESS_PHASES:
            status = "success"
        elif any(ev["outcome"] == "error" for ev in group["events"]):
            status = "error"
        elif "restart_cancelled" in group["phases"]:
            status = "error"  # opération interrompue : pastille rouge, étape « annulée » visible
        else:
            status = "running"
        group["status_label"], group["status_kind"] = _GROUP_PILLS[status]
        # Étapes du plus RÉCENT au plus ancien : même sens de lecture que le
        # tableau qui les entoure (retour Jeremy, 14/07/2026).
        group["events"].sort(key=lambda ev: ev["ts"], reverse=True)
        if status == "error":
            # La sous-étape fautive remonte dans la ligne repliée : plus besoin
            # de déplier pour savoir OÙ ça a cassé (retour Jeremy).
            failed = next((ev["label"] for ev in group["events"] if ev["outcome"] == "error"), "")
            group["failed_step"] = failed or (group["events"][0]["label"] if group["events"] else "")
    return rows


# Périodes proposées par le filtre « depuis » (clé = valeur du <select>).
_PERIODS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
_PAGE_SIZE = 50
# Fenêtre lue dans le journal : assez large pour que filtres et pagination
# aient de la profondeur, bornée pour ne pas relire tout un JSONL de
# plusieurs années à chaque affichage.
_WINDOW = 2000

_PHASE_LABELS = {
    "restore_requested": "Restauration demandée",
    "restore_rejected": "Restauration refusée",
    "restore_unavailable": "Restauration indisponible",
    "security_backup_started": "Sauvegarde de sécurité lancée",
    "security_backup_done": "Sauvegarde de sécurité terminée",
    "security_backup_failed": "Sauvegarde de sécurité échouée",
    "restore_pending": "Restauration en attente",
    "restore_started": "Restauration lancée",
    "restore_done": "Archive restaurée",
    "minecraft_restarting": "Redémarrage de Minecraft",
    "minecraft_unhealthy_timeout": "Minecraft n'a pas redémarré à temps",
    "minecraft_healthy": "Minecraft est de nouveau en ligne",
    "restore_completed": "Restauration terminée",
    "restore_failed": "Restauration échouée",
    "backup_started": "Sauvegarde lancée",
    "backup_done": "Archive produite",
    "backup_failed": "Aucune archive produite",
    "restart_scheduled": "Redémarrage programmé",
    "restart_cancelled": "Programmation annulée",
    "restart_started": "Redémarrage lancé",
    "restart_done": "Serveur redémarré",
    "op_levels_started": "Application des niveaux OP lancée",
    "op_levels_applied": "Niveaux OP appliqués, Minecraft en ligne",
    "op_levels_failed": "Niveaux OP non appliqués",
    "first_account": "Premier compte créé",
    "account_created": "Compte créé",
    "password_reset": "Mot de passe réinitialisé",
    "role_changed": "Rôle modifié",
    "account_deleted": "Compte supprimé",
}

_DETAIL_LABELS = {
    "archive": "Archive",
    "requested_by": "Demandé par",
    "reason": "Raison",
    "error": "Erreur",
    "health": "État serveur",
    "detail": "Détail",
    "active_phase": "Étape active",
    "account": "Compte",
    "username": "Compte",
    "role": "Rôle",
    "credentials_removed": "Ancien mot de passe supprimé",
    "count": "Niveaux à appliquer",
    "exit_code": "Code de retour",
}

_REASON_LABELS = {
    "archive_not_found": "archive introuvable",
    "health_timeout": "Minecraft n'est pas revenu healthy à temps",
    "not_configured": "restauration non configurée",
    "restore_already_running": "une restauration est déjà en cours",
    "restore_pending": "une restauration attend déjà sa sauvegarde de sécurité",
}


def _parse_detail(detail: str) -> dict[str, str]:
    if not detail:
        return {}
    try:
        tokens = shlex.split(detail)
    except ValueError:
        return {}
    fields: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key:
            fields[key] = value
    return fields


def _format_value(key: str, value: str) -> str:
    if key == "reason":
        return _REASON_LABELS.get(value, value.replace("_", " "))
    if key in ("phase", "active_phase"):
        return _PHASE_LABELS.get(value, value.replace("_", " "))
    if key == "credentials_removed":
        return {"true": "oui", "false": "non"}.get(value.lower(), value)
    return value


def _human_detail(detail: str) -> dict[str, object]:
    fields = _parse_detail(detail)
    if not fields:
        return {"summary": detail, "items": (), "raw": detail}

    phase = fields.get("phase", "")
    summary = _PHASE_LABELS.get(phase, phase.replace("_", " ") if phase else detail)
    items = []
    for key, value in fields.items():
        if key == "phase":
            continue
        label = _DETAIL_LABELS.get(key, key.replace("_", " ").capitalize())
        items.append((label, _format_value(key, value)))
    return {"summary": summary, "items": tuple(items), "raw": detail}


@router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    user = _current(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    # Filtres (GET) : utilisateur / action / résultat / texte libre sur le détail.
    f_user = request.query_params.get("user", "").strip()
    f_action = request.query_params.get("action", "").strip()
    f_outcome = request.query_params.get("outcome", "").strip()
    f_q = request.query_params.get("q", "").strip().lower()
    f_period = request.query_params.get("period", "").strip()
    if f_period not in _PERIODS:
        f_period = ""
    f_family = request.query_params.get("family", "").strip()
    if f_family not in _FAMILY_LABELS or f_family == "all":
        f_family = ""
    filtered = bool(f_user or f_action or f_outcome or f_q or f_period or f_family)
    try:
        entries = request.app.state.service.audit_trail(user, limit=_WINDOW)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    ctx = _nav_context(request, user, "audit")
    # Options des filtres dérivées de la fenêtre COMPLÈTE (avant filtrage).
    # Humains d'abord (alphabetique), comptes systeme des taches de fond en
    # bas — annotes « auto » dans le menu pour ne pas les confondre.
    ctx["audit_users"] = sorted({e.username for e in entries},
                                key=lambda u: (u in _SYSTEM_USERS or u == "scheduler", u.lower()))
    ctx["audit_actions"] = sorted({e.action for e in entries})
    family_counts = {key: 0 for key in _FAMILY_LABELS}
    family_counts["all"] = len(entries)
    for e in entries:
        family_counts[_family(e)] += 1
    try:
        # Même fenêtre que le journal d'audit (_WINDOW) : depuis l'import de
        # l'historique Nitroserv, 300 ne couvrait plus que l'époque récente.
        family_counts["ingame"] = len(
            request.app.state.service.ingame_events(user, limit=_WINDOW)
        )
        family_counts["all"] += family_counts["ingame"]
    except PermissionDenied:
        raise
    ctx["family_counts"] = family_counts
    ctx["family_labels"] = _FAMILY_LABELS
    if f_period:
        cutoff = datetime.now(timezone.utc) - _PERIODS[f_period]
        entries = [e for e in entries if e.timestamp >= cutoff]
    if f_family:
        entries = [e for e in entries if _family(e) == f_family]
    if f_user:
        entries = [e for e in entries if e.username == f_user]
    if f_action:
        entries = [e for e in entries if e.action == f_action]
    if f_outcome:
        entries = [e for e in entries if e.outcome == f_outcome]
    if f_q:
        entries = [e for e in entries if f_q in e.detail.lower()]
    # Événements en jeu (connexions, commandes) : fusionnés au flux, sauf si
    # un filtre purement « audit » (utilisateur/action/résultat) est actif.
    game_events = []
    if not (f_user or f_action or f_outcome) and f_family in ("", "ingame"):
        since = datetime.now(timezone.utc) - _PERIODS[f_period] if f_period else None
        game_events = request.app.state.service.ingame_events(
            user, limit=_WINDOW, since=since
        )
        if since is not None:
            game_events = [ev for ev in game_events if ev[0] >= since]
        if f_q:
            game_events = [ev for ev in game_events
                           if f_q in ev[3].lower() or f_q in ev[1].lower()]
    entries = list(reversed(entries))  # plus récent en premier
    ctx["summary"] = {
        "total": len(entries),
        "errors": sum(1 for e in entries if e.outcome == "error"),
        "denied": sum(1 for e in entries if e.outcome == "denied"),
    }
    rows = _build_rows(entries)
    if f_family == "ingame":
        rows = []
    for ts, player, kind, text in game_events:
        rows.append({"type": "game", "ts": ts, "username": player,
                     "kind": kind, "line": f"{player} {text}"})
    if game_events:
        rows.sort(key=lambda r: r["ts"], reverse=True)
    # Pagination (après regroupement : une opération = une ligne) ; les liens
    # préservent les filtres actifs.
    total = len(rows)
    total_pages = max(1, -(-total // _PAGE_SIZE))
    try:
        page = int(request.query_params.get("page", "1"))
    except ValueError:
        page = 1
    page = max(1, min(page, total_pages))
    ctx["rows"] = rows[(page - 1) * _PAGE_SIZE:page * _PAGE_SIZE]
    ctx["page"], ctx["total_pages"], ctx["total_entries"] = page, total_pages, total
    ctx["filter_qs"] = urlencode({k: v for k, v in (
        ("user", f_user), ("action", f_action), ("outcome", f_outcome),
        ("q", f_q), ("period", f_period), ("family", f_family),
    ) if v})
    ctx["f_user"], ctx["f_action"], ctx["f_outcome"], ctx["f_q"] = f_user, f_action, f_outcome, f_q
    ctx["f_period"], ctx["f_family"] = f_period, f_family
    # Query string SANS famille : les chips de famille reconstruisent la leur.
    ctx["family_qs"] = urlencode({k: v for k, v in (
        ("user", f_user), ("action", f_action), ("outcome", f_outcome),
        ("q", f_q), ("period", f_period),
    ) if v})
    ctx["filtered"] = filtered
    ctx["human_detail"] = _human_detail
    ctx["action_label"] = lambda action: _ACTION_LABELS.get(action, action)
    ctx["entry_family"] = _family
    ctx["day_label"] = _day_label
    ctx["system_users"] = _SYSTEM_USERS
    # Noms affichés : l'audit stocke le pseudo TECHNIQUE (identité immuable) ;
    # seule la présentation résout le nom choisi par l'utilisateur.
    dn = request.app.state.display_names
    usernames = set(ctx["audit_users"]) | {row["username"] for row in ctx["rows"]}
    ctx["names"] = {u: (dn.get(u) or u) for u in usernames}
    return templates.TemplateResponse(request, "audit.html", ctx)


@router.get("/events")
def events_redirect():
    """Les anciennes vues Événements/Timeline ont fusionné dans le Journal."""
    return RedirectResponse("/audit", status_code=303)
