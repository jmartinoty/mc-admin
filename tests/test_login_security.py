"""Tests du limiteur de connexion et de la résolution d'IP client."""
from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from api.login_security import (
    ClientIpResolver,
    LoginAttemptLimiter,
    parse_trusted_proxy_cidrs,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestClientIpResolver(unittest.TestCase):
    def test_untrusted_peer_cannot_spoof_forwarded_header(self):
        resolver = ClientIpResolver.from_config("10.0.0.0/8")
        self.assertEqual(
            resolver.resolve("192.0.2.10", "198.51.100.25"),
            "192.0.2.10",
        )

    def test_trusted_proxy_exposes_original_client(self):
        resolver = ClientIpResolver.from_config("10.0.0.0/8")
        self.assertEqual(
            resolver.resolve("10.0.0.2", "198.51.100.25"),
            "198.51.100.25",
        )

    def test_proxy_chain_is_walked_from_right_to_left(self):
        resolver = ClientIpResolver.from_config(
            "10.0.0.0/8, 192.168.0.0/16"
        )
        self.assertEqual(
            resolver.resolve(
                "10.0.0.2",
                "198.51.100.25, 192.168.4.8",
            ),
            "198.51.100.25",
        )

    def test_untrusted_intermediate_proxy_stops_the_chain(self):
        resolver = ClientIpResolver.from_config("10.0.0.0/8")
        self.assertEqual(
            resolver.resolve(
                "10.0.0.2",
                "198.51.100.25, 203.0.113.9",
            ),
            "203.0.113.9",
        )

    def test_invalid_forwarded_header_falls_back_to_direct_peer(self):
        resolver = ClientIpResolver.from_config("10.0.0.0/8")
        self.assertEqual(
            resolver.resolve("10.0.0.2", "not-an-ip"),
            "10.0.0.2",
        )

    def test_ipv6_addresses_are_supported(self):
        resolver = ClientIpResolver.from_config("fd00::/8")
        self.assertEqual(
            resolver.resolve("fd00::2", "2001:db8::4"),
            "2001:db8::4",
        )

    def test_invalid_trusted_proxy_config_fails_fast(self):
        with self.assertRaisesRegex(ValueError, "CIDR invalide"):
            parse_trusted_proxy_cidrs("10.0.0.0/8, definitely-not-a-cidr")


class TestLoginAttemptLimiter(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.limiter = LoginAttemptLimiter(
            clock=self.clock,
            cleanup_interval_seconds=0,
        )

    def fail(self, ip: str, username: str) -> None:
        attempt = self.limiter.begin(ip, username)
        self.assertIsNotNone(attempt)
        self.limiter.failure(attempt)

    def test_pair_lock_does_not_block_same_username_from_another_ip(self):
        for _ in range(5):
            self.fail("192.0.2.10", "jeremy")
        self.assertIsNone(self.limiter.begin("192.0.2.10", "jeremy"))
        self.assertIsNotNone(self.limiter.begin("192.0.2.11", "jeremy"))

    def test_username_is_normalized_for_rate_limiting_only(self):
        for username in ("Jeremy", "JEREMY", "jeremy", "JeReMy", "jeremy"):
            self.fail("192.0.2.10", username)
        self.assertIsNone(self.limiter.begin("192.0.2.10", "JEREMY"))

    def test_oversized_username_cannot_create_oversized_keys(self):
        prefix = "x" * 128
        for suffix in ("a", "b", "c", "d", "e"):
            self.fail("192.0.2.10", prefix + suffix * 10_000)
        self.assertIsNone(self.limiter.begin("192.0.2.10", prefix + "other"))

    def test_global_ip_limit_catches_username_rotation(self):
        for index in range(20):
            self.fail("192.0.2.10", f"user-{index}")
        self.assertIsNone(self.limiter.begin("192.0.2.10", "new-user"))
        self.assertIsNotNone(self.limiter.begin("192.0.2.11", "new-user"))

    def test_success_clears_pair_failures_but_not_other_ip_failures(self):
        for _ in range(4):
            self.fail("192.0.2.10", "jeremy")
        success = self.limiter.begin("192.0.2.10", "jeremy")
        self.assertIsNotNone(success)
        self.limiter.success(success)

        for _ in range(4):
            self.fail("192.0.2.10", "jeremy")
        next_attempt = self.limiter.begin("192.0.2.10", "jeremy")
        self.assertIsNotNone(next_attempt)
        self.limiter.cancel(next_attempt)

        # Les échecs globaux des autres pseudos restent comptabilisés.
        for index in range(12):
            self.fail("192.0.2.10", f"rotated-{index}")
        self.assertIsNone(self.limiter.begin("192.0.2.10", "last-user"))

    def test_lock_expires_and_starts_with_a_clean_bucket(self):
        for _ in range(5):
            self.fail("192.0.2.10", "jeremy")
        self.assertIsNone(self.limiter.begin("192.0.2.10", "jeremy"))
        self.clock.advance(301)
        self.assertIsNotNone(self.limiter.begin("192.0.2.10", "jeremy"))

    def test_cancel_releases_in_flight_capacity(self):
        attempts = [
            self.limiter.begin("192.0.2.10", "jeremy")
            for _ in range(5)
        ]
        self.assertTrue(all(attempt is not None for attempt in attempts))
        self.assertIsNone(self.limiter.begin("192.0.2.10", "jeremy"))
        self.limiter.cancel(attempts[0])
        self.assertIsNotNone(self.limiter.begin("192.0.2.10", "jeremy"))

    def test_stale_reservations_are_cleaned_up(self):
        attempt = self.limiter.begin("192.0.2.10", "jeremy")
        self.assertIsNotNone(attempt)
        self.assertEqual(self.limiter.active_attempt_count, 1)
        self.clock.advance(121)
        replacement = self.limiter.begin("192.0.2.11", "paul")
        self.assertIsNotNone(replacement)
        self.assertEqual(self.limiter.active_attempt_count, 1)

    def test_memory_is_bounded(self):
        limiter = LoginAttemptLimiter(
            pair_max_failures=50,
            ip_max_failures=50,
            max_pair_keys=3,
            max_ip_keys=2,
            clock=self.clock,
            cleanup_interval_seconds=0,
        )
        for index in range(12):
            attempt = limiter.begin(f"192.0.2.{index + 1}", f"user-{index}")
            self.assertIsNotNone(attempt)
            limiter.failure(attempt)
            self.clock.advance(1)
        self.assertLessEqual(limiter.tracked_pair_count, 3)
        self.assertLessEqual(limiter.tracked_ip_count, 2)

    def test_concurrent_burst_cannot_exceed_pair_capacity(self):
        def reserve(_index):
            return self.limiter.begin("192.0.2.10", "jeremy")

        with ThreadPoolExecutor(max_workers=20) as executor:
            attempts = list(executor.map(reserve, range(40)))
        accepted = [attempt for attempt in attempts if attempt is not None]
        self.assertEqual(len(accepted), 5)
        self.assertEqual(self.limiter.active_attempt_count, 5)
        for attempt in accepted:
            self.limiter.failure(attempt)
        self.assertIsNone(self.limiter.begin("192.0.2.10", "jeremy"))


if __name__ == "__main__":
    unittest.main()
