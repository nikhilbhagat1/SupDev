"""Small SQLite wrapper shared by the settings store, secret store and token auth."""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenant_config (tenant_id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS secrets (tenant_id TEXT NOT NULL, name TEXT NOT NULL, ciphertext TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY (tenant_id, name));
CREATE TABLE IF NOT EXISTS api_tokens (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL, token_sha256 TEXT NOT NULL UNIQUE, created_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
"""


class Database:
    def __init__(self, path: str = ":memory:") -> None:
        self._c = sqlite3.connect(path, check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            if path != ":memory:":
                self._c.execute("PRAGMA journal_mode=WAL")
            self._c.executescript(_SCHEMA)
            self._c.commit()

    def exec(self, sql: str, args: tuple[Any, ...] = ()) -> None:
        with self.lock:
            self._c.execute(sql, args)
            self._c.commit()

    def one(self, sql: str, args: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self.lock:
            row: sqlite3.Row | None = self._c.execute(sql, args).fetchone()
            return row

    def all(self, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self._c.execute(sql, args).fetchall()


_singletons: dict[str, Database] = {}


def get_database(path: str | None = None) -> Database:
    p = path or os.environ.get("SUPDEV_DB", "supdev.db")
    if p not in _singletons:
        _singletons[p] = Database(p)
    return _singletons[p]
