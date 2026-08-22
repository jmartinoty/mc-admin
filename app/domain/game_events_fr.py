"""Libellés FRANÇAIS des faits de jeu.

Ce module est la seule chose à dupliquer pour ajouter une langue (une version
anglaise de l'app est prévue « pour plus tard », cf. demande du 22/08/2026) :
le parseur, la base et les agrégats ne connaissent que des identifiants.

L'anglais aura d'ailleurs la partie facile pour les créatures : le log DONNE
déjà le nom anglais (`GameEvent.killer`), donc `MOBS` y sera superflu — seules
les phrases de `MESSAGES` seront à écrire.
"""
from __future__ import annotations

from domain.game_events import GameEvent

# Phrases qui se lisent APRÈS le pseudo, au format des événements « en jeu »
# déjà affichés dans le Journal (« s'est connecté »). `{killer}` reçoit le
# tueur déjà mis en forme (pseudo nu, ou créature avec son article).
MESSAGES: dict[str, str] = {
    "slain_by": "a été tué par {killer}",
    "shot_by": "a été abattu par {killer}",
    "pummeled_by": "a été assommé par {killer}",
    "fireballed_by": "a été carbonisé par {killer}",
    "impaled_by": "a été empalé par {killer}",
    "speared_by": "a été transpercé par {killer}",
    "blown_up_by": "a été soufflé par {killer}",
    "squashed_by": "a été écrasé par {killer}",
    "stung_by": "est mort piqué par {killer}",
    "magic_by": "a été tué par la magie de {killer}",
    "killed_by": "a été tué par {killer}",
    "doomed_to_fall_by": "a été précipité dans le vide par {killer}",
    "burnt_fighting": "a brûlé vif en combattant {killer}",
    "fire_fighting": "a marché dans le feu en combattant {killer}",
    "cactus_escaping": "s'est empalé sur un cactus en fuyant {killer}",
    "drowned_escaping": "s'est noyé en fuyant {killer}",
    "lava_escaping": "a plongé dans la lave pour fuir {killer}",
    "fall_escaping": "s'est écrasé au sol en fuyant {killer}",
    "despair": "n'a pas supporté de vivre dans le même monde que {killer}",
    "thorns": "s'est tué en tentant de blesser {killer}",
    "stalagmite_fighting": "s'est empalé sur une stalagmite en combattant {killer}",
    "fell_high": "est tombé de haut",
    "fell_ladder": "est tombé d'une échelle",
    "fell_vines": "est tombé de lianes",
    "fell_scaffolding": "est tombé d'un échafaudage",
    "fell_climbing": "a chuté en escaladant",
    "doomed_to_fall": "était condamné à tomber",
    "void": "est tombé hors du monde",
    "ground_too_hard": "s'est écrasé au sol",
    "kinetic": "s'est écrasé en elytra",
    "anvil": "a été écrasé par une enclume",
    "falling_block": "a été écrasé par un bloc",
    "stalactite": "a été transpercé par une stalactite",
    "stalagmite": "s'est empalé sur une stalagmite",
    "drowned": "s'est noyé",
    "burned": "a brûlé vif",
    "in_flames": "s'est embrasé",
    "burnt_crisp": "a été réduit en cendres",
    "floor_was_lava": "a découvert que le sol était de la lave",
    "swim_in_lava": "a tenté de nager dans la lave",
    "danger_zone": "a marché dans la zone de danger",
    "lightning": "a été foudroyé",
    "froze": "est mort de froid",
    "starved": "est mort de faim",
    "suffocated": "a suffoqué dans un mur",
    "squished": "a été trop compressé",
    "withered": "a dépéri",
    "pricked": "est mort piqué par un cactus",
    "walked_into_cactus": "a marché dans un cactus",
    "berry_bush": "est mort piqué par un buisson de baies",
    "sonic_boom": "a été pulvérisé par le hurlement du Warden",
    "dragon_breath": "a été rôti par le souffle du dragon",
    "firework": "a explosé en feu d'artifice",
    "more_magic": "a été tué par une magie plus forte",
    "magic": "a été tué par magie",
    "died": "est mort",
    "advancement": "a obtenu le succès « {detail} »",
    "chat": "a écrit : {detail}",
}

