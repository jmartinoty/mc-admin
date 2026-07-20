"""Mises à jour contrôlées : le serveur Minecraft (V2.3) et mc-admin
lui-même (bouton MAJ).

Découpage de services.py (V7.0) — RBAC/audit via ServiceCore (base.py),
assemblé dans la façade AdminService (__init__.py).
"""
from __future__ import annotations

from domain.errors import (
    ServerUnavailable,
    UpdateBlocked,
    UpdateUnavailable,
)
from domain.model import (
    AppUpdateStatus,
    Permission,
    UpdateStatus,
    User,
)


def _version_tuple(version: str) -> tuple[int, ...] | None:
    """« 0.9.1 » -> (0, 9, 1). None si le format n'est pas numérique."""
    try:
        return tuple(int(part) for part in version.strip().split("."))
    except (ValueError, AttributeError):
        return None


def is_newer_app_version(latest: str, current: str) -> bool:
    """Vrai si `latest` est strictement plus récente que `current` (semver
    numérique ; formats exotiques = différence stricte, comme is_newer MC)."""
    lv, cv = _version_tuple(latest), _version_tuple(current)
    if lv is not None and cv is not None:
        return lv > cv
    return latest != current


# Format officiel des pseudos Minecraft. Validé avant TOUT envoi RCON.



