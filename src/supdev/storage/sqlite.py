from __future__ import annotations

import sqlite3
import threading

from ..core.models import Session


class SqliteSessionStore:
    def __init__(self, path: str = "supdev.db") -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    @property
    def _db(self) -> sqlite3.Connection:  # lazy: plugin discovery instantiates this without touching disk
        if self._conn is None:
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "tenant_id TEXT NOT NULL, id TEXT NOT NULL, user_id TEXT NOT NULL, "
                "created_at REAL NOT NULL, body TEXT NOT NULL, PRIMARY KEY (tenant_id, id))"
            )
            self._conn.commit()
        return self._conn

    def save(self, session: Session) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO sessions (tenant_id, id, user_id, created_at, body) VALUES (?,?,?,?,?) "
                "ON CONFLICT(tenant_id, id) DO UPDATE SET body=excluded.body",
                (session.tenant_id, session.id, session.user_id, session.created_at,
                 session.model_dump_json()),
            )
            self._db.commit()

    def get(self, tenant_id: str, session_id: str) -> Session | None:
        with self._lock:
            row = self._db.execute(
                "SELECT body FROM sessions WHERE tenant_id=? AND id=?", (tenant_id, session_id)
            ).fetchone()
        return Session.model_validate_json(row[0]) if row else None

    def list(self, tenant_id: str, user_id: str | None = None) -> list[Session]:
        q, args = "SELECT body FROM sessions WHERE tenant_id=?", [tenant_id]
        if user_id:
            q += " AND user_id=?"
            args.append(user_id)
        with self._lock:
            rows = self._db.execute(q + " ORDER BY created_at DESC", args).fetchall()
        return [Session.model_validate_json(r[0]) for r in rows]
