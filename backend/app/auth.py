"""Voliteľné prihlasovanie a prístupové práva k dokumentom (RAG_AUTH_ENABLED).

- Používatelia (admin / member) so skupinami; heslá cez scrypt (stdlib).
- Session = náhodný token v HttpOnly cookie, v DB len jeho SHA-256.
- API tokeny (Bearer) pre MCP klientov a skripty.
- Dokument má zoznam skupín, ktoré ho vidia (["*"] = všetci).

Vynucovanie: middleware nastaví `access scope` (contextvar) pre celý request — retrieval,
graf, záznamy, témy aj MCP si z neho prečítajú povolené dokumenty a filtrujú PRIAMO v
dotaze (LanceDB where / SQL), nie až výsledky. Bez prihlasovania je scope neobmedzený.
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
        return self.unrestricted or EVERYONE in access_groups or bool(
            self.groups & set(access_groups)
        )


LOCAL_ADMIN = AccessScope(user_id=None, email=None, role="admin")
# neprihlásený request na verejnú cestu pri zapnutom prihlasovaní — nevidí nič
NO_ACCESS = AccessScope(user_id="", email=None, role="member")
_scope: ContextVar[AccessScope] = ContextVar("rag_access_scope", default=LOCAL_ADMIN)


def current_scope() -> AccessScope:
    return _scope.get()


def set_scope(scope: AccessScope) -> object:  # noqa: D103
    return _scope.set(scope)


def reset_scope(token: object) -> None:
    _scope.reset(token)  # type: ignore[arg-type]


def allowed_doc_ids() -> set[str] | None:
    """None = bez obmedzenia; inak množina dokumentov, ktoré smie aktuálny používateľ vidieť."""
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


# --- heslá a tokeny -------------------------------------------------------------------


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


def create_api_token(user_id: str, name: str) -> str:
    token = "rag_" + secrets.token_urlsafe(32)
    db.insert_api_token(_token_hash(token), user_id, name)
    return token


def scope_for_user(user: dict[str, Any]) -> AccessScope:
    return AccessScope(
        user_id=user["id"], email=user["email"], role=user["role"],
        groups=frozenset(json.loads(user["groups_json"] or "[]")),
    )


def resolve_token(session_token: str | None, bearer: str | None) -> AccessScope | None:
    if bearer:
        user = db.user_for_api_token(_token_hash(bearer))
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
    db.insert_user(user_id, email.strip().casefold(), name, hash_password(password), role,
                   json.dumps(sorted(set(groups or []))))
    user = db.get_user(user_id)
    assert user is not None
    return user


def set_password(user_id: str, password: str) -> None:
    """Zmena hesla: nový hash + všetky existujúce session a API tokeny používateľa zrušiť."""
    db.update_user_password(user_id, hash_password(password))


def ensure_bootstrap_admin() -> None:
    """Prvý admin z RAG_ADMIN_EMAIL / RAG_ADMIN_PASSWORD, ak ešte žiadny admin neexistuje.

    Kontroluje sa admin, nie hocijaký používateľ — pracovníci dochádzky (PIN) sú tiež
    používatelia a majiteľ, ktorý zapne prihlasovanie až po ich pridaní, by sa zamkol von."""
    settings = get_settings()
    if not (settings.admin_email and settings.admin_password):
        return
    if any(u["role"] == "admin" for u in db.list_users()):
        return
    if db.get_user_by_email(settings.admin_email):
        return  # e-mail už má iný (neadmin) účet — nič neprepisujeme
    create_user(settings.admin_email, "Admin", settings.admin_password, role="admin")


# pre neexistujúci e-mail sa overí heslo proti tomuto hashu — login trvá rovnako dlho
# a podľa času odpovede sa nedá zistiť, ktoré účty existujú
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
