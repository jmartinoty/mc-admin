"""Identification du client et limitation des tentatives de connexion."""
from __future__ import annotations

import ipaddress
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from threading import Lock

IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def parse_trusted_proxy_cidrs(raw: str) -> tuple[IpNetwork, ...]:
    """Parse une liste de CIDR séparés par des virgules, en échouant tôt."""
    networks: list[IpNetwork] = []
    for value in raw.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise ValueError(
                f"LOGIN_TRUSTED_PROXY_CIDRS contient un CIDR invalide : {value!r}"
            ) from exc
    return tuple(networks)


class ClientIpResolver:
    """Résout l'IP réelle sans faire confiance aux en-têtes d'un client direct."""

    _MAX_FORWARDED_HOPS = 16
    _MAX_HEADER_LENGTH = 2048

    def __init__(self, trusted_proxies: Iterable[IpNetwork] = ()) -> None:
        self._trusted_proxies = tuple(trusted_proxies)

    @classmethod
    def from_config(cls, raw: str) -> "ClientIpResolver":
        return cls(parse_trusted_proxy_cidrs(raw))

    def resolve(self, peer_host: str | None, x_forwarded_for: str | None) -> str:
        direct_text = (peer_host or "").strip() or "unknown"
        direct_ip = self._parse_ip(direct_text)
        if direct_ip is None:
            return direct_text.casefold()
        if not self._is_trusted(direct_ip) or not x_forwarded_for:
            return str(direct_ip)
        if len(x_forwarded_for) > self._MAX_HEADER_LENGTH:
            return str(direct_ip)

        parts = [part.strip() for part in x_forwarded_for.split(",")]
        if not parts or len(parts) > self._MAX_FORWARDED_HOPS:
            return str(direct_ip)
        forwarded = [self._parse_ip(part) for part in parts]
        if any(address is None for address in forwarded):
            return str(direct_ip)

        # On remonte depuis le pair directement connecté. Le premier saut qui
        # n'est pas explicitement approuvé est le client observable.
        chain = [address for address in forwarded if address is not None]
        chain.append(direct_ip)
        for address in reversed(chain):
            if not self._is_trusted(address):
                return str(address)
        return str(chain[0])

    def _is_trusted(self, address: IpAddress) -> bool:
        return any(
            address.version == network.version and address in network
            for network in self._trusted_proxies
        )

    @staticmethod
    def _parse_ip(value: str) -> IpAddress | None:
        try:
            return ipaddress.ip_address(value)
        except ValueError:
            return None


@dataclass(frozen=True)
class LoginAttempt:
    """Jeton opaque représentant une vérification de mot de passe en cours."""

    token: int


@dataclass
class _Bucket:
    failures: deque[float] = field(default_factory=deque)
    blocked_until: float = 0.0
    last_seen: float = 0.0
    in_flight: int = 0


@dataclass(frozen=True)
class _Lease:
    pair_key: tuple[str, str]
    ip_key: str
    started_at: float


