"""Optional login and per-document access rights (RAG_AUTH_ENABLED).

- Users (admin / member) with groups; passwords hashed with scrypt (stdlib).
- Session = random token in an HttpOnly cookie; only its SHA-256 is stored in the DB.
- API tokens (Bearer) for MCP clients and scripts.
- A document has a list of groups that can see it (["*"] = everyone).

Enforcement: middleware sets the `access scope` (contextvar) for the whole request — retrieval,
graph, records, topics and MCP read the allowed documents from it and filter DIRECTLY in
the query (LanceDB where / SQL), not the results afterwards. Without login the scope is
unrestricted.
"""

import hashlib
import hmac
import json
import secrets
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app import db
from app.config import get_settings
from app.security import get_security_settings

SESSION_COOKIE = "rag_session"
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
EVERYONE = "*"


@dataclass(frozen=True)
class AccessScope:
    user_id: str | None
    email: str | None
    role: str  # admin | member
    groups: frozenset[str] = field(default_factory=frozenset)

    @property
    def unrestricted(self) -> bool:
        return self.role == "admin"

    def can_see(self, access_groups: list[str]) -> bool:
        if self.unrestricted:
            return True
        if not self.user_id:
            return False  # anonymous (NO_ACCESS) sees nothing, not even documents for "*"
        return EVERYONE in access_groups or bool(self.groups & set(access_groups))


LOCAL_ADMIN = AccessScope(user_id=None, email=None, role="admin")
# unauthenticated request to a public path with login on — sees nothing
NO_ACCESS = AccessScope(user_id="", email=None, role="member")
_scope: ContextVar[AccessScope | None] = ContextVar("rag_access_scope", default=None)


def current_scope() -> AccessScope:
    scope = _scope.get()
    if scope is not None:
        return scope
    # Fail closed: without an explicit request scope, full access only when login is off.
    return NO_ACCESS if get_settings().auth_enabled else LOCAL_ADMIN


def set_scope(scope: AccessScope) -> object:  # noqa: D103
    return _scope.set(scope)


def reset_scope(token: object) -> None:
    _scope.reset(token)  # type: ignore[arg-type]


def allowed_doc_ids() -> set[str] | None:
    """None = unrestricted; otherwise the set of documents the current user may see."""
    scope = current_scope()
    if scope.unrestricted:
        return None
    return {d["id"] for d in db.list_documents() if scope.can_see(document_access(d))}


def document_access(doc: dict[str, Any]) -> list[str]:
    raw = doc.get("access_json")
    return json.loads(raw) if raw else [EVERYONE]


def can_access_document(doc_id: str) -> bool:
    allowed = allowed_doc_ids()
    return allowed is None or doc_id in allowed


# --- passwords and tokens -------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, digest_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=get_settings().session_ttl_hours)
    db.insert_session(_token_hash(token), user_id, expires.isoformat())
    return token


def create_api_token(user_id: str, name: str) -> tuple[str, dict[str, Any]]:
    """→ (plain token shown once, stored row without the hash). Expires after
    RAG_API_TOKEN_TTL_DAYS."""
    token = "rag_" + secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=get_security_settings().api_token_ttl_days)
    token_id = uuid.uuid4().hex
    db.insert_api_token(_token_hash(token), user_id, name, token_id, expires.isoformat())
    row = db.get_api_token(token_id)
    assert row is not None
    return token, row


def scope_for_user(user: dict[str, Any]) -> AccessScope:
    return AccessScope(
        user_id=user["id"],
        email=user["email"],
        role=user["role"],
        groups=frozenset(json.loads(user["groups_json"] or "[]")),
    )


def resolve_token(session_token: str | None, bearer: str | None) -> AccessScope | None:
    if bearer:
        user = db.user_for_api_token(_token_hash(bearer), datetime.now(UTC).isoformat())
        return scope_for_user(user) if user else None
    if session_token:
        user = db.user_for_session(_token_hash(session_token), datetime.now(UTC).isoformat())
        return scope_for_user(user) if user else None
    return None


def logout(session_token: str) -> None:
    db.delete_session(_token_hash(session_token))


def create_user(
    email: str, name: str, password: str, role: str = "member", groups: list[str] | None = None
) -> dict[str, Any]:
    user_id = uuid.uuid4().hex
    db.insert_user(
        user_id,
        email.strip().casefold(),
        name,
        hash_password(password),
        role,
        json.dumps(sorted(set(groups or []))),
    )
    user = db.get_user(user_id)
    assert user is not None
    return user


def set_password(user_id: str, password: str) -> None:
    """Password change: store the new hash and revoke all of the user's sessions and API tokens."""
    db.update_user_password(user_id, hash_password(password))


def ensure_bootstrap_admin() -> None:
    """First admin from RAG_ADMIN_EMAIL / RAG_ADMIN_PASSWORD, if no admin exists yet.

    It checks for an admin, not any user — attendance workers (PIN) are users too,
    and an owner who turns login on only after adding them would be locked out."""
    settings = get_settings()
    if not (settings.admin_email and settings.admin_password):
        return
    if any(u["role"] == "admin" for u in db.list_users()):
        return
    if db.get_user_by_email(settings.admin_email):
        return  # the e-mail already belongs to another (non-admin) account — we overwrite nothing
    create_user(settings.admin_email, "Admin", settings.admin_password, role="admin")


# for a non-existent e-mail the password is checked against this hash — login takes equally long
# so the response time does not reveal which accounts exist
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
