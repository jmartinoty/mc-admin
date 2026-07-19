"""Tests de la décision d'authentification (hasher factice — pas d'argon2)."""
from __future__ import annotations

import unittest

from api.auth import authenticate
from domain.model import Role, User


class FakeHasher:
    """Vérifie selon une correspondance hash->mot de passe attendu."""

    def __init__(self, expected: dict[str, str]):
        self._expected = expected  # {stored_hash: mot_de_passe_valide}
        self.calls: list[tuple[str, str]] = []

    def verify(self, stored_hash: str, password: str) -> bool:
        self.calls.append((stored_hash, password))
        return self._expected.get(stored_hash) == password


def _user(name):
    return User(username=name, role=Role(name="owner", permissions=frozenset(), grants_all=True))


class TestAuthenticate(unittest.TestCase):
    def setUp(self):
        self.users = {"jeremy": _user("jeremy")}
        self.credentials = {"jeremy": "HASH_J"}
        self.hasher = FakeHasher({"HASH_J": "s3cret"})

    def test_valid_credentials_returns_user(self):
        user = authenticate(self.credentials, self.users, "jeremy", "s3cret", self.hasher)
        self.assertIsNotNone(user)
        self.assertEqual(user.username, "jeremy")

    def test_wrong_password_returns_none(self):
        self.assertIsNone(authenticate(self.credentials, self.users, "jeremy", "nope", self.hasher))

    def test_unknown_user_returns_none(self):
        self.assertIsNone(authenticate(self.credentials, self.users, "ghost", "s3cret", self.hasher))

    def test_unknown_user_performs_dummy_verification_when_configured(self):
        self.assertIsNone(
            authenticate(
                self.credentials,
                self.users,
                "ghost",
                "s3cret",
                self.hasher,
                fallback_hash="DUMMY_HASH",
            )
        )
        self.assertEqual(self.hasher.calls, [("DUMMY_HASH", "s3cret")])

    def test_user_without_password_hash_returns_none(self):
        users = dict(self.users, paul=_user("paul"))  # paul n'a pas de credential
        self.assertIsNone(authenticate(self.credentials, users, "paul", "s3cret", self.hasher))


if __name__ == "__main__":
    unittest.main()