# Créatures, avec leur article — indexées par IDENTIFIANT, jamais par nom
# anglais : c'est ce qui rend le dictionnaire stable si Mojang change un
# libellé, et réutilisable pour l'historique déjà enregistré.
#
# La liste couvre les 57 entités réellement présentes dans les statistiques du
# serveur de Jeremy (26.1). Une créature absente d'ici s'affiche avec son nom
# d'origine : c'est le comportement voulu, et jamais une erreur.
MOBS: dict[str, str] = {
    "minecraft:armadillo": "un Tatou",
    "minecraft:bat": "une Chauve-souris",
    "minecraft:bee": "une Abeille",
    "minecraft:blaze": "un Blaze",
    "minecraft:bogged": "un Enlisé",
    "minecraft:breeze": "une Brise",
    "minecraft:cave_spider": "une Araignée venimeuse",
    "minecraft:chicken": "une Poule",
    "minecraft:cow": "une Vache",
    "minecraft:creeper": "un Creeper",
    "minecraft:donkey": "un Âne",
    "minecraft:drowned": "un Noyé",
    "minecraft:elder_guardian": "un Gardien ancien",
    "minecraft:ender_dragon": "le Dragon de l'Ender",
    "minecraft:enderman": "un Enderman",
    "minecraft:evoker": "un Évocateur",
    "minecraft:ghast": "un Ghast",
    "minecraft:glow_squid": "une Pieuvre luminescente",
    "minecraft:guardian": "un Gardien",
    "minecraft:hoglin": "un Hoglin",
    "minecraft:horse": "un Cheval",
    "minecraft:husk": "un Zombie momifié",
    "minecraft:iron_golem": "un Golem de fer",
    "minecraft:magma_cube": "un Cube de magma",
    "minecraft:phantom": "un Phantom",
    "minecraft:pig": "un Cochon",
    "minecraft:piglin": "un Piglin",
    "minecraft:piglin_brute": "une Brute piglin",
    "minecraft:pillager": "un Pillard",
    "minecraft:polar_bear": "un Ours polaire",
    "minecraft:ravager": "un Ravageur",
    "minecraft:salmon": "un Saumon",
    "minecraft:sheep": "un Mouton",
    "minecraft:shulker": "un Shulker",
    "minecraft:silverfish": "un Poisson d'argent",
    "minecraft:skeleton": "un Squelette",
    "minecraft:skeleton_horse": "un Cheval squelette",
    "minecraft:slime": "un Slime",
    "minecraft:spider": "une Araignée",
    "minecraft:squid": "une Pieuvre",
    "minecraft:trader_llama": "un Lama de marchand",
    "minecraft:tropical_fish": "un Poisson tropical",
    "minecraft:turtle": "une Tortue",
    "minecraft:vex": "un Vex",
    "minecraft:villager": "un Villageois",
    "minecraft:vindicator": "un Vindicateur",
    "minecraft:wandering_trader": "un Marchand ambulant",
    "minecraft:warden": "un Warden",
    "minecraft:witch": "une Sorcière",
    "minecraft:wither_skeleton": "un Squelette wither",
    "minecraft:zombie": "un Zombie",
    "minecraft:zombie_horse": "un Cheval zombie",
    "minecraft:zombie_villager": "un Villageois zombie",
    "minecraft:zombified_piglin": "un Piglin zombifié",
}

# Catégories de dégâts, pour les vues « morts par cause ».
CAUSES: dict[str, str] = {
    "attack": "Corps à corps",
    "projectile": "Projectile",
    "explosion": "Explosion",
    "fall": "Chute",
    "drown": "Noyade",
    "fire": "Feu",
    "lava": "Lave",
    "magic": "Magie",
    "void": "Vide",
    "freeze": "Froid",
    "starve": "Faim",
    "suffocation": "Étouffement",
    "wither": "Wither",
    "cactus": "Cactus",
    "lightning": "Foudre",
    "crush": "Écrasement",
    "kinetic": "Choc en elytra",
    "sonic": "Warden",
    "dragon_breath": "Souffle du dragon",
    "berry": "Buisson de baies",
    "firework": "Feu d'artifice",
    "sting": "Piqûre",
    "thorns": "Épines",
    "unknown": "Inconnue",
}


def mob_name(killer_id: str, fallback: str) -> str:
    """Créature en français, avec son article. `fallback` (le nom brut du log)
    sert aux créatures absentes du dictionnaire : mob renommé par étiquette,
    entité d'un mod, ou espèce ajoutée par une version du jeu plus récente que
    ce fichier — le serveur de Jeremy tourne en 26.1 et voit déjà des mobs
    comme `parched` ou `happy_ghast`."""
    known = MOBS.get(killer_id)
    if known:
        return known
    return f"un {fallback}" if fallback else "quelque chose"


def describe(event: GameEvent, killer_is_player: bool = False) -> str:
    """Phrase française à afficher APRÈS le pseudo, au format des événements
    « en jeu » du Journal.

    `killer_is_player` vient de l'appelant : un joueur est annoncé par son seul
    pseudo, une créature avec son article. C'est ce qui rend un duel lisible
    d'un coup d'œil dans le flux."""
    template = MESSAGES.get(event.key)
    if template is None:
        return MESSAGES["died"] if event.kind == "death" else ""
    killer = (event.killer if killer_is_player
              else mob_name(event.killer_id, event.killer))
    text = template.format(killer=killer, detail=event.detail)
    if event.kind == "death" and event.weapon:
        text += f" (avec {event.weapon})"
    return text


def cause_label(cause: str) -> str:
    return CAUSES.get(cause, CAUSES["unknown"])
