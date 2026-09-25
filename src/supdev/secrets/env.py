"""Platform-held integration credentials, read from env: SUPDEV_SECRET__<TENANT>__<NAME>.

The model never sees these; adapters call `get()` internally (A4)."""
from __future__ import annotations

import os


class EnvSecretStore:
    def __init__(self, values: dict[tuple[str, str], str] | None = None) -> None:
        self._values = values or {}

    def get(self, tenant_id: str, name: str) -> str | None:
        if (tenant_id, name) in self._values:
            return self._values[(tenant_id, name)]
        key = f"SUPDEV_SECRET__{tenant_id}__{name}".upper().replace("-", "_")
        return os.environ.get(key)

    def all_values(self, tenant_id: str) -> list[str]:
        prefix = f"SUPDEV_SECRET__{tenant_id}__".upper().replace("-", "_")
        vals = [v for k, v in os.environ.items() if k.startswith(prefix)]
        return vals + [v for (t, _), v in self._values.items() if t == tenant_id]
