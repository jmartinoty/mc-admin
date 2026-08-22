"""Suivi des joueurs en tâche de fond : tail de `latest.log` (V3.4).

Depuis la V0.17 il capte AUSSI les faits de jeu (morts, succès, chat) via
`domain.game_events`. Un seul tailer pour tout : ouvrir un second lecteur sur
le même fichier dupliquerait l'I/O et la gestion de position, pour rien.

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
from datetime import datetime, timedelta, timezone

from domain.game_events import parse_line

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
                 notify_cooldown_seconds: float = NOTIFY_COOLDOWN_SECONDS,
                 game_events=None, known_players=None,
                 chat_retention_days: int = 0) -> None:
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
        # Faits de jeu (GameEventsPort). None = désactivé : le tailer garde
        # exactement le comportement d'avant, joins/leaves/commandes.
        self._game_events = game_events
        # Rend l'ensemble des pseudos connus (minuscules) : c'est LUI qui
        # tranche « tué par un joueur » vs « tué par une créature », le
        # parseur ne voyant qu'un nom. Source côté app : usercache du serveur
        # + joueurs déjà vus. Limite assumée : un joueur totalement inconnu
        # (jamais connecté, absent du usercache) serait pris pour une
        # créature — la ligne brute est conservée en base, donc rattrapable.
        self._known_players = known_players
        self._chat_retention_days = chat_retention_days
        self._last_purge: datetime | None = None
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
            self._purge_chat_if_due()
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
        """Lit ce qui a été ajouté depuis `position` et rend la nouvelle.

        DÉTECTE LA ROTATION (bug constaté le 22/08/2026) : Minecraft archive
        `latest.log` chaque nuit et à chaque redémarrage, puis repart d'un
        fichier vide. Sans ce contrôle, on cherchait à lire à l'octet 96 000
        d'un fichier qui n'en faisait plus que 5 000 : aucune ligne n'était
        lue, la position ne bougeait pas, et le tailer restait AVEUGLE
        jusqu'à ce que le nouveau fichier dépasse l'ancienne taille — des
        heures, parfois. C'est ainsi qu'un « left the game » a été manqué,
        laissant un joueur affiché en ligne pendant plus d'un jour.

        Un fichier plus court que notre position ne peut être que le signe
        d'une rotation ou d'une troncature : on repart du début, car ce
        début est du contenu neuf, jamais vu."""
        try:
            with open(self._path, encoding="utf-8", errors="replace") as fh:
                fh.seek(0, 2)
                taille = fh.tell()
                if taille < position:
                    position = 0
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
        elif self._game_events is not None:
            self._record_game_event(line)

    def _record_game_event(self, line: str) -> None:
        """Morts, succès et chat. Jamais notifié (choix de Jeremy 22/08/2026 :
        le suivi se consulte, il ne sonne pas) — et JAMAIS bloquant : une
        écriture qui échoue ne doit pas tuer le tailer, dont la mission
        première reste les connexions."""
        event = parse_line(line)
        if event is None:
            return
        killer_type = ""
        if event.kind == "death" and event.killer:
            killer_type = "player" if self._is_player(event.killer) else "mob"
        try:
            self._game_events.record(event, self._clock(), killer_type)
        except Exception:  # noqa: BLE001 - le tailer survit à tout
            pass

    def _is_player(self, name: str) -> bool:
        if self._known_players is None:
            return False
        try:
            return name.lower() in self._known_players()
        except Exception:  # noqa: BLE001 - source indisponible = créature
            return False

    def _purge_chat_if_due(self) -> None:
        """Rétention du chat : une passe par jour suffit (choix de conception —
        les messages sont de la conversation entre joueurs, pas des faits de
        jeu, cf. GAME_CHAT_RETENTION_DAYS). 0 = conservation illimitée."""
        if self._game_events is None or self._chat_retention_days <= 0:
            return
        now = self._clock()
        if self._last_purge is not None and (now - self._last_purge) < timedelta(days=1):
            return
        self._last_purge = now
        try:
            self._game_events.purge_chat(now - timedelta(days=self._chat_retention_days))
        except Exception:  # noqa: BLE001
            pass

    def _may_notify(self, player: str, now: datetime) -> bool:
        """Fenêtre anti-spam par joueur : True au plus une fois par cooldown."""
        last = self._last_notified.get(player)
        if last is not None and (now - last).total_seconds() < self._notify_cooldown:
            return False
        self._last_notified[player] = now
        return True
