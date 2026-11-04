"""Security settings and HTTP helpers shared by the auth middleware, worker guard and login.

Kept apart from `app.config` so the hardening knobs (API token lifetime, login rate limit,
trusted proxy) live in one place; same `RAG_` prefix and `.env` file as the main settings.
"""

import threading
import time
from collections import deque
from collections.abc import Callable

from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import Request
from starlette.types import Scope


class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RAG_", extra="ignore")

    api_token_ttl_days: int = 90  # API tokens (Bearer) expire after this many days
    login_max_failures: int = 10  # failed logins (password or PIN) per client IP ...
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


class LoginRateLimiter:
    """In-memory sliding window of failed logins per client key (IP).

    Complements the per-worker PIN lock: that one stops guessing one PIN, this one stops
    one client from cycling through many accounts. State lives in the process only."""

    MAX_KEYS = 10_000  # bound memory when many distinct IPs fail

    def __init__(
        self,
        max_failures: int,
        window_s: float,
        *,
        trusted_proxy: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
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

    def blocked(self, key: str) -> bool:
        if self.max_failures <= 0:
            return False
        with self._lock:
            hits = self._recent(key, self._clock())
            return hits is not None and len(hits) >= self.max_failures

    def record_failure(self, key: str) -> None:
        if self.max_failures <= 0:
            return
        with self._lock:
            now = self._clock()
            if len(self._failures) >= self.MAX_KEYS:
                for k in list(self._failures):
                    self._recent(k, now)
            hits = self._recent(key, now)
            if hits is None:
                hits = self._failures[key] = deque()
            hits.append(now)

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
                trusted_proxy=s.trusted_proxy,
            )
        return _limiter


def reset_login_limiter() -> None:
    """Fresh limiter with current settings — called on app startup."""
    global _limiter
    with _limiter_lock:
        _limiter = None


LOGIN_RATE_LIMITED = "príliš veľa neúspešných prihlásení — skús to o chvíľu"
