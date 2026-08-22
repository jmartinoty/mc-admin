"""Analyse des faits de jeu écrits par le serveur dans `latest.log`.

Module PUR (aucune I/O, aucun état) : le tailer lui passe des lignes, il rend
un événement ou `None`. C'est ce qui permet de le tester sur les vraies formes
rencontrées dans les archives sans monter de serveur.

Trois familles reconnues :
  - `death`       : les messages de mort vanilla (« X was slain by Y ») ;
  - `advancement` : succès, défis et objectifs ;
  - `chat`        : messages des joueurs.

RIEN ICI N'EST TRADUIT — c'est délibéré (demande de Jeremy, 22/08/2026, en vue
d'une version anglaise de l'app). Un événement porte des identifiants stables :

  - `key`       : quel message ("slain_by", "fell_high"…), indépendant de la
                  langue ; c'est lui qui choisit la phrase à l'affichage ;
  - `cause`     : la catégorie de dégâts ("attack", "fall", "explosion"…),
                  pour les agrégats « morts par cause » ;
  - `killer_id` : l'entité au format Minecraft (`minecraft:cave_spider`).

La base stocke donc des identifiants, jamais des phrases : ajouter une langue
se fera en écrivant un dictionnaire, sans toucher ni au parseur ni aux données
déjà enregistrées, qui deviendront traduisibles rétroactivement.

DEUX PIÈGES, tous deux constatés dans les archives réelles de Jeremy :

1. « X was kicked due to keepalive timeout! » et « X was kicked for floating
   too long! » ressemblent aux messages de mort (13 occurrences dans
   l'historique Nitroserv) mais n'en sont PAS. On ne reconnaît donc QUE des
   motifs explicitement listés : tout le reste est ignoré. Une liste blanche,
   jamais un « X was ... » générique.

2. Le chat est écrit sur les mêmes lignes que les messages du serveur. Un
   joueur qui tape « Toto was slain by moi » produirait un faux événement :
   on refuse donc toute ligne dont le contenu commence par « < » (chat) avant
   d'y chercher une mort.

Le parseur ne décide PAS si un tueur est un joueur ou une créature : il rend
le nom brut et l'identifiant dérivé, et l'appelant tranche en confrontant le
nom aux joueurs connus (players.db, usercache, whitelist).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

# Pseudo Minecraft : 3 à 16 caractères alphanumériques ou underscore. Sert
# pour la VICTIME, qui est toujours un joueur.
_NAME = r"[A-Za-z0-9_]{3,16}"

# Le TUEUR, lui, n'est pas forcément un pseudo : les noms d'entité comportent
# des espaces (« Cave Spider », « Iron Golem », « Magma Cube », « Piglin
# Brute » — tous relevés dans les archives réelles), et un mob peut porter un
# nom personnalisé via une étiquette. Motif non gourmand et borné : assez
# large pour ces noms, assez serré pour ne pas avaler une ligne entière.
_KILLER = r".{1,48}?"

# Contenu utile d'une ligne : « [21:15:16] [Server thread/INFO]: <message> ».
# Le thread est capturé, car il décide de ce qu'on accepte d'y lire : les
# lignes de mods et de workers ne portent pas de faits de jeu et ouvriraient
# la porte aux faux positifs.
_LINE_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] \[([^\]]+)/INFO\]: (.+)$")

_SERVER_THREAD = "Server thread"

# PaperMC écrit le chat depuis un thread ASYNCHRONE dédié (constaté sur les
# archives Nitroserv : 7 messages d'août-septembre 2025 étaient perdus, dont
# celui qui explique pourquoi les autres manquent — « je suis sur discord si
# jamais »). On accepte donc ce thread, mais UNIQUEMENT pour du chat : aucune
# mort n'y transite, et l'y autoriser rouvrirait la porte aux faux positifs.
_CHAT_THREAD_RE = re.compile(r"^Async Chat Thread - #\d+$")

_CHAT_RE = re.compile(rf"^<({_NAME})> (.+)$")
_ADVANCEMENT_RE = re.compile(
    rf"^({_NAME}) has (?:made the advancement|completed the challenge|reached the goal) "
    r"\[(.+)\]$"
)

# Un nom d'entité vanilla s'écrit en mots capitalisés séparés par des espaces
# (« Cave Spider »). Tout le reste — ponctuation, accents, chiffres — trahit un
# mob renommé par étiquette ou une entité de mod : pas d'identifiant dérivable.
_ENTITY_NAME_RE = re.compile(r"^[A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*)*$")


def entity_id(name: str) -> str:
    """Identifiant Minecraft d'un nom affiché : « Cave Spider » ->
    `minecraft:cave_spider`. Rend "" si le nom ne peut pas en porter un.

    La règle (minuscules, espaces en underscores) est celle de la langue
    anglaise du jeu ; elle a été VÉRIFIÉE sur les données réelles du serveur de
    Jeremy : les 16 noms croisés dans ses logs se retrouvent tous parmi les 57
    identifiants présents dans ses fichiers de statistiques (`minecraft:killed`
    et `minecraft:killed_by`). Ces fichiers restent la source d'autorité — un
    mob qui tue un joueur atterrit forcément dans son `killed_by`, donc le
    catalogue se remplit tout seul, sans liste à maintenir dans le code.
    """
    if not name or not _ENTITY_NAME_RE.match(name):
        return ""
    return "minecraft:" + name.lower().replace(" ", "_")


@dataclass(frozen=True)
class GameEvent:
    """Un fait de jeu analysé. Volontairement SANS horodatage : les lignes du
    log ne portent que l'heure (« [21:15:16] »), jamais la date. C'est
    l'appelant qui date — l'instant courant pour le tailer, le nom du fichier
    d'archive pour l'import rétroactif."""

    kind: str            # "death" | "advancement" | "chat"
    player: str          # victime, ou auteur du succès / du message
    key: str = ""        # clé de message, langue-neutre (cf. _DEATHS)
    cause: str = ""      # catégorie de dégâts, pour les agrégats
    killer: str = ""     # nom BRUT du tueur ("" = environnement)
    killer_id: str = ""  # `minecraft:...` quand il est dérivable
    weapon: str = ""     # arme nommée, quand le message la donne
    detail: str = ""     # nom du succès, ou message de chat
    raw: str = ""        # ligne d'origine, pour re-parser plus tard


@dataclass(frozen=True)
class RecordedEvent:
    """Un fait de jeu tel qu'il ressort de la base : le même contenu que
    `GameEvent`, plus la date qu'on lui a donnée et le verdict joueur/créature
    tranché à l'écriture."""

    at: datetime
    kind: str
    player: str
    key: str = ""
    cause: str = ""
    killer: str = ""
    killer_id: str = ""
    killer_type: str = ""   # "player" | "mob" | "" (environnement)
    weapon: str = ""
    detail: str = ""
    raw: str = ""

    @property
    def killer_is_player(self) -> bool:
        return self.killer_type == "player"


