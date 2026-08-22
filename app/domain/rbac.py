"""Construction des rôles/utilisateurs depuis la config déjà parsée.

Fonctions PURES : elles reçoivent des structures Python (dicts/lists issus du
YAML) et renvoient des `Role`/`User`. La lecture du fichier YAML est un détail
d'I/O confié à un adapter ; ici on ne fait que valider et transformer, ce qui
rend toute la logique RBAC testable sans fichier.
"""
from __future__ import annotations

from .errors import ConfigError
from .model import WILDCARD, Permission, Role, User

# Rôles par défaut d'une installation SANS roles.yml (V6.2 — premier
# lancement en libre-service). Un roles.yml présent les remplace entièrement.
#
# Quatre échelons (22/08/2026), là où il n'y en avait que trois : il manquait
# le MODÉRATEUR, quelqu'un qui gère les joueurs sans pouvoir toucher au
# serveur — le cas courant d'une communauté, et le seul qui n'était couvert ni
# par l'observateur (rien) ni par l'administrateur (tout l'exploitation).
#
# Ce qui reste au propriétaire SEUL, et pourquoi :
#   RCON_RAW        l'échappatoire complète : toute commande, sans garde-fou ;
#   BACKUP_RESTORE  écrase le monde en place ;
#   STOP / UPDATE   arrêt définitif et changement de version du jeu ;
#   SERVER_MANAGE   rebranche l'application sur un autre serveur ;
#   USER_MANAGE     donne et retire les droits — la clé de toutes les autres.
DEFAULT_ROLES_SPEC: dict[str, list[str]] = {
    "owner": ["*"],
    "admin": [
        # tout ce que peut le modérateur…
        "STATUS", "LOGS_VIEW", "AUDIT_VIEW",
        "KICK", "BAN_MANAGE", "WHITELIST_MANAGE",
        # …plus l'exploitation courante du serveur
        "RESTART", "START", "MAINTENANCE",
        "BACKUP_TRIGGER", "BACKUP_DOWNLOAD",
        "GAME_SETTINGS", "OP_MANAGE",
    ],
    "moderator": [
        "STATUS", "LOGS_VIEW", "AUDIT_VIEW",
        "KICK", "BAN_MANAGE", "WHITELIST_MANAGE",
    ],
    "viewer": ["STATUS", "LOGS_VIEW"],   # lecture seule (état + logs)
}


def build_role(name: str, permission_names: list[str]) -> Role:
    """Construit un Role depuis une liste de noms de permissions (dont "*")."""
    perms: set[Permission] = set()
    grants_all = False
    for raw in permission_names or []:
        token = str(raw).strip()
        if token == WILDCARD:
            grants_all = True
            continue
        try:
            perms.add(Permission(token))
        except ValueError as exc:
            raise ConfigError(f"rôle '{name}' : permission inconnue '{token}'") from exc
    return Role(name=name, permissions=frozenset(perms), grants_all=grants_all)


def build_roles(spec: dict[str, list[str]]) -> dict[str, Role]:
    """Transforme la section `roles:` du YAML en {nom: Role}."""
    if not isinstance(spec, dict):
        raise ConfigError("section 'roles' invalide (dict attendu)")
    return {name: build_role(name, perms) for name, perms in spec.items()}


def build_users(spec: dict[str, dict], roles: dict[str, Role]) -> dict[str, User]:
    """Transforme la section `users:` du YAML en {username: User}.

    Ne gère PAS les mots de passe (concern auth/adapter) : uniquement le mapping
    utilisateur → rôle.
    """
    if not isinstance(spec, dict):
        raise ConfigError("section 'users' invalide (dict attendu)")
    users: dict[str, User] = {}
    for username, cfg in spec.items():
        cfg = cfg or {}
        role_name = cfg.get("role")
        if role_name is None:
            raise ConfigError(f"utilisateur '{username}' : champ 'role' manquant")
        role = roles.get(role_name)
        if role is None:
            raise ConfigError(f"utilisateur '{username}' : rôle inconnu '{role_name}'")
        users[username] = User(username=username, role=role)
    return users
