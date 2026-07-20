"""Erreurs du domaine — indépendantes de tout transport/framework."""
from __future__ import annotations


class DomainError(Exception):
    """Base de toutes les erreurs métier."""


class PermissionDenied(DomainError):
    """Un utilisateur a tenté une action non couverte par son rôle (RBAC #3).

    L'appel est refusé CÔTÉ SERVEUR, quel que soit ce que montre l'UI.
    """

    def __init__(self, username: str, permission: str) -> None:
        self.username = username
        self.permission = permission
        super().__init__(f"user '{username}' n'a pas la permission {permission}")


class ServerUnavailable(DomainError):
    """Le serveur de jeu est injoignable (ex. RCON down) — dégradation attendue,
    pas un plantage : le status doit rester consultable."""


class UnauthorizedContainer(DomainError):
    """Tentative de piloter un conteneur ≠ celui configuré (MINECRAFT_CONTAINER).

    Garde-fou contre le risque résiduel du socket-proxy, qui ne filtre pas par
    nom de conteneur (cf. CLAUDE.md §7.1). Levé par l'adapter ContainerPort.
    """


class ConfigError(DomainError):
    """Configuration RBAC invalide (permission/rôle inconnu, champ manquant)."""


class BackupUnavailable(DomainError):
    """La sauvegarde n'est pas déclenchable (adapter backup non encore déployé)."""


class BackupArchiveNotFound(DomainError):
    """Archive de sauvegarde introuvable ou nom invalide."""


class UpdateBlocked(DomainError):
    """Mise à jour refusée par un garde-fou métier (joueurs connectés, état
    inconnu) tant que l'owner n'a pas confirmé explicitement (`force`)."""


class UpdateUnavailable(DomainError):
    """Le mécanisme de mise à jour n'est pas déclenchable (conteneur mc-updater
    absent / non créé — cf. profil compose `tools`)."""


class InvalidPlayerName(DomainError):
    """Pseudo Minecraft invalide (attendu : [A-Za-z0-9_]{3,16}).

    Validé DANS le domaine avant tout envoi RCON : bloque toute tentative
    d'injection de contenu arbitraire dans une commande whitelist.
    """


class InvalidDuration(DomainError):
    """Durée de ban temporisé invalide (doit être strictement positive)."""


class InvalidOpLevel(DomainError):
    """Niveau d'opérateur invalide (doit être un entier de 1 à 4)."""


class OpLevelsUnavailable(DomainError):
    """Un niveau OP individuel n'a pas pu être enregistré ou appliqué."""


class RestoreUnavailable(DomainError):
    """Restauration non déclenchable (conteneur mc-restore absent / non créé,
    ou archive introuvable)."""


class InvalidGameValue(DomainError):
    """Valeur de réglage de jeu hors liste blanche (difficulté, gamerule…)."""


class MaintenanceUnavailable(DomainError):
    """Mode maintenance non actionnable : portier non configuré/créé, déjà en
    place, ou impossible à relever proprement (le serveur ne redémarre jamais
    tant que le portier tient encore son endpoint réseau)."""
