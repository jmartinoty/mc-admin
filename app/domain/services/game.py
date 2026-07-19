"""Réglages de jeu (gamerules, difficulté, météo, heure), console RCON, stop.

Découpage de services.py (V7.0) — corps INCHANGÉ, RBAC/audit via
ServiceCore (base.py), assemblé dans la façade AdminService (__init__.py).
"""
from __future__ import annotations


from domain.errors import (
    InvalidGameValue,
)
from domain.model import (
    GameSettings,
    Permission,
    User,
)


# Format officiel des pseudos Minecraft. Validé avant TOUT envoi RCON.



class GameMixin:
    # ---- réglages de jeu (V5 ; barrière GAME_SETTINGS, listes blanches) ----

    _DIFFICULTIES = ("peaceful", "easy", "normal", "hard")
    # Noms RCON des gamerules — MC 26.1 les a renommées en snake_case
    # (vérifié contre le vrai serveur : keepInventory -> keep_inventory,
    # doDaylightCycle -> advance_time).
    _GAMERULES = ("keep_inventory", "mob_griefing", "advance_time")
    _WEATHERS = ("clear", "rain", "thunder")
    _TIMES = ("day", "noon", "night", "midnight")

    def game_settings(self, user: User) -> GameSettings:
        """Lecture des réglages courants (non auditée en succès). Chaque champ
        dégrade à None individuellement si sa réponse RCON est inattendue."""
        self._authorize(user, Permission.GAME_SETTINGS)
        return GameSettings(
            difficulty=self._game.get_difficulty(),
            keep_inventory=self._game.get_gamerule("keep_inventory"),
            mob_griefing=self._game.get_gamerule("mob_griefing"),
            daylight_cycle=self._game.get_gamerule("advance_time"),
        )

    def set_difficulty(self, user: User, level: str) -> str:
        self._authorize(user, Permission.GAME_SETTINGS)
        if level not in self._DIFFICULTIES:
            raise InvalidGameValue(f"difficulté inconnue : {level!r}")
        return self._game_mutation(user, f"difficulté -> {level}", lambda: self._game.set_difficulty(level))

    def set_gamerule(self, user: User, name: str, value: bool | int | str) -> str:
        """Le nom doit exister sur CE serveur (liste blanche DYNAMIQUE lue via
        RCON — les gamerules varient selon la version, cf. renommage 26.1) ;
        la valeur est normalisée en bool ou entier borné : jamais de texte
        libre vers RCON."""
        self._authorize(user, Permission.GAME_SETTINGS)
        if isinstance(value, str):
            raw = value.strip().lower()
            if raw in ("true", "false"):
                value = raw == "true"
            else:
                try:
                    value = int(raw)
                except ValueError:
                    raise InvalidGameValue(f"valeur invalide : {value!r} (booléen ou entier)") from None
        if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 1_000_000:
            raise InvalidGameValue(f"valeur hors bornes : {value}")
        if name not in self._game.list_gamerule_names():
            raise InvalidGameValue(f"gamerule inconnue de ce serveur : {name!r}")
        return self._game_mutation(user, f"gamerule {name} -> {str(value).lower()}",
                                   lambda: self._game.set_gamerule(name, value))

    def full_game_settings(self, user: User) -> tuple[str | None, dict[str, str]]:
        """Page Réglages : difficulté + TOUTES les gamerules de ce serveur avec
        leur valeur brute ("true"/"false"/entier). Lecture non auditée."""
        self._authorize(user, Permission.GAME_SETTINGS)
        names = self._game.list_gamerule_names()
        return self._game.get_difficulty(), self._game.get_gamerules(names)

    def set_weather(self, user: User, kind: str) -> str:
        self._authorize(user, Permission.GAME_SETTINGS)
        if kind not in self._WEATHERS:
            raise InvalidGameValue(f"météo inconnue : {kind!r}")
        return self._game_mutation(user, f"météo -> {kind}", lambda: self._game.set_weather(kind))

    def set_time(self, user: User, moment: str) -> str:
        self._authorize(user, Permission.GAME_SETTINGS)
        if moment not in self._TIMES:
            raise InvalidGameValue(f"moment inconnu : {moment!r}")
        return self._game_mutation(user, f"heure -> {moment}", lambda: self._game.set_time(moment))

    def _game_mutation(self, user: User, detail: str, action) -> str:
        try:
            result = action()
        except Exception:  # noqa: BLE001 — on audite puis on relaie
            self._record(user, Permission.GAME_SETTINGS, "error", detail)
            raise
        self._record(user, Permission.GAME_SETTINGS, "allowed", detail)
        return result

    def can_access_console(self, user: User) -> None:
        """Barrière d'accès à la page console (aucune donnée à renvoyer côté
        GET ; sert uniquement à appliquer le contrôle RBAC + auditer un refus,
        cohérent avec le reste du service — la RBAC ne vit jamais dans la route)."""
        self._authorize(user, Permission.RCON_RAW)

    def run_rcon(self, user: User, command: str) -> str:
        """Commande RCON arbitraire — réservée au rôle qui a RCON_RAW (owner)."""
        self._authorize(user, Permission.RCON_RAW)
        try:
            output = self._game.raw(command)
        except Exception:  # noqa: BLE001
            self._record(user, Permission.RCON_RAW, "error", command)
            raise
        self._record(user, Permission.RCON_RAW, "allowed", command)
        return output

    def stop(self, user: User) -> None:
        """Arrêt définitif — réservé au rôle qui a STOP (owner)."""
        self._authorize(user, Permission.STOP)
        try:
            self._container.stop()
        except Exception as exc:  # noqa: BLE001
            self._record(user, Permission.STOP, "error", str(exc))
            self._notify.notify("Arrêt échoué", str(exc), "error")
            raise
        self._record(user, Permission.STOP, "allowed")