class UpdateMixin:
    # ---- mise à jour contrôlée (barrière UPDATE ; garde-fous V2.3) ----

    def update_status(self, user: User) -> UpdateStatus:
        """Versions actuelle/cible + changelog (lecture, non auditée en succès)."""
        self._authorize(user, Permission.UPDATE)
        return self._updater.check()

    def apply_update(self, user: User, force: bool = False) -> None:
        """Déclenche la mise à jour, dans l'ordre des garde-fous :

        1. refus si des joueurs sont connectés — ou si leur nombre est INCONNU
           (RCON down) — sauf confirmation explicite (`force`) ;
        2. avertissement in-game (`say`) puis `save-all` avant l'arrêt
           (best-effort : si RCON tombe entre-temps, l'étape est tracée) ;
        3. démarrage du conteneur mc-updater (pull + recreate) ;
        4. chaque issue est auditée avec le détail des étapes exécutées.
        """
        self._authorize(user, Permission.UPDATE)

        players_unknown = False
        try:
            players = self._game.list_players()
        except ServerUnavailable:
            players, players_unknown = [], True

        if (players or players_unknown) and not force:
            detail = (
                "nombre de joueurs inconnu (RCON indisponible)"
                if players_unknown
                else f"{len(players)} joueur(s) connecté(s)"
            )
            self._record(user, Permission.UPDATE, "denied", f"bloqué : {detail}, confirmation requise")
            raise UpdateBlocked(f"{detail} — cocher la confirmation pour forcer")

        steps: list[str] = []
        try:
            self._game.say("⚠ Mise à jour du serveur imminente — coupure temporaire dans quelques instants.")
            steps.append("say")
            self._game.save_all()
            steps.append("save-all")
        except ServerUnavailable:
            steps.append("say/save-all sautés (RCON indisponible)")

        try:
            self._updater.apply()
            steps.append("mc-updater démarré")
        except Exception as exc:  # noqa: BLE001
            self._record(user, Permission.UPDATE, "error", "; ".join(steps + [str(exc)]))
            self._notify.notify("Mise à jour échouée", str(exc), "error")
            raise
        self._record(user, Permission.UPDATE, "allowed", "; ".join(steps))
        self._notify.notify("Mise à jour du serveur lancée", "; ".join(steps),
                            "info", event="update")

    # ---- mise à jour de mc-admin LUI-MÊME (bouton MAJ ; barrière UPDATE) ----

    def app_update_status(self, user: User, current_version: str) -> AppUpdateStatus:
        """Verdict du checker + applicabilité sur CETTE installation
        (lecture, non auditée en succès). La route ne l'appelle que pour un
        utilisateur ayant UPDATE — pas de refus audité sous le polling."""
        self._authorize(user, Permission.UPDATE)
        known = self._app_update_state.get() if self._app_update_state else None
        if known is None:
            return AppUpdateStatus(current_version=current_version, latest=None,
                                   update_available=False, checked_at=None,
                                   can_apply=False)
        release, checked_at = known
        available = is_newer_app_version(release.version, current_version)
        can_apply, reason = self._app_update_applicable()
        return AppUpdateStatus(current_version=current_version, latest=release,
                               update_available=available, checked_at=checked_at,
                               can_apply=can_apply, reason=reason)

    def _app_update_applicable(self) -> tuple[bool, str]:
        """Le bouton « Appliquer » a-t-il un sens ici ? Install ghcr = oui ;
        build git local = la mise à jour passe par le dépôt ; image
        indéterminable = prudence, bouton retiré avec explication."""
        if self._app_updater is None:
            return False, "mise à jour en un clic non configurée sur cette installation"
        image = self._self_image.image() if self._self_image else ""
        if not image:
            return False, "mode d'installation indéterminable — mise à jour manuelle"
        # Une référence SANS registre/namespace (« mc-admin:latest ») est un
        # build local : rien à tirer. Avec un slash (ghcr.io/…, registre
        # privé…), l'image vient d'un registre et le pull a un sens.
        if "/" not in image:
            return False, ("installation construite depuis le dépôt git — "
                           "mise à jour via git pull + redéploiement, pas par l'image")
        return True, ""

    def apply_app_update(self, user: User) -> None:
        """Déclenche le one-shot mc-admin-updater. Garde-fous dans l'ordre :
        une mise à jour doit être DISPONIBLE, applicable sur cette install,
        et aucune sauvegarde/restauration ne doit être en cours (l'updater
        recrée mc-admin ET les one-shots — pas pendant qu'ils travaillent)."""
        self._authorize(user, Permission.UPDATE)
        known = self._app_update_state.get() if self._app_update_state else None
        if known is None:
            self._record(user, Permission.UPDATE, "denied",
                         "phase=app_update_blocked raison=aucune-version-connue")
            raise UpdateBlocked("aucune mise à jour connue — la vérification n'a pas encore eu lieu")
        can_apply, reason = self._app_update_applicable()
        if not can_apply:
            self._record(user, Permission.UPDATE, "denied",
                         f"phase=app_update_blocked raison={reason!r}")
            raise UpdateBlocked(reason)
        # Occupations : tolérantes aux ports ABSENTS (install partielle, banc
        # d'essai) — un port non configuré n'est pas une opération en cours,
        # et TOUT refus passe par l'audit (bug attrapé au banc réel 19/07 :
        # _port_running(None) levait RestoreUnavailable sans trace).
        busy = ""
        if self._restore_in_progress():
            busy, message = "restauration-en-cours", "une restauration est en cours — réessaie quand elle est terminée"
        elif self._profile_backup is not None:
            try:
                if self._profile_backup.is_running():
                    busy, message = "sauvegarde-en-cours", "une sauvegarde est en cours — réessaie quand elle est terminée"
            except Exception:  # noqa: BLE001 — état inconnu = prudence
                busy, message = "etat-sauvegarde-illisible", "l'état des sauvegardes est illisible — réessaie dans un instant"
        if busy:
            self._record(user, Permission.UPDATE, "denied",
                         f"phase=app_update_blocked raison={busy}")
            raise UpdateBlocked(message)
        release = known[0]
        try:
            self._app_updater.start()
        except UpdateUnavailable as exc:
            self._record(user, Permission.UPDATE, "error",
                         f"phase=app_update_failed version={release.version} {exc}")
            self._notify.notify("Mise à jour de mc-admin échouée", str(exc), "error")
            raise
        self._record(user, Permission.UPDATE, "allowed",
                     f"phase=app_update_applied version={release.version}")
        self._notify.notify("Mise à jour de mc-admin lancée",
                            f"vers la version {release.version} — l'application redémarre",
                            "info", event="update")