@dataclass(frozen=True)
class PlayerGameRecord:
    """Ce qu'un joueur a vécu, tel que le Journal l'a observé — à distinguer
    des compteurs vanilla de la même page : ceux-ci remontent à la création du
    monde et ne sont ni datés ni détaillés, là où ceux-là sont vérifiables
    ligne par ligne."""

    deaths: int = 0
    kills: int = 0                                   # duels gagnés
    top_cause: tuple[str, int] | None = None         # (cause, nombre)
    nemesis_mob: tuple[str, str, int] | None = None  # (killer_id, nom brut, nombre)
    nemesis_player: tuple[str, int] | None = None    # (pseudo, nombre)
    victims: tuple[tuple[str, int], ...] = ()        # (pseudo, nombre)
    advancements: int = 0
    messages: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    @property
    def nemesis(self) -> tuple[str, str, str, int] | None:
        """(nature, identifiant, nom, nombre) de qui l'a le plus tué — joueur
        ou créature confondus. C'est le NOMBRE qui tranche : un rival croisé
        une fois ne doit pas éclipser la créature qui vous tue chaque semaine.
        """
        candidats = []
        if self.nemesis_player is not None:
            candidats.append(("player", "", self.nemesis_player[0], self.nemesis_player[1]))
        if self.nemesis_mob is not None:
            candidats.append(("mob", self.nemesis_mob[0], self.nemesis_mob[1],
                              self.nemesis_mob[2]))
        return max(candidats, key=lambda c: c[3]) if candidats else None


