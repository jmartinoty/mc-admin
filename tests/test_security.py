"""Tests des primitives CSRF (stdlib, aucune dépendance)."""
from __future__ import annotations

import unittest

from api.security import csrf_ok, new_csrf_token


class TestCsrf(unittest.TestCase):
    def test_new_token_is_nonempty_and_unique(self):
        a, b = new_csrf_token(), new_csrf_token()
        self.assertTrue(a and b)
        self.assertNotEqual(a, b)

    def test_matching_tokens_ok(self):
        token = new_csrf_token()
        self.assertTrue(csrf_ok(token, token))

    def test_mismatch_rejected(self):
        self.assertFalse(csrf_ok(new_csrf_token(), new_csrf_token()))

    def test_empty_or_none_rejected(self):
        token = new_csrf_token()
        self.assertFalse(csrf_ok(None, token))
        self.assertFalse(csrf_ok(token, None))
        self.assertFalse(csrf_ok("", token))
        self.assertFalse(csrf_ok(token, ""))


if __name__ == "__main__":
    unittest.main()