class LoginAttemptLimiter:
    """Limiteur en mémoire par (IP, pseudo) et par IP globale.

    Un jeton est réservé avant la vérification du hash. Cela borne aussi les
    vérifications Argon2 simultanées et évite qu'une rafale concurrente ne
    franchisse silencieusement les seuils.
    """

    _MAX_USERNAME_KEY_LENGTH = 128

    def __init__(
        self,
        *,
        pair_max_failures: int = 5,
        ip_max_failures: int = 20,
        window_seconds: float = 600.0,
        pair_lock_seconds: float = 300.0,
        ip_lock_seconds: float = 600.0,
        max_pair_keys: int = 4096,
        max_ip_keys: int = 1024,
        lease_timeout_seconds: float = 120.0,
        cleanup_interval_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        numeric_values = {
            "pair_max_failures": pair_max_failures,
            "ip_max_failures": ip_max_failures,
            "window_seconds": window_seconds,
            "pair_lock_seconds": pair_lock_seconds,
            "ip_lock_seconds": ip_lock_seconds,
            "max_pair_keys": max_pair_keys,
            "max_ip_keys": max_ip_keys,
            "lease_timeout_seconds": lease_timeout_seconds,
        }
        invalid = [name for name, value in numeric_values.items() if value <= 0]
        if invalid or cleanup_interval_seconds < 0:
            names = ", ".join(invalid or ["cleanup_interval_seconds"])
            raise ValueError(f"paramètre(s) du limiteur invalide(s) : {names}")

        self._pair_max_failures = pair_max_failures
        self._ip_max_failures = ip_max_failures
        self._window_seconds = window_seconds
        self._pair_lock_seconds = pair_lock_seconds
        self._ip_lock_seconds = ip_lock_seconds
        self._max_pair_keys = max_pair_keys
        self._max_ip_keys = max_ip_keys
        self._lease_timeout_seconds = lease_timeout_seconds
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._clock = clock
        self._lock = Lock()
        self._pair_buckets: dict[tuple[str, str], _Bucket] = {}
        self._ip_buckets: dict[str, _Bucket] = {}
        self._leases: dict[int, _Lease] = {}
        self._next_token = 1
        self._last_cleanup = 0.0

    def begin(self, client_ip: str, username: str) -> LoginAttempt | None:
        """Réserve une tentative, ou renvoie ``None`` si elle est limitée."""
        now = self._clock()
        ip_key = self._normalize_ip(client_ip)
        pair_key = (ip_key, self._normalize_username(username))
        with self._lock:
            self._cleanup_if_due(now)
            pair_bucket = self._get_or_create(
                self._pair_buckets, pair_key, self._max_pair_keys, now
            )
            ip_bucket = self._get_or_create(
                self._ip_buckets, ip_key, self._max_ip_keys, now
            )
            if pair_bucket is None or ip_bucket is None:
                self._drop_empty_bucket(self._pair_buckets, pair_key)
                self._drop_empty_bucket(self._ip_buckets, ip_key)
                return None

            self._prepare_bucket(pair_bucket, now)
            self._prepare_bucket(ip_bucket, now)
            pair_bucket.last_seen = now
            ip_bucket.last_seen = now
            if pair_bucket.blocked_until > now or ip_bucket.blocked_until > now:
                self._drop_empty_bucket(self._pair_buckets, pair_key)
                self._drop_empty_bucket(self._ip_buckets, ip_key)
                return None
            if (
                len(pair_bucket.failures) + pair_bucket.in_flight
                >= self._pair_max_failures
                or len(ip_bucket.failures) + ip_bucket.in_flight
                >= self._ip_max_failures
            ):
                self._drop_empty_bucket(self._pair_buckets, pair_key)
                self._drop_empty_bucket(self._ip_buckets, ip_key)
                return None

            token = self._next_token
            self._next_token += 1
            pair_bucket.in_flight += 1
            ip_bucket.in_flight += 1
            self._leases[token] = _Lease(pair_key, ip_key, now)
            return LoginAttempt(token)

    def failure(self, attempt: LoginAttempt) -> None:
        """Termine une tentative invalide et applique les verrous atteints."""
        now = self._clock()
        with self._lock:
            lease = self._leases.pop(attempt.token, None)
            if lease is None:
                return
            pair_bucket, ip_bucket = self._release_lease(lease, now)
            self._prepare_bucket(pair_bucket, now)
            self._prepare_bucket(ip_bucket, now)
            pair_bucket.failures.append(now)
            ip_bucket.failures.append(now)
            if len(pair_bucket.failures) >= self._pair_max_failures:
                pair_bucket.blocked_until = max(
                    pair_bucket.blocked_until, now + self._pair_lock_seconds
                )
            if len(ip_bucket.failures) >= self._ip_max_failures:
                ip_bucket.blocked_until = max(
                    ip_bucket.blocked_until, now + self._ip_lock_seconds
                )

    def success(self, attempt: LoginAttempt) -> None:
        """Termine une réussite et remet à zéro seulement le couple IP+pseudo."""
        now = self._clock()
        with self._lock:
            lease = self._leases.pop(attempt.token, None)
            if lease is None:
                return
            pair_bucket, ip_bucket = self._release_lease(lease, now)
            self._prepare_bucket(ip_bucket, now)
            pair_bucket.failures.clear()
            pair_bucket.blocked_until = 0.0
            self._drop_empty_bucket(self._pair_buckets, lease.pair_key)
            self._drop_empty_bucket(self._ip_buckets, lease.ip_key)

    def cancel(self, attempt: LoginAttempt) -> None:
        """Libère une réservation si l'authentification lève une exception."""
        now = self._clock()
        with self._lock:
            lease = self._leases.pop(attempt.token, None)
            if lease is None:
                return
            self._release_lease(lease, now)
            self._drop_empty_bucket(self._pair_buckets, lease.pair_key)
            self._drop_empty_bucket(self._ip_buckets, lease.ip_key)

    @property
    def tracked_pair_count(self) -> int:
        with self._lock:
            return len(self._pair_buckets)

    @property
    def tracked_ip_count(self) -> int:
        with self._lock:
            return len(self._ip_buckets)

    @property
    def active_attempt_count(self) -> int:
        with self._lock:
            return len(self._leases)

    def _release_lease(self, lease: _Lease, now: float) -> tuple[_Bucket, _Bucket]:
        pair_bucket = self._pair_buckets[lease.pair_key]
        ip_bucket = self._ip_buckets[lease.ip_key]
        pair_bucket.in_flight = max(0, pair_bucket.in_flight - 1)
        ip_bucket.in_flight = max(0, ip_bucket.in_flight - 1)
        pair_bucket.last_seen = now
        ip_bucket.last_seen = now
        return pair_bucket, ip_bucket

    def _prepare_bucket(self, bucket: _Bucket, now: float) -> None:
        if bucket.blocked_until:
            if bucket.blocked_until > now:
                return
            bucket.blocked_until = 0.0
            bucket.failures.clear()
        cutoff = now - self._window_seconds
        while bucket.failures and bucket.failures[0] <= cutoff:
            bucket.failures.popleft()

    def _cleanup_if_due(self, now: float) -> None:
        if now - self._last_cleanup < self._cleanup_interval_seconds:
            return
        self._last_cleanup = now
        stale_tokens = [
            token
            for token, lease in self._leases.items()
            if now - lease.started_at >= self._lease_timeout_seconds
        ]
        for token in stale_tokens:
            lease = self._leases.pop(token)
            self._release_lease(lease, now)

        for buckets in (self._pair_buckets, self._ip_buckets):
            for key, bucket in list(buckets.items()):
                self._prepare_bucket(bucket, now)
                self._drop_empty_bucket(buckets, key)

    def _get_or_create(self, buckets, key, limit: int, now: float) -> _Bucket | None:
        bucket = buckets.get(key)
        if bucket is not None:
            return bucket
        if len(buckets) >= limit:
            candidates = [
                (bool(candidate.failures), candidate.last_seen, candidate_key)
                for candidate_key, candidate in buckets.items()
                if candidate.in_flight == 0 and candidate.blocked_until <= now
            ]
            if not candidates:
                return None
            _, _, oldest_key = min(candidates)
            buckets.pop(oldest_key, None)
        bucket = _Bucket(last_seen=now)
        buckets[key] = bucket
        return bucket

    @staticmethod
    def _drop_empty_bucket(buckets, key) -> None:
        bucket = buckets.get(key)
        if (
            bucket is not None
            and not bucket.failures
            and bucket.blocked_until == 0.0
            and bucket.in_flight == 0
        ):
            buckets.pop(key, None)

    @staticmethod
    def _normalize_ip(value: str) -> str:
        value = value.strip() or "unknown"
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            return value.casefold()

    @classmethod
    def _normalize_username(cls, value: str) -> str:
        return value.strip().casefold()[: cls._MAX_USERNAME_KEY_LENGTH]
