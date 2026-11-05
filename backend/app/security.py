"""Security settings and HTTP helpers shared by the auth middleware, worker guard and login.

Kept apart from `app.config` so the hardening knobs (API token lifetime, login rate limit,
trusted proxy) live in one place; same `RAG_` prefix and `.env` file as the main settings.
"""

import heapq
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import Request
from starlette.types import Scope


class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RAG_", extra="ignore")

    api_token_ttl_days: int = 90  # API tokens (Bearer) expire after this many days
    # failed logins (password or PIN) per client IP AND account (e-mail / worker) ...
    login_max_failures: int = 10
    # ... and per client IP over all accounts (slows password spraying) ...
    login_ip_max_failures: int = 100
    login_window_minutes: int = 15  # ... within this window → 429
    # honour X-Forwarded-For only behind a reverse proxy you control (docker-compose nginx)
    trusted_proxy: bool = False


def get_security_settings() -> SecuritySettings:
    # not cached: read rarely (startup, token creation) and tests change the env per test
    return SecuritySettings()


def request_path(scope: Scope) -> str:
    """Request path without the ASGI root_path (--root-path / reverse proxy prefix)."""
    path: str = scope.get("path", "")
    root: str = scope.get("root_path", "")
    if root and path.startswith(root):
        path = path[len(root) :]
    return path


# --- failed login rate limit ------------------------------------------------------------


@dataclass(frozen=True)
class LoginAttempt:
    """One claimed login attempt — pass it to `succeeded` when the credentials were right."""

    ip_key: str
    account_key: str
    at: float


class LoginRateLimiter:
    """In-memory sliding window of login attempts, in two buckets per attempt:

    - per (client IP, account): `max_failures` — stops guessing one account's password/PIN;
      keyed on the account too, so behind a proxy that makes every client look like
      127.0.0.1 one attacker cannot lock everyone (the admin included) out;
    - per client IP: the much higher `ip_max_failures` — slows spraying many accounts.

    An attempt is counted BEFORE the credentials are verified (`claim`) and forgiven on
    success (`succeeded`), so a parallel burst cannot verify more guesses than the limit —
    the same semantics as the per-worker PIN lock. State lives in the process only."""

    MAX_KEYS = 10_000  # bound memory when many distinct clients fail

    def __init__(
        self,
        max_failures: int,
        window_s: float,
        *,
        ip_max_failures: int = 0,
        trusted_proxy: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.ip_max_failures = ip_max_failures
        self.window_s = window_s
        self.trusted_proxy = trusted_proxy
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float] | None:
        hits = self._failures.get(key)
        if hits is None:
            return None
        while hits and now - hits[0] >= self.window_s:
            hits.popleft()
        if not hits:
            del self._failures[key]
            return None
        return hits

    def _full(self, key: str, limit: int, now: float) -> bool:
        if limit <= 0:
            return False
        hits = self._recent(key, now)
        return hits is not None and len(hits) >= limit

    def _make_room(self, now: float) -> None:
        """At the key cap drop expired keys, then the least recently hit ones."""
        if len(self._failures) < self.MAX_KEYS:
            return
        for k in list(self._failures):
            self._recent(k, now)
        excess = len(self._failures) - self.MAX_KEYS + 1
        if excess > 0:
            oldest = heapq.nsmallest(excess, self._failures, key=lambda k: self._failures[k][-1])
            for k in oldest:
                del self._failures[k]

    def _add(self, key: str, now: float) -> None:
        hits = self._recent(key, now)
        if hits is None:
            self._make_room(now)
            hits = self._failures[key] = deque()
        hits.append(now)

    def claim(self, ip: str, account: str) -> LoginAttempt | None:
        """Count one attempt for `account` from `ip` → None (nothing counted) while either
        bucket is full."""
        ip_key, account_key = f"ip:{ip}", f"acct:{ip}\x00{account}"
        with self._lock:
            now = self._clock()
            if self._full(account_key, self.max_failures, now) or self._full(
                ip_key, self.ip_max_failures, now
            ):
                return None
            if self.max_failures > 0:
                self._add(account_key, now)
            if self.ip_max_failures > 0:
                self._add(ip_key, now)
            return LoginAttempt(ip_key, account_key, now)

    def succeeded(self, attempt: LoginAttempt) -> None:
        """Right credentials: reset the account's failures and take this attempt back from
        the IP bucket (other accounts' failures from the same IP stay counted)."""
        with self._lock:
            self._failures.pop(attempt.account_key, None)
            hits = self._failures.get(attempt.ip_key)
            if hits is not None and attempt.at in hits:
                hits.remove(attempt.at)
                if not hits:
                    del self._failures[attempt.ip_key]

    def client_key(self, request: Request) -> str:
        if self.trusted_proxy:
            forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
            forwarded = [p for p in forwarded if p]
            if forwarded:
                # right-most entry = the address our own proxy saw; earlier ones are spoofable
                return forwarded[-1]
        return request.client.host if request.client else "unknown"


_limiter: LoginRateLimiter | None = None
_limiter_lock = threading.Lock()


def get_login_limiter() -> LoginRateLimiter:
    global _limiter
    with _limiter_lock:
        if _limiter is None:
            s = get_security_settings()
            _limiter = LoginRateLimiter(
                s.login_max_failures,
                s.login_window_minutes * 60.0,
                ip_max_failures=s.login_ip_max_failures,
                trusted_proxy=s.trusted_proxy,
            )
        return _limiter


def reset_login_limiter() -> None:
    """Fresh limiter with current settings — called on app startup."""
    global _limiter
    with _limiter_lock:
        _limiter = None


LOGIN_RATE_LIMITED = "príliš veľa neúspešných prihlásení — skús to o chvíľu"
