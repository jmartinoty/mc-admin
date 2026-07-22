"""Lectures : status, métriques, performances, joueurs, mods, journal, spark.

Découpage de services.py (V7.0) — corps INCHANGÉ, RBAC/audit via
ServiceCore (base.py), assemblé dans la façade AdminService (__init__.py).
"""
from __future__ import annotations

from datetime import datetime

from domain.errors import (
    InvalidGameValue,
    ServerUnavailable,
)
from domain.model import (
    AlertThresholds,
    AuditEntry,
    ContainerState,
    Incident,
    MetricReading,
    PerformanceSnapshot,
    ModInfo,
    ModUpdate,
    Permission,
    PlayerStats,
    PlayerSummary,
    SparkProfile,
    ServerStatus,
    TimelineEvent,
    User,
)


# Format officiel des pseudos Minecraft. Validé avant TOUT envoi RCON.


def _corrective_kind(action: str, detail: str) -> str | None:
    """Genre d'action corrective à corréler à un incident, ou None si l'entrée
    d'audit n'est pas une intervention pertinente. Ne renvoie QUE le genre —
    aucun détail technique n'est exposé (barrière STATUS, cf. incidents())."""
    if action == Permission.RESTART.value:
        return "restart"
    if action == Permission.BACKUP_RESTORE.value:
        return "restore"
    if action == Permission.MAINTENANCE.value:
        return "maintenance"
    if action == Permission.UPDATE.value and "phase=app_update_applied" not in detail:
        return "update"
    if action == Permission.BACKUP_TRIGGER.value and "phase=backup_done" in detail:
        return "backup"
    return None


