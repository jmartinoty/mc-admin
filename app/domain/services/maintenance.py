"""Mode maintenance : fermeture annoncée du serveur, portier en relais.

Le geste métier est « fermer le serveur SANS le rendre muet » : Minecraft est
arrêté (donc plus aucune charge, plus aucun monde ouvert) mais son endpoint
réseau reste tenu par le portier `mc-doorman`, qui répond aux joueurs à la
place du serveur (MOTD de maintenance dans la liste, refus de connexion
expliqué). Sans lui, le joueur ne lit qu'un « connexion refusée » brut.

Trois garde-fous structurent ce module :

1. **L'état réel n'est jamais déduit.** « Sommes-nous en maintenance ? » se
   répond en interrogeant Docker (`DoormanPort.is_running()`), jamais une
   variable locale : mc-admin peut redémarrer pendant une maintenance, et un
   portier oublié en poste empêcherait le serveur de redémarrer.
2. **On ne redémarre JAMAIS le serveur avant que le portier ait rendu
   l'adresse.** Les deux se disputent la même IP statique (celle que le tunnel
   playit cible en dur, spike du 19/07/2026) : `DoormanPort.release()` attend
   la libération effective et lève sinon — on préfère refuser la réouverture
   plutôt que la voir échouer sur un conflit d'adresse.
3. **Un portier qui ne prend pas son poste ne rouvre pas le serveur tout
   seul.** Si l'arrêt réussit mais que le portier échoue, le serveur reste
   arrêté, l'échec est audité ET notifié : `exit_maintenance` est le chemin de
   reprise (il relève un portier absent sans broncher, puis redémarre).

Permission dédiée `MAINTENANCE` plutôt que réemploi de `STOP` : `STOP` est un
arrêt sec et définitif, la maintenance est une fermeture annoncée, réversible
par un bouton — deux gestes de sensibilité différente, que l'audit doit
distinguer. Les notifications réutilisent en revanche l'événement `restart`
(même famille « disponibilité du serveur ») : pas de 11e interrupteur pour un
cas que les joueurs vivent exactement comme un redémarrage long.
"""
from __future__ import annotations

from datetime import timedelta

from domain.errors import InvalidDuration, MaintenanceUnavailable
from domain.model import MaintenanceStatus, Permission, User
from domain.services.base import _format_seconds

# Laissé à Minecraft pour sauvegarder ses mondes avant le SIGKILL. Le défaut
# docker (10 s) le tuait en pleine sauvegarde (spike 19/07/2026 : ~2 min
# observées sur le monde réel avant un arrêt propre).
MAINTENANCE_STOP_TIMEOUT_SECONDS = 120

_DEFAULT_MOTD_TITLE = "§e⚙ Maintenance en cours"
_DEFAULT_MESSAGE = "Le serveur est fermé pour maintenance."


def build_maintenance_messages(message: str, until: str = "") -> tuple[str, str]:
    """(MOTD 2 lignes pour la liste des serveurs, message de refus au login).

    Fonction PURE, testée à part : c'est le seul endroit où l'on décide ce que
    le joueur lit, et le portier n'a aucune logique de présentation.
    """
    body = " ".join((message or "").split())[:120] or _DEFAULT_MESSAGE
    horizon = " ".join((until or "").split())[:40]
    second_line = f"{body} §7(retour prévu : {horizon})" if horizon else body
    motd = f"{_DEFAULT_MOTD_TITLE}\n§f{second_line}"
    kick = body if not horizon else f"{body}\n\nRetour prévu : {horizon}"
    return motd, kick


