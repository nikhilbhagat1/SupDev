"""Append-only, hash-chained audit trail (A9)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .models import canonical_hash, now


class AuditRecord(BaseModel):
    ts: float = Field(default_factory=now)
    tenant_id: str
    session_id: str
    user_id: str
    kind: str  # tool_call | tool_denied | approval_requested | approval_granted | flag | mode_switch ...
    system: str = ""  # target system/capability
    action: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)  # must already be redacted
    prev_hash: str = ""
    hash: str = ""


class _ChainMixin:
    _last: dict[str, str]  # tenant -> last hash

    def _seal(self, rec: AuditRecord) -> AuditRecord:
        rec.prev_hash = self._last.get(rec.tenant_id, "")
        body = rec.model_dump(exclude={"hash"})
        rec.hash = canonical_hash(body)
        self._last[rec.tenant_id] = rec.hash
        return rec


class MemoryAuditSink(_ChainMixin):
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []
        self._last = {}

    def write(self, rec: AuditRecord) -> None:
        self.records.append(self._seal(rec))

    def query(self, tenant_id: str, session_id: str | None = None) -> list[AuditRecord]:
        return [
            r
            for r in self.records
            if r.tenant_id == tenant_id and (session_id is None or r.session_id == session_id)
        ]

    @staticmethod
    def verify(records: list[AuditRecord]) -> bool:
        prev: dict[str, str] = {}
        for r in records:
            if r.prev_hash != prev.get(r.tenant_id, ""):
                return False
            if canonical_hash(r.model_dump(exclude={"hash"})) != r.hash:
                return False
            prev[r.tenant_id] = r.hash
        return True


class JsonlAuditSink(_ChainMixin):
    def __init__(self, path: str | Path = "audit.jsonl") -> None:
        self.path = Path(path)
        self._last = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    self._last[r["tenant_id"]] = r["hash"]

    def write(self, rec: AuditRecord) -> None:
        with self.path.open("a") as f:
            f.write(self._seal(rec).model_dump_json() + "\n")

    def query(self, tenant_id: str, session_id: str | None = None) -> list[AuditRecord]:
        if not self.path.exists():
            return []
        out = [AuditRecord.model_validate_json(x) for x in self.path.read_text().splitlines() if x]
        return [
            r
            for r in out
            if r.tenant_id == tenant_id and (session_id is None or r.session_id == session_id)
        ]
