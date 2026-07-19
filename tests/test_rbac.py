"""Tests RBAC : construction des rôles/utilisateurs et matrice permission×rôle.

Aucun I/O : on part de specs Python (comme le YAML une fois parsé).
"""
from __future__ import annotations

import unittest

from domain.errors import ConfigError
from domain.model import Permission
from domain.rbac import build_role, build_roles, build_users

# Spec de référence (miroir de config/roles.example.yml + un rôle "guest" vide
# pour tester le refus des lectures).
ROLE_SPEC = {
    "owner": ["*"],
    "admin": ["STATUS", "LOGS_VIEW", "RESTART", "START", "BACKUP_TRIGGER", "BACKUP_DOWNLOAD", "KICK"],
    "friend": ["STATUS", "LOGS_VIEW"],
    "guest": [],
}
USER_SPEC = {
    "jeremy": {"role": "owner"},
    "paul": {"role": "admin"},
    "sam": {"role": "friend"},
    "vic": {"role": "guest"},
}

# Matrice attendue : (rôle, permission) -> autorisé ?
EXPECTED = {
    "owner": {p: True for p in Permission},
    "admin": {
        Permission.STATUS: True,
        Permission.LOGS_VIEW: True,
        Permission.RESTART: True,
        Permission.START: True,
        Permission.BACKUP_TRIGGER: True,
        Permission.BACKUP_DOWNLOAD: True,
        Permission.BACKUP_RETENTION: False,
        Permission.RCON_RAW: False,
        Permission.OP_MANAGE: False,
        Permission.STOP: False,
        Permission.UPDATE: False,
        Permission.WHITELIST_MANAGE: False,
        Permission.AUDIT_VIEW: False,
        Permission.KICK: True,
        Permission.BAN_MANAGE: False,
    },
    "friend": {
        Permission.STATUS: True,
        Permission.LOGS_VIEW: True,
        Permission.RESTART: False,
        Permission.START: False,
        Permission.BACKUP_TRIGGER: False,
        Permission.BACKUP_DOWNLOAD: False,
        Permission.BACKUP_RETENTION: False,
        Permission.RCON_RAW: False,
        Permission.OP_MANAGE: False,
        Permission.STOP: False,
        Permission.UPDATE: False,
        Permission.WHITELIST_MANAGE: False,
        Permission.AUDIT_VIEW: False,
        Permission.KICK: False,
        Permission.BAN_MANAGE: False,
    },
    "guest": {p: False for p in Permission},
}


class TestRoleBuilding(unittest.TestCase):
    def test_wildcard_grants_every_permission(self):
        role = build_role("owner", ["*"])
        self.assertTrue(role.grants_all)
        for perm in Permission:
            self.assertTrue(role.allows(perm))

    def test_explicit_permissions_only(self):
        role = build_role("friend", ["STATUS", "LOGS_VIEW"])
        self.assertFalse(role.grants_all)
        self.assertTrue(role.allows(Permission.STATUS))
        self.assertFalse(role.allows(Permission.RESTART))

    def test_empty_role_denies_all(self):
        role = build_role("guest", [])
        for perm in Permission:
            self.assertFalse(role.allows(perm))

    def test_unknown_permission_raises_config_error(self):
        with self.assertRaises(ConfigError):
            build_role("bad", ["STATUS", "NOPE"])

    def test_roles_spec_must_be_dict(self):
        with self.assertRaises(ConfigError):
            build_roles(["owner"])  # type: ignore[arg-type]


class TestUserBuilding(unittest.TestCase):
    def setUp(self):
        self.roles = build_roles(ROLE_SPEC)

    def test_users_mapped_to_roles(self):
        users = build_users(USER_SPEC, self.roles)
        self.assertEqual(users["jeremy"].role.name, "owner")
        self.assertEqual(users["paul"].role.name, "admin")

    def test_missing_role_field_raises(self):
        with self.assertRaises(ConfigError):
            build_users({"x": {}}, self.roles)

    def test_unknown_role_raises(self):
        with self.assertRaises(ConfigError):
            build_users({"x": {"role": "ghost"}}, self.roles)


class TestPermissionMatrix(unittest.TestCase):
    """Vérifie chaque case de la matrice attendue (deny-by-default inclus)."""

    def setUp(self):
        self.roles = build_roles(ROLE_SPEC)
        self.users = build_users(USER_SPEC, self.roles)

    def test_full_matrix(self):
        by_role = {"owner": "jeremy", "admin": "paul", "friend": "sam", "guest": "vic"}
        for role_name, username in by_role.items():
            user = self.users[username]
            for perm, allowed in EXPECTED[role_name].items():
                with self.subTest(role=role_name, permission=perm.value):
                    self.assertEqual(user.can(perm), allowed)


if __name__ == "__main__":
    unittest.main()
