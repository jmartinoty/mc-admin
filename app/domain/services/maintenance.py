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

import re
from datetime import datetime, timedelta

from domain.errors import InvalidDuration, MaintenanceUnavailable, ServerUnavailable
from domain.model import MaintenanceStatus, Permission, ScheduledMaintenance, User
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
    body = " ".join((message or "").split())[:120]
    horizon = " ".join((until or "").split())[:40]
    # Le TITRE dit déjà « Maintenance en cours » : la 2e ligne ne le répète pas
    # (retour Jeremy 31/07 — « Maintenance en cours » suivi de « Le serveur est
    # fermé pour maintenance » était redondant). Elle ne porte que ce qui AJOUTE
    # de l'information : le message personnalisé et/ou le retour prévu.
    if body and horizon:
        second_line = f"{body} §7(retour prévu : {horizon})"
    elif body:
        second_line = body
    elif horizon:
        second_line = f"§7retour prévu : {horizon}"
    else:
        second_line = ""
    if not second_line:
        motd = _DEFAULT_MOTD_TITLE
    else:
        # Pas de §f devant une ligne qui porte déjà sa couleur (évite « §f§7 »).
        prefix = "" if second_line.startswith("§") else "§f"
        motd = f"{_DEFAULT_MOTD_TITLE}\n{prefix}{second_line}"
    # Refus au login : le joueur n'a PAS le titre sous les yeux, il faut donc
    # une phrase complète -> le message par défaut reprend sa place ici.
    kick_body = body or _DEFAULT_MESSAGE
    kick = kick_body if not horizon else f"{kick_body}\n\nRetour prévu : {horizon}"
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
        defer_if_players: bool = False,
    ) -> None:
        """Ferme le serveur, soit tout de suite, soit après un délai de grâce
        annoncé in-game (avertissements dégressifs par `tick_maintenance`).

        `defer_if_players` (uniquement avec un délai de grâce) : la fermeture
        est reportée tant que des joueurs sont connectés — le tick attend le
        serveur vide au lieu de les déconnecter à l'échéance."""
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
            engage_at, user.username, motd, kick, grace_minutes * 60,
            defer_if_players=defer_if_players,
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
        self._tick_scheduled_maintenance(system_user)
        if self._pending_maintenance is None:
            return
        pending = self._pending_maintenance.status()
        if pending is None:
            return
        now = self._clock.now()
        if pending.engage_at <= now:
            # Garde-fou « ne pas exécuter tant que joueurs connectés » : à
            # l'échéance, on ne déconnecte personne — la fermeture reste en
            # attente (jamais annulée) et s'engage dès le serveur vide.
            if pending.defer_if_players and not self._server_confirmed_empty():
                self._note_deferred_once(
                    system_user, Permission.MAINTENANCE,
                    pending.operation_id, "Maintenance",
                )
                return
            self._deferred_ops_announced.discard(pending.operation_id)
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

    # ---- maintenances programmées (liste ; barrière MAINTENANCE) ----

    def add_scheduled_maintenance(
        self,
        user: User,
        *,
        kind: str,
        time_hhmm: str,
        date: str = "",
        weekdays: tuple[int, ...] = (),
        lead_seconds: int = 300,
        message: str = "",
        until: str = "",
        defer_if_players: bool = False,
    ) -> str:
        """Ajoute une maintenance programmée à la liste. `kind="once"` = une
        date précise (`date`="AAAA-MM-JJ") ; `kind="weekly"` = des jours de
        semaine (`weekdays`, 0=lundi). Renvoie l'identifiant créé. Le tick de
        fond arme la fermeture annoncée `lead_seconds` avant l'heure."""
        self._authorize(user, Permission.MAINTENANCE)
        if self._scheduled_maintenance is None:
            raise ServerUnavailable("maintenances programmées non configurées côté serveur")
        if kind not in ("once", "weekly"):
            raise InvalidDuration(f"type de programmation invalide : {kind!r}")
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", time_hhmm or ""):
            raise InvalidDuration(f"heure invalide : {time_hhmm!r} (attendu HH:MM)")
        if lead_seconds <= 0:
            raise InvalidDuration(f"préavis invalide : {lead_seconds}s (doit être > 0)")
        clean_date = ""
        clean_weekdays: tuple[int, ...] = ()
        if kind == "once":
            try:
                d = datetime.strptime(date or "", "%Y-%m-%d").date()
            except ValueError as exc:
                raise InvalidDuration(f"date invalide : {date!r} (attendu AAAA-MM-JJ)") from exc
            if d < self._clock.now().astimezone().date():
                raise InvalidDuration("date déjà passée")
            clean_date = d.isoformat()
        else:  # weekly
            clean_weekdays = tuple(sorted({int(w) for w in weekdays if 0 <= int(w) <= 6}))
            if not clean_weekdays:
                raise InvalidDuration("aucun jour de semaine sélectionné")
        entry_id = self._scheduled_maintenance.add(
            ScheduledMaintenance(
                id="", kind=kind, time_hhmm=time_hhmm,
                date=clean_date, weekdays=clean_weekdays,
                lead_seconds=lead_seconds,
                message=self._sanitize_reason(message),
                until=self._sanitize_reason(until),
                defer_if_players=defer_if_players,
            )
        )
        when = clean_date if kind == "once" else "jours=" + ",".join(str(w) for w in clean_weekdays)
        self._record(user, Permission.MAINTENANCE, "allowed",
                     f"maintenance programmée ajoutée (id={entry_id} {kind} {when} {time_hhmm})")
        return entry_id

    def remove_scheduled_maintenance(self, user: User, entry_id: str) -> None:
        self._authorize(user, Permission.MAINTENANCE)
        had = (
            self._scheduled_maintenance.remove(entry_id)
            if self._scheduled_maintenance is not None else False
        )
        self._record(
            user, Permission.MAINTENANCE, "allowed",
            f"maintenance programmée retirée (id={entry_id})" if had else "rien à retirer",
        )

    def list_scheduled_maintenance(self, user: User) -> list[ScheduledMaintenance]:
        """Lecture (même barrière que la configuration, non auditée en succès)."""
        self._authorize(user, Permission.MAINTENANCE)
        return (
            self._scheduled_maintenance.list()
            if self._scheduled_maintenance is not None else []
        )

    def _scheduled_target_today(
        self, entry: ScheduledMaintenance, local_now: datetime
    ) -> datetime | None:
        """Échéance d'AUJOURD'HUI pour cette entrée, ou None si elle ne
        s'applique pas ce jour (mauvais jour de semaine / autre date)."""
        try:
            hour, minute = (int(p) for p in entry.time_hhmm.split(":"))
        except (ValueError, TypeError):
            return None
        if entry.kind == "once":
            if entry.date != local_now.date().isoformat():
                return None
        elif entry.kind == "weekly":
            if local_now.weekday() not in entry.weekdays:
                return None
        else:
            return None
        return local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _tick_scheduled_maintenance(self, system_user: User) -> None:
        """Arme la fermeture annoncée pour la première entrée entrée dans sa
        fenêtre de préavis. Une seule armée par tick, une seule fois par jour et
        par entrée (persisté). Pas de rattrapage si mc-admin a démarré après
        l'heure (fermer par surprise serait pire). Les entrées `once` passées
        sont nettoyées."""
        if self._scheduled_maintenance is None or self._pending_maintenance is None:
            return
        if self._doorman is None:
            return  # aucun portier : rien à armer (comme enter_maintenance)
        entries = self._scheduled_maintenance.list()
        if not entries:
            return
        # Ne jamais écraser une fermeture déjà annoncée ni fermer un serveur
        # déjà en maintenance.
        if self._pending_maintenance.status() is not None:
            return
        try:
            if self._doorman.is_running():
                return
        except Exception:  # noqa: BLE001 — lecture Docker : on tente quand même
            pass
        local_now = self._clock.now().astimezone()
        today = local_now.date().isoformat()
        for entry in entries:
            if entry.kind == "once" and entry.date and entry.date < today:
                self._scheduled_maintenance.remove(entry.id)  # date passée : ménage
                continue
            target = self._scheduled_target_today(entry, local_now)
            if target is None:
                continue
            if self._scheduled_maintenance.last_fired(entry.id) == today:
                continue
            if local_now >= target:
                self._scheduled_maintenance.mark_fired(entry.id, today)  # fenêtre passée
                if entry.kind == "once":
                    self._scheduled_maintenance.remove(entry.id)
                continue
            remaining = (target - local_now).total_seconds()
            if remaining > entry.lead_seconds:
                continue  # pas encore dans la fenêtre de préavis
            motd, kick = build_maintenance_messages(entry.message, entry.until)
            self._pending_maintenance.schedule(
                target, system_user.username, motd, kick, remaining,
                defer_if_players=entry.defer_if_players,
            )
            try:
                self._game.say(
                    "⚠ Fermeture programmée pour maintenance dans "
                    f"{_format_seconds(int(remaining))}."
                )
            except Exception:  # noqa: BLE001
                pass
            self._record(system_user, Permission.MAINTENANCE, "allowed",
                         f"maintenance programmée armée (à {entry.time_hhmm})")
            self._scheduled_maintenance.mark_fired(entry.id, today)
            return  # une seule armée par tick
