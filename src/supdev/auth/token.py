"""Bearer-token authenticator (default for containers). Tokens are shown once, stored only as SHA-256."""
from __future__ import annotations

import hashlib
import secrets as _secrets

from ..core.db import Database, get_database
from ..core.errors import PolicyViolation
from ..core.models import Principal, Role, new_id, now


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(db: Database, tenant_id: str, user_id: str, role: Role, raw: str | None = None) -> tuple[str, str]:
    token = raw or "sdk_" + _secrets.token_urlsafe(32)
    tid = new_id("tok_")
    db.exec("INSERT INTO api_tokens (id, tenant_id, user_id, role, token_sha256, created_at) VALUES (?,?,?,?,?,?)",
            (tid, tenant_id, user_id, role.value, _h(token), now()))
    return tid, token


def list_tokens(db: Database, tenant_id: str) -> list[dict[str, object]]:
    return [dict(r) for r in db.all("SELECT id, user_id, role, created_at, revoked FROM api_tokens "
                                    "WHERE tenant_id=? ORDER BY created_at DESC", (tenant_id,))]


def revoke_token(db: Database, tenant_id: str, token_id: str) -> None:
    db.exec("UPDATE api_tokens SET revoked=1 WHERE tenant_id=? AND id=?", (tenant_id, token_id))


def has_tokens(db: Database, tenant_id: str) -> bool:
    return db.one("SELECT 1 FROM api_tokens WHERE tenant_id=? AND revoked=0", (tenant_id,)) is not None


class TokenAuth:
    mode = "token"

    def __init__(self, db: Database | None = None) -> None:
        self._db = db

    @property
    def db(self) -> Database:
        if self._db is None:
            self._db = get_database()
        return self._db

    def authenticate(self, headers: dict[str, str]) -> Principal:
        h = {k.lower(): v for k, v in headers.items()}
        auth = h.get("authorization", "")
        if not auth.lower().startswith("bearer ") or len(auth) < 15:
            raise PolicyViolation("missing bearer token")
        row = self.db.one("SELECT tenant_id, user_id, role FROM api_tokens WHERE token_sha256=? AND revoked=0",
                          (_h(auth[7:].strip()),))
        if row is None:
            raise PolicyViolation("invalid or revoked token")
        return Principal(tenant_id=row["tenant_id"], user_id=row["user_id"], role=Role(row["role"]))
