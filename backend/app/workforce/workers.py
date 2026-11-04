"""Workers: creation (user + PIN), PIN verification with lockout, session on mobile."""

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app import db
from app.auth import create_user, hash_password, verify_password
from app.workforce import store
from app.workforce.config import get_workforce_settings

PIN_RE = re.compile(r"^\d{4,6}$")
WORKER_GROUP = "pracovnici"


class PinError(ValueError):
    pass


def validate_pin(pin: str) -> str:
    if not PIN_RE.match(pin):
        raise PinError("PIN má 4–6 číslic")
    return pin


def worker_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "professions": json.loads(row["professions_json"] or "[]"),
        "active": bool(row["active"]),
        "locked": _locked(row),
    }


def _locked(row: dict[str, Any]) -> bool:
    until = row.get("locked_until")
    return until is not None and datetime.fromisoformat(str(until)) > datetime.now(UTC)


def create_worker(name: str, pin: str, professions: list[str]) -> dict[str, Any]:
    """Worker = a user with the member role (no usable password) + PIN."""
    validate_pin(pin)
    email = f"w-{secrets.token_hex(6)}@pracovnik.local"
    user = create_user(email, name, secrets.token_urlsafe(32), role="member", groups=[WORKER_GROUP])
    store.insert_worker(user["id"], hash_password(pin), professions)
    row = store.get_worker(user["id"])
    assert row is not None
    return worker_out(row)


@dataclass
class PinResult:
    ok: bool
    locked: bool = False
    attempts_left: int | None = None


def check_pin(user_id: str, pin: str) -> PinResult:
    settings = get_workforce_settings()
    row = store.get_worker(user_id)
    if row is None or not row["active"]:
        return PinResult(ok=False)
    now = datetime.now(UTC)
    # the attempt is counted (and the lock set) atomically before the slow hash check —
    # parallel requests cannot squeeze in more guesses than pin_max_attempts
    attempt = store.claim_pin_attempt(
        user_id,
        now_iso=now.isoformat(),
        max_attempts=settings.pin_max_attempts,
        lock_until_iso=(now + timedelta(minutes=settings.pin_lock_minutes)).isoformat(),
    )
    if attempt is None:
        return PinResult(ok=False, locked=True)
    if verify_password(pin, row["pin_hash"]):
        store.reset_pin_failures(user_id)
        return PinResult(ok=True)
    if attempt >= settings.pin_max_attempts:
        return PinResult(ok=False, locked=True)
    return PinResult(ok=False, attempts_left=settings.pin_max_attempts - attempt)


def create_worker_session(user_id: str) -> tuple[str, int]:
    """→ (token, max_age seconds). Longer validity than an admin session — mobile on a site.

    The token hash must match app.auth (SHA-256 hex) — verified by test_workforce_auth."""
    days = get_workforce_settings().worker_session_days
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=days)
    db.insert_session(hashlib.sha256(token.encode()).hexdigest(), user_id, expires.isoformat())
    return token, days * 86400
