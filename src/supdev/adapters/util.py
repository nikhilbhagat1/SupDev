from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..core.errors import SupdevError
from ..core.netguard import check_url
from ..plugins.base import SecretStore

_REL = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$")
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
MAX_WINDOW = timedelta(days=7)


def parse_time_range(spec: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """'2h' -> (now-2h, now); 'ISO/ISO' or 'ISO,ISO' -> explicit window. Windows over 7 days are refused
    (A7: narrow ranges)."""
    now = now or datetime.now(UTC)
    if m := _REL.match(spec):
        start, end = now - timedelta(seconds=int(m[1]) * _UNIT[m[2]]), now
    else:
        parts = re.split(r"[/,]", spec)
        if len(parts) != 2:
            raise SupdevError(f"unrecognised time_range '{spec}' (use '2h' or 'ISO/ISO')")
        start, end = (datetime.fromisoformat(p.strip().replace("Z", "+00:00")) for p in parts)
    if end <= start:
        raise SupdevError("time_range end must be after start")
    if end - start > MAX_WINDOW:
        raise SupdevError("time_range exceeds 7 days; narrow it")
    return start, end


def instances(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Integration settings may hold one instance or {'instances': [...]} (e.g. one per environment)."""
    return list(cfg["instances"]) if "instances" in cfg else [cfg]


@dataclass
class Http:
    """Thin authenticated httpx wrapper. Credentials are resolved from the platform SecretStore at call
    time and never leave this object."""
    base_url: str
    tenant_id: str
    secrets: SecretStore
    auth: str = "bearer"  # bearer | basic | header:<Name> | none
    secret: str | None = None
    secret2: str | None = None  # basic: user secret name
    timeout: float = 30.0

    def _headers_auth(self) -> tuple[dict[str, str], tuple[str, str] | None]:
        if self.auth == "none" or not self.secret:
            return {}, None
        token = self.secrets.get(self.tenant_id, self.secret)
        if not token:
            raise SupdevError(f"integration credential '{self.secret}' is not configured (Integration Settings)")
        if self.auth == "bearer":
            return {"Authorization": f"Bearer {token}"}, None
        if self.auth.startswith("header:"):
            return {self.auth.split(":", 1)[1]: token}, None
        user = self.secrets.get(self.tenant_id, self.secret2 or "") if self.secret2 else ""
        return {}, (user or "", token)

    async def request(self, method: str, path: str, **kw: Any) -> Any:
        check_url(self.base_url)  # SSRF guard on every request (DNS re-resolved)
        headers, basic = self._headers_auth()
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, follow_redirects=False) as c:
            r = await c.request(method, path, headers={**headers, **kw.pop("headers", {})}, auth=basic, **kw)
        if r.status_code >= 400:
            raise SupdevError(f"{method} {path.split('?')[0]} -> HTTP {r.status_code}: {r.text[:200]}")
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return r.text


def workdir_for(tenant_id: str, repo: str) -> Path:
    """Platform-managed clone location: <SUPDEV_WORKDIR_ROOT>/<tenant>/<org__repo> (tenant admins cannot choose paths)."""
    from ..core.flags import workdir_root

    return workdir_root() / tenant_id / repo.replace("/", "__")
