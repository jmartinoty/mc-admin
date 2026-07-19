"""Suivi des joueurs en tâche de fond : tail de `latest.log` (V3.4).

Détecte les « X joined the game » / « X left the game » dès qu'ils surviennent
(pas seulement au polling status) et les écrit DIRECTEMENT dans
PlayerHistoryPort — PAS via AdminService : ce n'est pas une action utilisateur
soumise à RBAC, c'est de l'observation passive (comme un collector). Seule la
LECTURE (`AdminService.player_history`) passe par le service et sa barrière.

Ne repart JAMAIS depuis le début du fichier (seek en fin de fichier au
démarrage) : au redémarrage de mc-admin, seuls les événements FUTURS sont
capturés — cohérent avec le fait que le fichier est monté en lecture seule et
peut déjà contenir des mois d'historique.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timezone

_JOIN_RE = re.compile(r"(\w{3,16}) joined the game")
_LEAVE_RE = re.compile(r"(\w{3,16}) left the game")
# Commande lancée en jeu — format vanilla « X issued server command: /... ».
# Les commandes mc-admin (RCON) ne matchent pas : elles sont journalisées
# différemment et déjà tracées par l'audit.
_CMD_RE = re.compile(r"(\w{3,16}) issued server command: (.+)$")

# Anti-spam (choix Jeremy, 12/07/2026) : un même joueur ne génère plus AUCUNE
# notification pendant ce délai après sa dernière — un joueur qui se
# reconnecte en boucle n'inonde pas Discord. L'HISTORIQUE, lui, enregistre
# toujours tout : seule la notification est limitée.
NOTIFY_COOLDOWN_SECONDS = 15 * 60


class PlayerLogWatcher:
    def __init__(self, log_path: str, history, poll_interval: float = 2.0, clock=None, notifier=None,
                 notify_cooldown_seconds: float = NOTIFY_COOLDOWN_SECONDS) -> None:
        self._path = log_path
        self._history = history
        self._poll_interval = poll_interval
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # Optionnel (None = désactivé, cf. NOTIFY_PLAYER_EVENTS) : notifie les
        # joins/leaves. Le contrat NotificationPort (`notify` NE LÈVE JAMAIS)
        # protège déjà la boucle ; pas de try/except supplémentaire.
        self._notifier = notifier
        self._notify_cooldown = notify_cooldown_seconds
        self._last_notified: dict[str, datetime] = {}
        self._stop = threading.Event()
        # Position initiale prise (fin de fichier) : les écritures POSTÉRIEURES
        # sont garanties vues. Les tests attendent cet événement avant d'écrire
        # (sinon : course entre le seek initial et leur première ligne).
        self.ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="player-watcher")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        position = self._initial_position()
        self.ready.set()
        while not self._stop.is_set():
            position = self._consume_new_lines(position)
            if self._stop.wait(self._poll_interval):
                break

    def _initial_position(self) -> int:
        try:
            with open(self._path, encoding="utf-8", errors="replace") as fh:
                fh.seek(0, 2)  # fin de fichier : ignore tout l'historique déjà écrit
                return fh.tell()
        except FileNotFoundError:
            return 0

    def _consume_new_lines(self, position: int) -> int:
        try:
            with open(self._path, encoding="utf-8", errors="replace") as fh:
                fh.seek(position)
                for line in fh:
                    self._handle_line(line)
                return fh.tell()
        except FileNotFoundError:
            return position

    def _handle_line(self, line: str) -> None:
        if match := _JOIN_RE.search(line):
            now = self._clock()
            player = match.group(1)
            # AVANT record_join : après, il est forcément « connu ».
            first_time = not self._history.is_known(player)
            self._history.record_join(player, now)
            if self._notifier is not None and self._may_notify(player, now):
                self._notifier.notify("Joueur connecté", f"{player} a rejoint le serveur", "info", event="player")
            if first_time and self._notifier is not None:
                self._notifier.notify("Nouveau joueur 🎉",
                                      f"Première connexion de {player} !", "info",
                                      event="new_player")
        elif match := _LEAVE_RE.search(line):
            now = self._clock()
            self._history.record_leave(match.group(1), now)
            if self._notifier is not None and self._may_notify(match.group(1), now):
                self._notifier.notify("Joueur déconnecté", f"{match.group(1)} a quitté le serveur", "info", event="player")
        elif match := _CMD_RE.search(line):
            # Jamais notifié (bruit) : consultable dans le Journal, famille « En jeu ».
            self._history.record_command(match.group(1), match.group(2).strip(), self._clock())

    def _may_notify(self, player: str, now: datetime) -> bool:
        """Fenêtre anti-spam par joueur : True au plus une fois par cooldown."""
        last = self._last_notified.get(player)
        if last is not None and (now - last).total_seconds() < self._notify_cooldown:
            return False
        self._last_notified[player] = now
        return True