# Messages de mort vanilla : (motif après le pseudo, clé de message, cause).
#
# L'ordre compte — les motifs les plus spécifiques (avec tueur) passent AVANT
# leur variante nue, sinon « drowned whilst trying to escape X » serait
# reconnu comme une simple noyade.
_DEATHS: tuple[tuple[str, str, str], ...] = (
    # --- morts causées par une entité nommée ---
    (rf"was slain by ({_KILLER})", "slain_by", "attack"),
    (rf"was shot by ({_KILLER})", "shot_by", "projectile"),
    (rf"was pummeled by ({_KILLER})", "pummeled_by", "attack"),
    (rf"was fireballed by ({_KILLER})", "fireballed_by", "fire"),
    (rf"was impaled by ({_KILLER})", "impaled_by", "attack"),
    (rf"was speared by ({_KILLER})", "speared_by", "projectile"),
    (rf"was blown up by ({_KILLER})", "blown_up_by", "explosion"),
    (rf"was squashed by ({_KILLER})", "squashed_by", "crush"),
    (rf"was stung to death by ({_KILLER})", "stung_by", "sting"),
    (rf"was killed by ({_KILLER}) using magic", "magic_by", "magic"),
    (rf"was killed by ({_KILLER})", "killed_by", "attack"),
    (rf"was doomed to fall by ({_KILLER})", "doomed_to_fall_by", "fall"),
    (rf"was burnt to a crisp whilst fighting ({_KILLER})", "burnt_fighting", "fire"),
    (rf"walked into fire whilst fighting ({_KILLER})", "fire_fighting", "fire"),
    (rf"walked into a cactus whilst trying to escape ({_KILLER})", "cactus_escaping", "cactus"),
    (rf"drowned whilst trying to escape ({_KILLER})", "drowned_escaping", "drown"),
    (rf"tried to swim in lava to escape ({_KILLER})", "lava_escaping", "lava"),
    (rf"hit the ground too hard whilst trying to escape ({_KILLER})", "fall_escaping", "fall"),
    (rf"didn't want to live in the same world as ({_KILLER})", "despair", "magic"),
    (rf"was killed while trying to hurt ({_KILLER})", "thorns", "thorns"),
    (rf"was impaled on a stalagmite whilst fighting ({_KILLER})", "stalagmite_fighting", "crush"),
    # --- morts sans tueur ---
    ("fell from a high place", "fell_high", "fall"),
    ("fell off a ladder", "fell_ladder", "fall"),
    ("fell off some vines", "fell_vines", "fall"),
    ("fell off scaffolding", "fell_scaffolding", "fall"),
    ("fell while climbing", "fell_climbing", "fall"),
    ("was doomed to fall", "doomed_to_fall", "fall"),
    ("fell out of the world", "void", "void"),
    ("hit the ground too hard", "ground_too_hard", "fall"),
    ("experienced kinetic energy", "kinetic", "kinetic"),
    ("was squashed by a falling anvil", "anvil", "crush"),
    ("was squashed by a falling block", "falling_block", "crush"),
    ("was skewered by a falling stalactite", "stalactite", "crush"),
    ("was impaled on a stalagmite", "stalagmite", "crush"),
    ("drowned", "drowned", "drown"),
    ("burned to death", "burned", "fire"),
    ("went up in flames", "in_flames", "fire"),
    ("was burnt to a crisp", "burnt_crisp", "fire"),
    ("discovered the floor was lava", "floor_was_lava", "lava"),
    ("tried to swim in lava", "swim_in_lava", "lava"),
    ("walked into danger zone due to intentional game design", "danger_zone", "lava"),
    ("was struck by lightning", "lightning", "lightning"),
    ("froze to death", "froze", "freeze"),
    ("starved to death", "starved", "starve"),
    ("suffocated in a wall", "suffocated", "suffocation"),
    ("was squished too much", "squished", "suffocation"),
    ("withered away", "withered", "wither"),
    ("was pricked to death", "pricked", "cactus"),
    ("walked into a cactus", "walked_into_cactus", "cactus"),
    ("was poked to death by a sweet berry bush", "berry_bush", "berry"),
    ("was obliterated by a sonically-charged shriek", "sonic_boom", "sonic"),
    ("was roasted in dragon's breath", "dragon_breath", "dragon_breath"),
    ("went off with a bang", "firework", "firework"),
    ("was killed by even more magic", "more_magic", "magic"),
    ("was killed by magic", "magic", "magic"),
    ("died", "died", "unknown"),
)

