from __future__ import annotations

from ..core.models import Session


class MemorySessionStore:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], str] = {}

    def save(self, session: Session) -> None:
        self._data[(session.tenant_id, session.id)] = session.model_dump_json()

    def get(self, tenant_id: str, session_id: str) -> Session | None:
        raw = self._data.get((tenant_id, session_id))  # tenant is part of the key (A3)
        return Session.model_validate_json(raw) if raw else None

    def list(self, tenant_id: str, user_id: str | None = None) -> list[Session]:
        out = [Session.model_validate_json(v) for (t, _), v in self._data.items() if t == tenant_id]
        return [s for s in out if user_id is None or s.user_id == user_id]