class MaintenanceMixin:
    # ---- lecture ----

    def maintenance_status(self, user: User) -> MaintenanceStatus:
        """Barrière STATUS : tout le monde a le droit de savoir que le serveur
        est fermé (le bandeau s'affiche pour tous). Non auditée en succès —
        elle est appelée à chaque poll du tableau de bord."""
        self._authorize(user, Permission.STATUS)
        if self._doorman is None:
            return MaintenanceStatus(active=False)
        try:
            active = self._doorman.is_running()
        except Exception:  # noqa: BLE001 — lecture : dégradation propre, jamais de 500
            active = False
        pending = (
            self._pending_maintenance.status()
            if self._pending_maintenance is not None
            else None
        )
        return MaintenanceStatus(
            active=active,
            pending=pending,
            motd=pending.motd if pending is not None else "",
        )

    # ---- fermeture ----

    def enter_maintenance(
        self,
        user: User,
        *,
        message: str = "",
        until: str = "",
        grace_minutes: float = 0.0,
    ) -> None:
        """Ferme le serveur, soit tout de suite, soit après un délai de grâce
        annoncé in-game (avertissements dégressifs par `tick_maintenance`)."""
        self._authorize(user, Permission.MAINTENANCE)
        if self._doorman is None:
            raise MaintenanceUnavailable(
                "mode maintenance indisponible : aucun portier configuré"
            )
        if grace_minutes < 0:
            raise InvalidDuration(f"délai invalide : {grace_minutes}min (doit être >= 0)")
        if self._doorman.is_running():
            raise MaintenanceUnavailable("le serveur est déjà en maintenance")

        motd, kick = build_maintenance_messages(
            self._sanitize_reason(message), self._sanitize_reason(until)
        )

        if grace_minutes <= 0:
            self._engage_maintenance(user, motd, kick, requested_by=user.username)
            return

        if self._pending_maintenance is None:
            raise MaintenanceUnavailable(
                "délai de grâce indisponible : aucun suivi d'annonce configuré"
            )
        engage_at = self._clock.now() + timedelta(minutes=grace_minutes)
        self._pending_maintenance.schedule(
            engage_at, user.username, motd, kick, grace_minutes * 60
        )
        delay_txt = _format_seconds(int(grace_minutes * 60))
        try:
            self._game.say(f"⚠ Fermeture du serveur pour maintenance dans {delay_txt}.")
        except Exception:  # noqa: BLE001 — best-effort, comme schedule_restart
            pass
        self._record(
            user,
            Permission.MAINTENANCE,
            "allowed",
            f'phase=maintenance_scheduled delay="{delay_txt}" '
            f"requested_by={user.username} "
            f"operation_id=maintenance::{engage_at.isoformat()}",
        )

    def cancel_pending_maintenance(self, user: User) -> None:
        self._authorize(user, Permission.MAINTENANCE)
        pending = (
            self._pending_maintenance.status()
            if self._pending_maintenance is not None
            else None
        )
        had = self._pending_maintenance.cancel() if self._pending_maintenance else False
        if had and pending is not None:
            try:
                self._game.say("✅ Maintenance annulée : le serveur reste ouvert.")
            except Exception:  # noqa: BLE001
                pass
            self._record(
                user,
                Permission.MAINTENANCE,
                "allowed",
                f"phase=maintenance_cancelled requested_by={user.username} "
                f"operation_id={pending.operation_id}",
            )
            return
        self._record(user, Permission.MAINTENANCE, "allowed", "rien à annuler")

    def _engage_maintenance(
        self,
        user: User,
        motd: str,
        kick: str,
        *,
        requested_by: str,
        operation_id: str = "",
    ) -> None:
        """Arrêt du serveur puis prise de poste du portier. L'ORDRE est le
        garde-fou : le portier ne peut pas prendre l'adresse d'un serveur
        vivant (Docker refuserait), on arrête donc d'abord — et si le portier
        échoue ensuite, on laisse le serveur arrêté en le disant fort."""
        op_id = operation_id or f"maintenance::{self._clock.now().isoformat()}"
        common = f"requested_by={requested_by} operation_id={op_id}"

        # Prévenir puis sauvegarder : best-effort, comme apply_update. Un RCON
        # muet ne doit pas empêcher une maintenance (c'est souvent POUR ça
        # qu'on la déclenche).
        try:
            self._game.say("⚠ Fermeture du serveur pour maintenance.")
            self._game.save_all()
        except Exception:  # noqa: BLE001
            pass

        try:
            self._container.stop(timeout=MAINTENANCE_STOP_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 — audité puis relayé
            self._record(
                user, Permission.MAINTENANCE, "error",
                f"phase=maintenance_stop_failed {common}",
            )
            self._notify.notify("Mise en maintenance échouée", str(exc), "error")
            raise

        try:
            self._doorman.engage(motd, kick)
        except Exception as exc:  # noqa: BLE001
            # Le serveur est arrêté mais personne ne tient l'adresse : les
            # joueurs verront « connexion refusée ». On le dit franchement —
            # « Rouvrir » reste le chemin de reprise.
            self._record(
                user, Permission.MAINTENANCE, "error",
                f"phase=maintenance_doorman_failed {common}",
            )
            self._notify.notify(
                "Portier de maintenance absent",
                (
                    "Le serveur est bien arrêté, mais le portier n'a pas pris "
                    f"son poste ({exc}). Les joueurs ne verront aucun message. "
                    "Utilisez « Rouvrir le serveur » pour revenir en ligne."
                ),
                "error",
                event="restart",
            )
            raise

        self._record(
            user, Permission.MAINTENANCE, "allowed",
            f"phase=maintenance_engaged {common}",
        )
        self._notify.notify(
            "Serveur en maintenance",
            f"Fermé par {requested_by}. Le portier répond aux joueurs à sa place.",
            "info",
            event="restart",
        )

    # ---- réouverture ----

    def exit_maintenance(self, user: User) -> None:
        """Relève le portier PUIS redémarre le serveur. Jamais l'inverse : les
        deux se disputent la même adresse. Idempotent côté portier (relever un
        portier absent ne lève pas), ce qui en fait aussi le chemin de reprise
        après un engagement à moitié réussi."""
        self._authorize(user, Permission.MAINTENANCE)
        if self._doorman is None:
            raise MaintenanceUnavailable(
                "mode maintenance indisponible : aucun portier configuré"
            )
        if self._pending_maintenance is not None:
            self._pending_maintenance.cancel()

        try:
            self._doorman.release()
        except Exception as exc:  # noqa: BLE001
            self._record(
                user, Permission.MAINTENANCE, "error",
                f"phase=maintenance_release_failed requested_by={user.username}",
            )
            self._notify.notify("Réouverture impossible", str(exc), "error")
            raise

        try:
            self._container.start()
        except Exception as exc:  # noqa: BLE001
            self._record(
                user, Permission.MAINTENANCE, "error",
                f"phase=maintenance_restart_failed requested_by={user.username}",
            )
            self._notify.notify("Réouverture échouée", str(exc), "error")
            raise

        self._record(
            user, Permission.MAINTENANCE, "allowed",
            f"phase=maintenance_ended requested_by={user.username}",
        )
        self._notify.notify(
            "Serveur rouvert",
            f"La maintenance est terminée ({user.username}). Le serveur redémarre.",
            "info",
            event="restart",
        )

    # ---- tâche de fond ----

    def tick_maintenance(self, system_user: User) -> None:
        """Appelé à chaque poll par `MaintenanceScheduler` : diffuse
        l'avertissement dû, ou ferme le serveur une fois l'échéance atteinte.
        Même discipline que `tick_scheduled_restart` — le thread ne connaît
        que le service, jamais les ports."""
        self._authorize(system_user, Permission.MAINTENANCE)
        if self._pending_maintenance is None:
            return
        pending = self._pending_maintenance.status()
        if pending is None:
            return
        now = self._clock.now()
        if pending.engage_at <= now:
            self._pending_maintenance.cancel()
            self._engage_maintenance(
                system_user,
                pending.motd,
                pending.kick,
                requested_by=pending.requested_by,
                operation_id=pending.operation_id,
            )
            return
        threshold = self._pending_maintenance.take_due_warning(now)
        if threshold is not None:
            try:
                self._game.say(
                    f"⚠ Fermeture pour maintenance dans {_format_seconds(threshold)}"
                )
            except Exception:  # noqa: BLE001
                pass