# Compilation une fois pour toutes. « using [Épée] » est un suffixe OPTIONNEL
# commun à tous les motifs à tueur : on le capture ici plutôt que de doubler
# chaque entrée du tableau.
_DEATH_RES: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (re.compile(rf"^({_NAME}) {pattern}(?: using \[?([^\]]+)\]?)?$"), key, cause)
    for pattern, key, cause in _DEATHS
)

# Clés de mort qui portent un tueur : l'absence de `killer` sur l'une d'elles
# signale un défaut d'analyse, pas une mort accidentelle.
KEYS_WITH_KILLER = frozenset(
    key for pattern, key, _cause in _DEATHS if _KILLER in pattern
)


def parse_line(line: str) -> GameEvent | None:
    """Rend le fait de jeu porté par `line`, ou `None` si la ligne n'en porte
    pas. Ne lève JAMAIS : le tailer tourne en tâche de fond et une ligne
    biscornue (mod exotique, log tronqué) ne doit pas l'arrêter."""
    match = _LINE_RE.match(line.rstrip("\n"))
    if match is None:
        return None
    thread, body = match.group(1), match.group(2).strip()
    if thread != _SERVER_THREAD and not _CHAT_THREAD_RE.match(thread):
        return None

    chat = _CHAT_RE.match(body)
    if chat is not None:
        return GameEvent(kind="chat", player=chat.group(1), key="chat",
                         detail=chat.group(2), raw=body)
    if thread != _SERVER_THREAD:
        return None  # thread de chat : rien d'autre n'y est digne de confiance

    # Après ce point, une ligne de chat ne doit plus être analysée : un joueur
    # pourrait sinon fabriquer de faux événements en les tapant dans le tchat.
    if body.startswith("<"):
        return None

    advancement = _ADVANCEMENT_RE.match(body)
    if advancement is not None:
        return GameEvent(kind="advancement", player=advancement.group(1),
                         key="advancement", detail=advancement.group(2), raw=body)

    for regex, key, cause in _DEATH_RES:
        found = regex.match(body)
        if found is None:
            continue
        groups = found.groups()
        # Le premier groupe est toujours la victime ; le tueur n'existe que
        # pour les motifs qui en portent un, l'arme est toujours en dernier.
        killer = (groups[1] or "") if len(groups) == 3 else ""
        return GameEvent(kind="death", player=groups[0], key=key, cause=cause,
                         killer=killer, killer_id=entity_id(killer),
                         weapon=groups[-1] or "", raw=body)
    return None