class MonitoringMixin:
    # ---- lectures (refus audité, succès non audité) ----

    def get_status(self, user: User) -> ServerStatus:
        self._authorize(user, Permission.STATUS)
        state = self._container.status()
        players: tuple = ()
        available = False
        if state.running:
            # Le serveur peut tourner alors que RCON n'est pas encore prêt :
            # on dégrade proprement au lieu de planter.
            try:
                players = tuple(self._game.list_players())
                available = True
            except ServerUnavailable:
                available = False
        return ServerStatus(container=state, players_online=players, players_available=available)

    def metrics_snapshot(self, user: User) -> list[MetricReading]:
        """Métriques configurées — même barrière que le status (lecture)."""
        self._authorize(user, Permission.STATUS)
        return self._metrics.snapshot()

    # Plages d'historique MSPT proposées par l'UI — liste FERMÉE : jamais de
    # plage arbitraire depuis une URL (même esprit que le PromQL prédéfini).
    PERFORMANCE_RANGES = (24, 72, 168)

    def performance_snapshot(self, user: User, history_hours: int = 24) -> PerformanceSnapshot:
        """Santé du serveur (TPS/MSPT/entités/chunks) — même barrière que le
        status : c'est de la lecture, un viewer peut regarder. Une plage
        inconnue retombe sur 24 h (URL bricolée = pas une erreur)."""
        self._authorize(user, Permission.STATUS)
        if history_hours not in self.PERFORMANCE_RANGES:
            history_hours = 24
        return self._metrics.performance(history_hours)

    def alert_thresholds(self, user: User) -> AlertThresholds:
        """Seuils d'alerte actuels de PerfWatcher — même barrière que le
        reste de la page Performances (lecture, un viewer peut regarder :
        c'est déjà ce que la copie affiche en dur aujourd'hui)."""
        self._authorize(user, Permission.STATUS)
        if self._alert_thresholds is None:
            return AlertThresholds()
        return self._alert_thresholds.get()

    # Fenêtre de scan pour les marqueurs de performance — même ordre de
    # grandeur que le Journal (docs V5.x : fenêtre 2000 entrées).
    _PERF_EVENT_SCAN_LIMIT = 2000

    def performance_events(self, user: User, since: datetime) -> list[tuple[datetime, str]]:
        """Marqueurs annotés sur les graphes de performance (sauvegardes
        réussies, redémarrages, mises à jour DU SERVEUR) — même barrière que
        le reste de la page (STATUS, un viewer peut regarder). Contrairement
        au Journal complet (AUDIT_VIEW), on ne retourne QUE le genre et
        l'instant, jamais l'auteur ni le détail technique.

        La mise à jour DE MC-ADMIN LUI-MÊME (bouton MAJ, `phase=
        app_update_applied`) est délibérément exclue : elle ne touche pas au
        jeu, l'annoter sur ce graphe induirait en erreur."""
        self._authorize(user, Permission.STATUS)
        events: list[tuple[datetime, str]] = []
        for entry in self._audit.recent(self._PERF_EVENT_SCAN_LIMIT):
            if entry.timestamp < since or entry.outcome != "allowed":
                continue
            if entry.action == Permission.BACKUP_TRIGGER.value:
                if "phase=backup_done" in entry.detail:
                    events.append((entry.timestamp, "backup"))
            elif entry.action == Permission.RESTART.value:
                events.append((entry.timestamp, "restart"))
            elif entry.action == Permission.UPDATE.value:
                if "phase=app_update_applied" not in entry.detail:
                    events.append((entry.timestamp, "update"))
        return events

    def incidents(self, user: User) -> list[tuple[Incident, list[tuple[datetime, str]]]]:
        """Historique des incidents (chute/dégradation persistées par les
        watchers) + actions correctives corrélées depuis le journal, chacune
        étant un couple (instant, genre). Barrière STATUS comme
        `performance_events` : on n'expose que le genre et l'instant des actions
        (jamais l'auteur ni le détail technique), la disponibilité n'est pas un
        secret dans cet outil. Chaque incident vient avec les actions AUDITÉES
        (redémarrage, sauvegarde, restauration, mise à jour, maintenance)
        tombant dans sa fenêtre [début, fin ou maintenant]."""
        self._authorize(user, Permission.STATUS)
        if self._incidents is None:
            return []
        incidents = self._incidents.recent()
        if not incidents:
            return []
        audit = self._audit.recent(self._PERF_EVENT_SCAN_LIMIT)
        result: list[tuple[Incident, list[tuple[datetime, str]]]] = []
        for incident in incidents:
            end = incident.ended_at or self._clock.now()
            actions: list[tuple[datetime, str]] = []
            for entry in audit:
                if entry.outcome != "allowed":
                    continue
                kind = _corrective_kind(entry.action, entry.detail)
                if kind is None:
                    continue
                try:
                    in_window = incident.started_at <= entry.timestamp <= end
                except TypeError:
                    continue  # horodatages incompatibles (naïf/aware) : on saute
                if in_window:
                    actions.append((entry.timestamp, kind))
            actions.sort(key=lambda item: item[0])
            result.append((incident, actions))
        return result

    def player_history(self, user: User) -> list[PlayerSummary]:
        """Temps de jeu cumulé + dernière connexion (même barrière que status).

        Lecture non auditée en succès (politique des lectures) ; les
        écritures (record_join/leave) ne passent PAS par ici — c'est le
        tailer de logs qui les fait directement, en observation passive."""
        self._authorize(user, Permission.STATUS)
        return self._player_history.summary()

    def player_stats(self, user: User) -> list[PlayerStats]:
        """Statistiques vanilla de tous les joueurs (V5.x) — comptées par le
        SERVEUR depuis la création du monde, donc y compris avant mc-admin.
        Même barrière que l'historique (STATUS), non audité en succès."""
        self._authorize(user, Permission.STATUS)
        if self._player_stats is None:
            return []
        return self._player_stats.all_stats()

    def player_detail(self, user: User, player_name: str):
        """Fiche d'un joueur : (résumé agrégé | None, sessions individuelles
        plus récentes d'abord, stats vanilla | None). Barrière STATUS."""
        self._authorize(user, Permission.STATUS)
        self._validate_player_name(player_name)
        key = player_name.lower()
        summary = next((p for p in self._player_history.summary() if p.player.lower() == key), None)
        sessions = [s for s in self._player_history.recent_sessions(200) if s.player.lower() == key]
        stats = None
        if self._player_stats is not None:
            stats = next((st for st in self._player_stats.all_stats() if st.player.lower() == key), None)
        return summary, sessions, stats

    def ingame_events(
        self,
        user: User,
        limit: int = 300,
        since: datetime | None = None,
    ) -> list[tuple]:
        """Événements en jeu pour le Journal (connexions, déconnexions,
        commandes des joueurs) : (horodatage, joueur, genre, texte), plus
        récent d'abord. Même barrière que le journal (AUDIT_VIEW).
        `since` descend jusqu'au SQL — depuis l'import de l'historique
        Nitroserv, tronquer AVANT de filtrer masquerait les périodes
        anciennes."""
        self._authorize(user, Permission.AUDIT_VIEW)
        events: list[tuple] = []
        for session in self._player_history.recent_sessions(limit, since=since):
            events.append((session.joined_at, session.player, "join", "s'est connecté"))
            if session.left_at is not None:
                minutes = int((session.left_at - session.joined_at).total_seconds() // 60)
                events.append((session.left_at, session.player, "leave",
                               f"s'est déconnecté (session de {minutes} min)"))
        for player, at, command in self._player_history.recent_commands(limit, since=since):
            events.append((at, player, "command", f"commande : {command}"))
        events.sort(key=lambda ev: ev[0], reverse=True)
        return events[:limit]

    # ---- profils spark (perf palier 3) ----

    _SPARK_DURATIONS = (30, 60, 120)

    def spark_profiles(self, user: User) -> list[SparkProfile]:
        """Profils sauvés — même barrière que la console : un profil expose
        les entrailles du serveur."""
        self._authorize(user, Permission.RCON_RAW)
        return self._spark.list_profiles() if self._spark else []

    def spark_profile_path(self, user: User, filename: str) -> str:
        self._authorize(user, Permission.RCON_RAW)
        path = self._spark.path(filename) if self._spark else None
        if path is None:
            raise InvalidGameValue(f"profil introuvable : {filename!r}")
        self._record(user, Permission.RCON_RAW, "allowed", f"phase=spark_download file={filename}")
        return path

    def start_spark_profile(self, user: User, seconds: int) -> None:
        """Lance le profiler. Le STOP est piloté par la page (compte à
        rebours) — filet : `--timeout +30 s` côté spark, pour qu'un onglet
        fermé ne laisse JAMAIS un profiler tourner indéfiniment (dans ce cas
        l'upload spark part dans le vide et aucun fichier n'est écrit —
        constaté : seules les sorties RCON sont muettes, pas le profiler)."""
        self._authorize(user, Permission.RCON_RAW)
        if seconds not in self._SPARK_DURATIONS:
            raise InvalidGameValue(f"durée invalide : {seconds}s")
        self._game.raw(f"spark profiler start --timeout {seconds + 30}")
        self._record(user, Permission.RCON_RAW, "allowed",
                     f"phase=spark_start seconds={seconds}")

    def stop_spark_profile(self, user: User) -> None:
        """Arrête le profiler et sauve le profil SUR DISQUE (--save-to-file :
        le seul canal fiable, les réponses RCON de spark étant muettes)."""
        self._authorize(user, Permission.RCON_RAW)
        self._game.raw("spark profiler stop --save-to-file")
        self._record(user, Permission.RCON_RAW, "allowed", "phase=spark_stop")

    def infra_status(self, user: User) -> list[ContainerState]:
        """État du serveur + des compagnons (carte Infrastructure — lecture,
        même barrière que le status). Compagnon injoignable : état honnête
        « injoignable », jamais d'exception."""
        self._authorize(user, Permission.STATUS)
        states: list[ContainerState] = []
        try:
            states.append(self._container.status())
        except Exception:  # noqa: BLE001 — proxy/conteneur injoignable : état honnête
            states.append(ContainerState(
                name=self._container_name or "serveur", running=False, status="injoignable"))
        if self._watched and self._companion_port_factory:
            for name in self._watched.all():
                try:
                    states.append(self._companion_port_factory(name).status())
                except Exception:  # noqa: BLE001
                    states.append(ContainerState(name=name, running=False, status="injoignable"))
        return states
    def mods_list(self, user: User) -> list[ModInfo]:
        """Mods installés (lecture non auditée, même barrière que le status)."""
        self._authorize(user, Permission.STATUS)
        if self._mods is None:
            return []
        return self._mods.list_mods()

    def mod_updates(self, user: User) -> tuple[dict[str, ModUpdate], datetime | None]:
        """Verdicts Modrinth par empreinte + date de la dernière passe (même
        barrière que la liste des mods — lecture non auditée)."""
        self._authorize(user, Permission.STATUS)
        if self._mod_checks is None:
            return {}, None
        return self._mod_checks.statuses(), self._mod_checks.checked_at()

    def audit_trail(self, user: User, limit: int = 100) -> list[AuditEntry]:
        """Journal d'audit (owner). La consultation elle-même n'est pas
        journalisée en succès (politique des lectures) ; un refus l'est."""
        self._authorize(user, Permission.AUDIT_VIEW)
        return self._audit.recent(limit)

    def events(self, user: User, limit: int = 100) -> list[TimelineEvent]:
        """Timeline unifiée (V5) : audit + sessions joueurs fusionnés, plus
        récent en premier. Même barrière que le journal (AUDIT_VIEW) — la
        timeline expose l'audit complet. Lecture non auditée en succès."""
        self._authorize(user, Permission.AUDIT_VIEW)
        events: list[TimelineEvent] = []
        for entry in self._audit.recent(limit):
            events.append(TimelineEvent(
                kind="audit", timestamp=entry.timestamp,
                title=entry.action, actor=entry.username,
                detail=entry.detail, outcome=entry.outcome,
            ))
        for session in self._player_history.recent_sessions(limit):
            events.append(TimelineEvent(
                kind="join", timestamp=session.joined_at,
                title=f"{session.player} a rejoint le serveur",
            ))
            if session.left_at is not None:
                events.append(TimelineEvent(
                    kind="leave", timestamp=session.left_at,
                    title=f"{session.player} a quitté le serveur",
                ))
        events.sort(key=lambda ev: ev.timestamp, reverse=True)
        return events[:limit]

    def recent_logs(self, user: User, lines: int = 100) -> list[str]:
        self._authorize(user, Permission.LOGS_VIEW)
        return self._logs.recent(lines)

