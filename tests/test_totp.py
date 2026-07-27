"""Tests du TOTP stdlib (RFC 6238) et du store des secrets 2FA."""
from __future__ import annotations

import os
import tempfile
import unittest

from api import totp
from adapters.totp_store import JsonTotp


class TestTotpCore(unittest.TestCase):
    def test_generated_secret_roundtrips(self):
        secret = totp.generate_secret()
        code = totp.code_at(secret, 1_000_000_000)
        self.assertIsNotNone(code)
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())
        self.assertTrue(totp.verify(secret, code, at=1_000_000_000))

    def test_rfc6238_known_vector(self):
        # Vecteur RFC 6238 (clé ASCII "12345678901234567890" -> base32), T=59s,
        # HMAC-SHA1 -> TOTP 94287082, soit 6 chiffres = 287082.
        import base64
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(totp.code_at(secret, 59), "287082")
        self.assertTrue(totp.verify(secret, "287082", at=59, window=0))

    def test_wrong_code_rejected(self):
        secret = totp.generate_secret()
        self.assertFalse(totp.verify(secret, "000000", at=1_000_000_000))
        self.assertFalse(totp.verify(secret, "not-a-code", at=1_000_000_000))
        self.assertFalse(totp.verify(secret, "", at=1_000_000_000))

    def test_time_window_tolerance(self):
        secret = totp.generate_secret()
        prev = totp.code_at(secret, 1_000_000_000 - 30)
        # Un code de la fenêtre précédente reste accepté (décalage d'horloge).
        self.assertTrue(totp.verify(secret, prev, at=1_000_000_000, window=1))
        self.assertFalse(totp.verify(secret, prev, at=1_000_000_000, window=0))

    def test_illegible_secret_never_crashes(self):
        self.assertIsNone(totp.code_at("!!!not base32!!!", 0))
        self.assertFalse(totp.verify("!!!", "123456", at=0))

    def test_provisioning_uri_contains_secret_and_issuer(self):
        uri = totp.provisioning_uri("ABCDEF", "jeremy", issuer="mc-admin")
        self.assertTrue(uri.startswith("otpauth://totp/"))
        self.assertIn("secret=ABCDEF", uri)
        self.assertIn("issuer=mc-admin", uri)
        self.assertIn("mc-admin:jeremy", uri)


class TestJsonTotp(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def test_pending_then_confirm(self):
        store = JsonTotp(self.path)
        self.assertFalse(store.is_enabled("jeremy"))
        store.set_pending("jeremy", "ABCDEF")
        self.assertTrue(store.has_pending("jeremy"))
        self.assertFalse(store.is_enabled("jeremy"))  # pas encore confirmé
        self.assertTrue(store.confirm("jeremy"))
        self.assertTrue(store.is_enabled("jeremy"))
        self.assertFalse(store.has_pending("jeremy"))

    def test_confirm_without_secret_is_false(self):
        self.assertFalse(JsonTotp(self.path).confirm("ghost"))

    def test_remove(self):
        store = JsonTotp(self.path)
        store.set_pending("jeremy", "ABCDEF")
        store.confirm("jeremy")
        self.assertTrue(store.remove("jeremy"))
        self.assertFalse(store.is_enabled("jeremy"))
        self.assertFalse(store.remove("jeremy"))

    def test_file_is_0600(self):
        store = JsonTotp(self.path)
        store.set_pending("jeremy", "ABCDEF")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
