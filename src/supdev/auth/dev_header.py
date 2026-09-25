"""LOCAL DEV ONLY authenticator: trusts X-Tenant-Id / X-User-Id / X-Role headers.
Replace with an OIDC plugin (entry point `supdev.auth`) for any real deployment."""
from __future__ import annotations

from ..core.errors import PolicyViolation
from ..core.models import Principal, Role


class DevHeaderAuth:
    mode = "dev-header"

    def authenticate(self, headers: dict[str, str]) -> Principal:
        h = {k.lower(): v for k, v in headers.items()}
        try:
            return Principal(
                tenant_id=h["x-tenant-id"], user_id=h["x-user-id"], role=Role(h.get("x-role", "viewer"))
            )
        except (KeyError, ValueError) as exc:
            raise PolicyViolation("missing or invalid identity headers") from exc
