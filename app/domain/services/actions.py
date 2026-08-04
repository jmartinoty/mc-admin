"""Actions mutantes : restart/start, redémarrages programmés et récurrents, niveaux OP.

Découpage de services.py (V7.0) — corps INCHANGÉ, RBAC/audit via
ServiceCore (base.py), assemblé dans la façade AdminService (__init__.py).
"""
from __future__ import annotations

import re
from datetime import timedelta

from domain.errors import (
    InvalidDuration,
    OpLevelsUnavailable,
    ServerUnavailable,
)
from domain.model import (
    Permission,
    RecurringRestart,
    ScheduledRestart,
    User,
)


# Format officiel des pseudos Minecraft. Validé avant TOUT envoi RCON.

from domain.services.base import (
    _format_seconds,
)


class ActionsMixin:
    # ---- actions mutantes (succès ET erreur audités) ----

    def _begin_op_levels_apply(
        self,
        user: User,
        permission: Permission,
        operation_id: str,
        requested_by: str,
    ) -> None:
        if self._op_levels_apply is None:
            raise OpLevelsUnavailable(
                "niveaux OP en attente, mais leur outil d'application "
                "n'est pas configuré"
            )
        with self._op_levels_lock:
            if self._op_levels_operation is not None:
                raise OpLevelsUnavailable(
                    "une application des niveaux OP est déjà en cours"
                )
            self._op_levels_apply.apply()
            self._op_levels_operation = (
                user,
                permission,
                operation_id,
                requested_by,
            )

    def _ensure_op_levels_idle(self) -> None:
        if self._op_levels_operation is not None:
            raise OpLevelsUnavailable(
                "les niveaux OP sont en cours d'application ; réessayez "
                "lorsque Minecraft sera de nouveau en ligne"
            )
        if self._op_levels_apply is None:
            return
        try:
            running = self._op_levels_apply.is_running()
        except Exception as exc:  # noqa: BLE001 — mutation refusée si l'état est incertain
            raise OpLevelsUnavailable(
                "impossible de vérifier l'état de l'application des niveaux OP"
            ) from exc
        if running:
            raise OpLevelsUnavailable(
                "les niveaux OP sont en cours d'application ; réessayez "
                "lorsque Minecraft sera de nouveau en ligne"
            )

    def _tick_op_levels_operation(self) -> None:
        """Conclut dans l'audit le worker one-shot lancé par start/restart."""
        with self._op_levels_lock:
            operation = self._op_levels_operation
        if operation is None or self._op_levels_apply is None:
            return
        try:
            if self._op_levels_apply.is_running():
                return
            exit_code = self._op_levels_apply.exit_code()
        except Exception:  # noqa: BLE001 — un statut transitoirement illisible sera retenté
            return
        if exit_code is None:
            return
        with self._op_levels_lock:
            if self._op_levels_operation != operation:
                return
            self._op_levels_operation = None
        user, permission, operation_id, requested_by = operation
        common = (
            f"requested_by={requested_by} operation_id={operation_id}"
        )
        if exit_code == 0:
            self._record(
                user,
                permission,
                "allowed",
                f"phase=op_levels_applied {common}",
            )
            self._notify.notify(
                "Niveaux OP appliqués",
                "Minecraft est de nouveau en ligne avec les niveaux demandés.",
                "info",
                event="restart",
            )
            return
        self._record(
            user,
            permission,
            "error",
            f"phase=op_levels_failed exit_code={exit_code} {common}",
        )
        self._notify.notify(
            "Niveaux OP non appliqués",
            (
                f"L'opération a échoué (code {exit_code}). "
                "Le retour à la configuration précédente a été tenté."
            ),
            "error",
            event="restart",
        )

    def restart(
        self,
        user: User,
        *,
        operation_id: str | None = None,
        requested_by: str | None = None,
    ) -> bool:
        self._authorize(user, Permission.RESTART)
        self._refuse_if_maintenance(
            user, Permission.RESTART, "utilise « Rouvrir le serveur »"
        )
        requester = requested_by or user.username
        op_id = operation_id or (
            f"op-levels::{self._clock.now().isoformat()}::{requester}"
        )
        try:
            pending_levels = (
                self._pending_op_levels.list_pending()
                if self._pending_op_levels is not None
                else []
            )
            if pending_levels:
                self._begin_op_levels_apply(
                    user,
                    Permission.RESTART,
                    op_id,
                    requester,
                )
            else:
                self._container.restart()
        except Exception as exc:  # noqa: BLE001 — on audite puis on relaie
            self._record(user, Permission.RESTART, "error", str(exc))
            self._notify.notify("Redémarrage échoué", str(exc), "error")
            raise
        detail = ""
        if pending_levels:
            detail = (
                f"phase=op_levels_started count={len(pending_levels)} "
                f"requested_by={requester} operation_id={op_id}"
            )
        self._record(user, Permission.RESTART, "allowed", detail)
        message = f"Demandé par {requester}."
        if pending_levels:
            message += " Les niveaux OP en attente vont être appliqués."
        self._notify.notify(
            "Redémarrage lancé" if pending_levels else "Serveur redémarré",
            message,
            "info",
            event="restart",
        )
        return bool(pending_levels)

    # ---- garde-fou « ne pas exécuter tant que des joueurs sont connectés » ----

    def _server_confirmed_empty(self) -> bool:
        """Vrai UNIQUEMENT si l'on a pu confirmer 0 joueur connecté. En cas de
        doute (RCON injoignable), renvoie False : un garde-fou ne s'efface pas
        sur une incertitude — mieux vaut reporter que couper des joueurs
        peut-être présents (cf. mémoire « vérifier avant action perturbatrice »)."""
        try:
            return len(self._game.list_players()) == 0
        except Exception:  # noqa: BLE001 — injoignable => le vide n'est PAS confirmé
            return False

    def _note_deferred_once(
        self, user: User, permission: Permission, operation_id: str, label: str
    ) -> None:
        """Annonce le report (in-game + notification + audit) la PREMIÈRE fois
        qu'une op programmée est retenue par le garde-fou joueurs, puis se tait
        jusqu'à son exécution ou son annulation — le tick de fond repasse
        toutes les 2 s, on ne spamme pas."""
        if operation_id in self._deferred_ops_announced:
            return
        self._deferred_ops_announced.add(operation_id)
        try:
            self._game.say(
                f"⏸ {label} reporté : il aura lieu dès que le serveur sera vide."
            )
        except Exception:  # noqa: BLE001 — best-effort, comme les autres say
            pass
        self._notify.notify(
            f"{label} reporté",
            "Des joueurs sont connectés : l'opération programmée attend que le "
            "serveur soit vide avant de s'exécuter.",
            "info",
            event="restart",
        )
        self._record(
            user, permission, "allowed",
            f"phase=deferred_players_online operation_id={operation_id}",
        )

    def schedule_restart(
        self, user: User, minutes: float, *, defer_if_players: bool = False
    ) -> None:
        """Programme un redémarrage différé avec avertissements dégressifs
        in-game (généralise le say+save-all déjà écrit pour la mise à jour,
        V2.3). L'exécution effective (et les avertissements aux seuils
        suivants) est pilotée par `tick_scheduled_restart`, appelé en tâche
        de fond par `RestartWarningScheduler` (`app/restart_scheduler.py`).

        `defer_if_players` : garde-fou « ne pas exécuter tant que joueurs
        connectés » — l'échéance atteinte, le redémarrage attend le serveur
        vide au lieu de couper les joueurs (cf. `tick_scheduled_restart`)."""
        self._authorize(user, Permission.RESTART)
        if minutes <= 0:
            raise InvalidDuration(f"délai invalide : {minutes}min (doit être > 0)")
        restart_at = self._clock.now() + timedelta(minutes=minutes)
        self._restart_schedule.schedule(
            restart_at, user.username, minutes * 60, defer_if_players=defer_if_players
        )
        delay_txt = _format_seconds(int(minutes * 60))  # "30 seconde(s)" / "5 minute(s)"
        try:
            self._game.say(f"⚠ Redémarrage du serveur programmé dans {delay_txt}.")
        except Exception:  # noqa: BLE001 — best-effort, comme apply_update
            pass
        # operation_id dérivé de l'échéance : programmation, annulation et
        # exécution se replient en une ligne dépliable au Journal, sans état
        # supplémentaire à stocker.
        self._record(user, Permission.RESTART, "allowed",
                     f'phase=restart_scheduled delay="{delay_txt}" requested_by={user.username} '
                     f"operation_id=restart::{restart_at.isoformat()}")

    def cancel_scheduled_restart(self, user: User) -> None:
        self._authorize(user, Permission.RESTART)
        pending = self._restart_schedule.status()
        had = self._restart_schedule.cancel()
        if had and pending is not None:
            self._deferred_ops_announced.discard(
                f"restart::{pending.restart_at.isoformat()}"
            )
            self._record(user, Permission.RESTART, "allowed",
                         f"phase=restart_cancelled requested_by={user.username} "
                         f"operation_id=restart::{pending.restart_at.isoformat()}")
        else:
            self._record(user, Permission.RESTART, "allowed", "rien à annuler")

    def scheduled_restart_status(self, user: User) -> ScheduledRestart | None:
        """Lecture (même barrière que la programmation, non auditée en succès)."""
        self._authorize(user, Permission.RESTART)
        return self._restart_schedule.status()

    def tick_scheduled_restart(self, system_user: User) -> None:
        """Appelé à chaque poll par `RestartWarningScheduler` (thread de
        fond) : diffuse l'avertissement dû (le cas échéant) ou déclenche le
        redémarrage effectif — via `restart()`, même chemin audité que le
        bouton manuel — une fois l'échéance atteinte. `system_user` doit être
        `SCHEDULED_RESTART_USER` (permission RESTART uniquement, cf.
        `app/restart_scheduler.py`)."""
        self._authorize(system_user, Permission.RESTART)
        self._tick_op_levels_operation()
        # Issue d'une mise à jour du serveur : constatée par ce thread TOUJOURS
        # actif, pour qu'une MAJ ratée soit notifiée même si personne n'ouvre
        # la page (même raison que le scan des sauvegardes).
        self._scan_update_outcome()
        self._tick_recurring_restart(system_user)
        pending = self._restart_schedule.status()
        if pending is None:
            return
        now = self._clock.now()
        if pending.restart_at <= now:
            operation_id = f"restart::{pending.restart_at.isoformat()}"
            # Garde-fou « ne pas exécuter tant que joueurs connectés » :
            # l'échéance est atteinte mais on ne coupe personne. On NE PAS
            # annule pas la programmation — elle est ré-évaluée au prochain
            # tick et se déclenchera dès le serveur vide.
            if pending.defer_if_players and not self._server_confirmed_empty():
                self._note_deferred_once(
                    system_user, Permission.RESTART, operation_id, "Redémarrage"
                )
                return
            self._deferred_ops_announced.discard(operation_id)
            with self._op_levels_lock:
                if self._op_levels_operation is not None:
                    return
                if self._op_levels_apply is not None:
                    try:
                        if self._op_levels_apply.is_running():
                            return
                    except Exception:  # noqa: BLE001 — ne pas perdre la programmation
                        return
            try:
                self._game.say("⚠ Redémarrage du serveur en cours...")
                self._game.save_all()
            except Exception:  # noqa: BLE001 — best-effort, comme apply_update
                pass
            self._restart_schedule.cancel()
            applies_op_levels = self.restart(
                system_user,
                operation_id=operation_id,
                requested_by=pending.scheduled_by,
            )
            if not applies_op_levels:
                self._record(
                    system_user,
                    Permission.RESTART,
                    "allowed",
                    f"phase=restart_done operation_id={operation_id}",
                )
            return
        threshold = self._restart_schedule.take_due_warning(now)
        if threshold is not None:
            try:
                self._game.say(f"⚠ Redémarrage du serveur programmé dans {_format_seconds(threshold)}")
            except Exception:  # noqa: BLE001
                pass

    # ---- redémarrage récurrent quotidien (V5 ; barrière RESTART) ----

    def set_recurring_restart(
        self,
        user: User,
        time_hhmm: str,
        lead_seconds: int = 300,
        *,
        defer_if_players: bool = False,
    ) -> None:
        """Active « tous les jours à HH:MM » (heure locale). Le déclenchement
        réel passe par le one-shot habituel (donc mêmes avertissements in-game),
        programmé `lead_seconds` avant l'échéance par le tick de fond.

        `defer_if_players` : reporté au one-shot armé chaque jour — le
        redémarrage quotidien attend le serveur vide au lieu de couper des
        joueurs connectés."""
        self._authorize(user, Permission.RESTART)
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", time_hhmm or ""):
            raise InvalidDuration(f"heure invalide : {time_hhmm!r} (attendu HH:MM)")
        if lead_seconds <= 0:
            raise InvalidDuration(f"préavis invalide : {lead_seconds}s (doit être > 0)")
        if self._recurring_restart is None:
            raise ServerUnavailable("redémarrage récurrent non configuré côté serveur")
        self._recurring_restart.set_config(
            RecurringRestart(
                time_hhmm=time_hhmm,
                lead_seconds=lead_seconds,
                defer_if_players=defer_if_players,
            )
        )
        defer_txt = " · attend le serveur vide" if defer_if_players else ""
        self._record(user, Permission.RESTART, "allowed",
                     f"redémarrage récurrent activé : tous les jours à {time_hhmm} "
                     f"(préavis {_format_seconds(lead_seconds)}){defer_txt}")

    def clear_recurring_restart(self, user: User) -> None:
        self._authorize(user, Permission.RESTART)
        if self._recurring_restart is not None:
            self._recurring_restart.clear()
        self._record(user, Permission.RESTART, "allowed", "redémarrage récurrent désactivé")

    def recurring_restart_status(self, user: User) -> RecurringRestart | None:
        """Lecture (non auditée en succès)."""
        self._authorize(user, Permission.RESTART)
        return self._recurring_restart.get() if self._recurring_restart is not None else None

    def _tick_recurring_restart(self, system_user: User) -> None:
        """Arme le one-shot quand on entre dans la fenêtre de préavis du
        rendez-vous quotidien. Une seule fois par jour (persisté) ; si mc-admin
        a démarré APRÈS l'heure prévue, le jour est marqué sans rattrapage (un
        redémarrage surprise à 15h pour un rendez-vous de 6h serait pire)."""
        if self._recurring_restart is None:
            return
        cfg = self._recurring_restart.get()
        if cfg is None:
            return
        local_now = self._clock.now().astimezone()  # heure LOCALE du conteneur (TZ)
        today = local_now.date().isoformat()
        if self._recurring_restart.last_fired() == today:
            return
        hour, minute = (int(part) for part in cfg.time_hhmm.split(":"))
        target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if local_now >= target:
            self._recurring_restart.mark_fired(today)  # fenêtre passée : pas de rattrapage
            return
        remaining = (target - local_now).total_seconds()
        if remaining > cfg.lead_seconds:
            return  # pas encore dans la fenêtre de préavis
        if self._restart_schedule.status() is None:  # ne jamais écraser un one-shot manuel
            self._restart_schedule.schedule(
                target, system_user.username, remaining,
                defer_if_players=cfg.defer_if_players,
            )
            try:
                self._game.say(f"⚠ Redémarrage quotidien programmé dans {_format_seconds(int(remaining))}.")
            except Exception:  # noqa: BLE001
                pass
            self._record(system_user, Permission.RESTART, "allowed",
                         f"redémarrage récurrent armé pour {cfg.time_hhmm}")
        self._recurring_restart.mark_fired(today)

    def start(self, user: User) -> None:
        """Démarrage du conteneur arrêté (moins sensible que STOP : permission dédiée)."""
        self._authorize(user, Permission.START)
        self._refuse_if_maintenance(
            user, Permission.START, "utilise « Rouvrir le serveur »"
        )
        operation_id = (
            f"op-levels::{self._clock.now().isoformat()}::{user.username}"
        )
        try:
            pending_levels = (
                self._pending_op_levels.list_pending()
                if self._pending_op_levels is not None
                else []
            )
            if pending_levels:
                self._begin_op_levels_apply(
                    user,
                    Permission.START,
                    operation_id,
                    user.username,
                )
            else:
                self._container.start()
        except Exception as exc:  # noqa: BLE001
            self._record(user, Permission.START, "error", str(exc))
            self._notify.notify("Démarrage échoué", str(exc), "error")
            raise
        detail = ""
        if pending_levels:
            detail = (
                f"phase=op_levels_started count={len(pending_levels)} "
                f"requested_by={user.username} operation_id={operation_id}"
            )
        self._record(user, Permission.START, "allowed", detail)

