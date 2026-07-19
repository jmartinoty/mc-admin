"""Mise à jour contrôlée du serveur (garde-fous V2.3).

Découpage de services.py (V7.0) — corps INCHANGÉ, RBAC/audit via
ServiceCore (base.py), assemblé dans la façade AdminService (__init__.py).
"""
from __future__ import annotations


from domain.errors import (
    ServerUnavailable,
    UpdateBlocked,
)
from domain.model import (
    Permission,
    UpdateStatus,
    User,
)


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

