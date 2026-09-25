"""Encrypted-at-rest secret store (default). Values are write-only from the API's point of view: `names()` lists
what is configured, `get()` is used only inside adapters/LLM providers. Falls back to SUPDEV_SECRET__* env vars."""
from __future__ import annotations

import re

from ..core.crypto import Crypto, load_master_key
from ..core.db import Database, get_database
from ..core.errors import SupdevError
from ..core.models import now
from .env import EnvSecretStore

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


class DbSecretStore:
    def __init__(self, db: Database | None = None, crypto: Crypto | None = None) -> None:
        self._db, self._crypto = db, crypto
        self._env = EnvSecretStore()

    @property
    def db(self) -> Database:
        if self._db is None:
            self._db = get_database()
        return self._db

    @property
    def crypto(self) -> Crypto:
        if self._crypto is None:
            self._crypto = Crypto(load_master_key())
        return self._crypto

    def get(self, tenant_id: str, name: str) -> str | None:
        row = self.db.one("SELECT ciphertext FROM secrets WHERE tenant_id=? AND name=?", (tenant_id, name))
        return self.crypto.decrypt(tenant_id, name, row["ciphertext"]) if row else self._env.get(tenant_id, name)

    def set(self, tenant_id: str, name: str, value: str) -> None:
        if not NAME.match(name):
            raise SupdevError(f"invalid secret name '{name}'")
        if not value or len(value) > 8192 or any(ord(c) < 32 for c in value):
            raise SupdevError("secret value must be 1–8192 printable characters")
        self.db.exec("INSERT INTO secrets (tenant_id, name, ciphertext, updated_at) VALUES (?,?,?,?) "
                     "ON CONFLICT(tenant_id, name) DO UPDATE SET ciphertext=excluded.ciphertext, updated_at=excluded.updated_at",
                     (tenant_id, name, self.crypto.encrypt(tenant_id, name, value), now()))

    def delete(self, tenant_id: str, name: str) -> None:
        self.db.exec("DELETE FROM secrets WHERE tenant_id=? AND name=?", (tenant_id, name))

    def names(self, tenant_id: str) -> dict[str, float]:
        return {r["name"]: r["updated_at"] for r in self.db.all(
            "SELECT name, updated_at FROM secrets WHERE tenant_id=? ORDER BY name", (tenant_id,))}

    def all_values(self, tenant_id: str) -> list[str]:  # for the redactor's known-secret list
        out = [self.crypto.decrypt(tenant_id, r["name"], r["ciphertext"]) for r in self.db.all(
            "SELECT name, ciphertext FROM secrets WHERE tenant_id=?", (tenant_id,))]
        return out + self._env.all_values(tenant_id)


class OverlaySecrets:
    """Ephemeral values on top of a store (used to test/discover with a secret typed in the form, before saving)."""

    def __init__(self, base: object, values: dict[str, str]) -> None:
        self._base, self._values = base, values

    def get(self, tenant_id: str, name: str) -> str | None:
        return self._values.get(name) or self._base.get(tenant_id, name)  # type: ignore[attr-defined]
