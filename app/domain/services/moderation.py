"""Whitelist, opérateurs, kick, bans (permanents et temporisés).

Découpage de services.py (V7.0) — corps INCHANGÉ, RBAC/audit via
ServiceCore (base.py), assemblé dans la façade AdminService (__init__.py).
"""
from __future__ import annotations

from datetime import timedelta

from domain.errors import (
    InvalidDuration,
    InvalidOpLevel,
    InvalidPlayerName,
    OpLevelsUnavailable,
)
from domain.model import (
    BanEntry,
    OpEntry,
    PendingOpLevel,
    PendingUnban,
    Permission,
    User,
)


# Format officiel des pseudos Minecraft. Validé avant TOUT envoi RCON.

from domain.services.base import (
    _PLAYER_NAME_RE,
)


class ModerationMixin:
    # ---- whitelist (barrière WHITELIST_MANAGE ; mutations auditées) ----

    @staticmethod
    def _validate_player_name(player_name: str) -> None:
        if not _PLAYER_NAME_RE.match(player_name or ""):
            raise InvalidPlayerName(f"pseudo invalide : {player_name!r}")

    def whitelist(self, user: User) -> list[str]:
        """Lecture de la whitelist (non auditée en succès, comme les lectures)."""
        self._authorize(user, Permission.WHITELIST_MANAGE)
        return self._game.whitelist_list()

    def whitelist_add(self, user: User, player_name: str) -> str:
        self._authorize(user, Permission.WHITELIST_MANAGE)
        self._validate_player_name(player_name)
        try:
            result = self._game.whitelist_add(player_name)
        except Exception:  # noqa: BLE001
            self._record(user, Permission.WHITELIST_MANAGE, "error", f"add {player_name}")
            raise
        self._record(user, Permission.WHITELIST_MANAGE, "allowed", f"add {player_name}")
        return result

    def whitelist_remove(self, user: User, player_name: str) -> str:
        self._authorize(user, Permission.WHITELIST_MANAGE)
        self._validate_player_name(player_name)
        try:
            result = self._game.whitelist_remove(player_name)
        except Exception:  # noqa: BLE001
            self._record(user, Permission.WHITELIST_MANAGE, "error", f"remove {player_name}")
            raise
        self._record(user, Permission.WHITELIST_MANAGE, "allowed", f"remove {player_name}")
        return result

    # ---- opérateurs (barrière OP_MANAGE ; mutations auditées) ----
    #
    # Lecture via OpsPort (fichier ops.json, aucune commande RCON « op list »
    # n'existe côté vanilla/Fabric) ; l'ajout/retrait passe par RCON (op/deop),
    # qui écrit ops.json côté serveur — cohérent avec le pattern whitelist.
    #
    # Niveau : Java Edition n'a pas de commande pour changer le niveau d'un op
    # à la volée (`/op` accorde `op-permission-level`, réglage global). Un
    # niveau individuel différent est donc mis en attente et appliqué hors
    # ligne pendant le prochain redémarrage demandé.

    _VALID_OP_LEVELS = (1, 2, 3, 4)
    _DEFAULT_OP_LEVEL = 4

    def ops(self, user: User) -> list[OpEntry]:
        self._authorize(user, Permission.OP_MANAGE)
        return self._ops.list_ops()

    def pending_op_levels(self, user: User) -> list[PendingOpLevel]:
        self._authorize(user, Permission.OP_MANAGE)
        if self._pending_op_levels is None:
            return []
        return self._pending_op_levels.list_pending()

    def op_add(self, user: User, player_name: str, level: int = 4) -> str:
        self._authorize(user, Permission.OP_MANAGE)
        self._validate_player_name(player_name)
        if level not in self._VALID_OP_LEVELS:
            raise InvalidOpLevel(f"niveau invalide : {level} (doit être 1 à 4)")
        with self._op_levels_lock:
            try:
                self._ensure_op_levels_idle()
            except Exception as exc:
                self._record(
                    user,
                    Permission.OP_MANAGE,
                    "error",
                    f"op {player_name} refusé : {exc}",
                )
                raise
            current_level = None
            current_level_known = True
            try:
                current_level = next(
                    (
                        entry.level
                        for entry in self._ops.list_ops()
                        if entry.name.lower() == player_name.lower()
                    ),
                    None,
                )
            except Exception:  # noqa: BLE001 - la promotion RCON reste disponible
                current_level_known = False
            try:
                result = self._game.op(player_name)
            except Exception:  # noqa: BLE001
                self._record(user, Permission.OP_MANAGE, "error", f"op {player_name}")
                raise

            immediate = (
                current_level == level
                or (
                    current_level_known
                    and current_level is None
                    and level == self._DEFAULT_OP_LEVEL
                )
            )
            try:
                if self._pending_op_levels is None:
                    if not immediate:
                        raise OpLevelsUnavailable(
                            "persistance des niveaux OP non configurée"
                        )
                elif immediate:
                    self._pending_op_levels.remove(player_name)
                else:
                    self._pending_op_levels.set_level(player_name, level)
            except Exception as exc:  # noqa: BLE001 - le /op a déjà réussi
                detail = (
                    f"op {player_name} réussi, mais niveau {level} non enregistré"
                )
                self._record(user, Permission.OP_MANAGE, "error", detail)
                raise OpLevelsUnavailable(
                    f"{player_name} est opérateur, mais le niveau {level} "
                    "n'a pas pu être enregistré"
                ) from exc

        detail = f"op {player_name} niveau {level}"
        if immediate:
            detail += " (effectif immédiatement)"
        else:
            detail += " (op immédiat, niveau au prochain redémarrage)"
        self._record(user, Permission.OP_MANAGE, "allowed", detail)
        notification = (
            f"{user.username} a promu {player_name} opérateur (niveau {level})."
            if immediate
            else (
                f"{user.username} a promu {player_name} opérateur. "
                f"Niveau {level} prévu au prochain redémarrage."
            )
        )
        self._notify.notify(
            "Modération",
            notification,
            "info",
            event="moderation",
        )
        return result

    def op_remove(self, user: User, player_name: str) -> str:
        self._authorize(user, Permission.OP_MANAGE)
        self._validate_player_name(player_name)
        with self._op_levels_lock:
            try:
                self._ensure_op_levels_idle()
            except Exception as exc:
                self._record(
                    user,
                    Permission.OP_MANAGE,
                    "error",
                    f"deop {player_name} refusé : {exc}",
                )
                raise
            try:
                result = self._game.deop(player_name)
            except Exception:  # noqa: BLE001
                self._record(user, Permission.OP_MANAGE, "error", f"deop {player_name}")
                raise
            if self._pending_op_levels is not None:
                try:
                    self._pending_op_levels.remove(player_name)
                except Exception as exc:  # noqa: BLE001 - le /deop a déjà réussi
                    self._record(
                        user,
                        Permission.OP_MANAGE,
                        "error",
                        f"deop {player_name} réussi, annulation du niveau en attente impossible",
                    )
                    raise OpLevelsUnavailable(
                        f"{player_name} n'est plus opérateur, mais son niveau "
                        "en attente n'a pas pu être supprimé"
                    ) from exc
        self._record(user, Permission.OP_MANAGE, "allowed", f"deop {player_name}")
        return result

    # ---- kick / ban (V4.4a) ----

    @staticmethod
    def _sanitize_reason(reason: str) -> str:
        """Nettoie la raison (texte libre, pas un pseudo) avant envoi RCON :
        pas de retour à la ligne — défense en profondeur, même si le protocole
        RCON traite un paquet comme une seule commande. Longueur bornée."""
        return " ".join((reason or "").split())[:200]

    def kick(self, user: User, player_name: str, reason: str = "") -> str:
        """Exclusion d'un joueur connecté, sans effet sur ses futures connexions
        (contrairement à ban) — permission dédiée, moins sensible que BAN_MANAGE."""
        self._authorize(user, Permission.KICK)
        self._validate_player_name(player_name)
        reason = self._sanitize_reason(reason)
        detail = f"kick {player_name}" + (f" ({reason})" if reason else "")
        try:
            result = self._game.kick(player_name, reason)
        except Exception:  # noqa: BLE001
            self._record(user, Permission.KICK, "error", detail)
            raise
        self._record(user, Permission.KICK, "allowed", detail)
        self._notify.notify("Modération", f"{user.username} a exclu {player_name}"
                            + (f" ({reason})" if reason else "") + ".",
                            "info", event="moderation")
        return result

    def bans(self, user: User) -> list[BanEntry]:
        """Liste des bannissements actifs (lecture, non auditée en succès)."""
        self._authorize(user, Permission.BAN_MANAGE)
        return self._bans.list_bans()

    def ban(self, user: User, player_name: str, reason: str = "") -> str:
        """Ban permanent — réservé au rôle qui a BAN_MANAGE (owner).

        Annule tout ban TEMPORISÉ en attente pour ce joueur : un ban permanent
        frais ne doit jamais être levé plus tard par une minuterie obsolète."""
        self._authorize(user, Permission.BAN_MANAGE)
        self._validate_player_name(player_name)
        reason = self._sanitize_reason(reason)
        detail = f"ban {player_name}" + (f" ({reason})" if reason else "")
        try:
            result = self._game.ban(player_name, reason)
        except Exception:  # noqa: BLE001
            self._record(user, Permission.BAN_MANAGE, "error", detail)
            raise
        self._temp_bans.cancel(player_name)
        self._record(user, Permission.BAN_MANAGE, "allowed", detail)
        self._notify.notify("Modération", f"{user.username} a banni {player_name}"
                            + (f" ({reason})" if reason else "") + ".",
                            "info", event="moderation")
        return result

    def ban_temporary(self, user: User, player_name: str, hours: float, reason: str = "") -> str:
        """Ban temporisé — vanilla n'a AUCUNE durée native sur `/ban` : on pose
        un ban normal puis on PLANIFIE nous-mêmes sa levée automatique
        (cf. `app/tempban_scheduler.py`). Toute minuterie précédente pour ce
        joueur est remplacée (jamais deux levées programmées en parallèle)."""
        self._authorize(user, Permission.BAN_MANAGE)
        self._validate_player_name(player_name)
        if hours <= 0:
            raise InvalidDuration(f"durée invalide : {hours}h (doit être > 0)")
        reason = self._sanitize_reason(reason)
        detail = f"ban {player_name} pendant {hours:g}h" + (f" ({reason})" if reason else "")
        try:
            result = self._game.ban(player_name, reason)
        except Exception:  # noqa: BLE001
            self._record(user, Permission.BAN_MANAGE, "error", detail)
            raise
        unban_at = self._clock.now() + timedelta(hours=hours)
        self._temp_bans.cancel(player_name)  # remplace toute minuterie existante
        self._temp_bans.schedule(player_name, unban_at)
        self._record(user, Permission.BAN_MANAGE, "allowed", detail)
        return result

    def pending_unbans(self, user: User) -> list[PendingUnban]:
        """Bans temporisés en attente de levée automatique (lecture)."""
        self._authorize(user, Permission.BAN_MANAGE)
        return self._temp_bans.pending()

    def pardon(self, user: User, player_name: str) -> str:
        """Lève un ban (permanent ou temporisé).

        Annule aussi toute levée automatique programmée pour ce joueur : une
        levée manuelle ne doit jamais être suivie d'une seconde levée (no-op
        inoffensif, mais autant garder l'état cohérent)."""
        self._authorize(user, Permission.BAN_MANAGE)
        self._validate_player_name(player_name)
        try:
            result = self._game.pardon(player_name)
        except Exception:  # noqa: BLE001
            self._record(user, Permission.BAN_MANAGE, "error", f"pardon {player_name}")
            raise
        self._temp_bans.cancel(player_name)
        self._record(user, Permission.BAN_MANAGE, "allowed", f"pardon {player_name}")
        self._notify.notify("Modération", f"{user.username} a débanni {player_name}.",
                            "info", event="moderation")
        return result
