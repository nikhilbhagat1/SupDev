"""MCP bridge: exposes tools of any MCP server as capability-tagged tools.

Tenant config (integrations['mcp']):
  {"servers": [{"name": "grafana-mcp", "command": "uvx", "args": ["mcp-grafana"], "env_secrets": {"GRAFANA_TOKEN": "grafana_token"},
                "tools": {"query_prometheus": {"capability": "metrics", "access": "read", "environment": "prod",
                                               "bounded": true, "description": "..."}}}]}
Only tools listed under `tools` are exposed — anything untagged is DENIED by default (A11). Use `url` instead of
`command` for streamable-HTTP servers. Secrets resolve from the platform SecretStore, never from the model."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from ..core.errors import PluginError, SupdevError
from ..core.models import Access, ApprovalKind, ToolSpec
from ..core.netguard import check_url
from ..plugins.base import SecretStore, TenantConfig


class McpAdapter:
    def __init__(self, tenant_id: str, secrets: SecretStore, server: dict[str, Any]) -> None:
        self.tenant_id, self.secrets, self.server = tenant_id, secrets, server
        self.vendor = f"mcp:{server['name']}"
        tags = server.get("tools", {})
        caps = {t["capability"] for t in tags.values()}
        self.capability = next(iter(caps)) if len(caps) == 1 else "mcp"
        self._specs: dict[str, ToolSpec] = {}
        for name, t in tags.items():
            access = Access(t.get("access", "read"))
            self._specs[f"{t['capability']}.{name}" + (f".{t['environment']}" if t.get("environment") else "")] = ToolSpec(
                name=f"{t['capability']}.{name}" + (f".{t['environment']}" if t.get("environment") else ""),
                description=t.get("description", f"MCP tool {name} on {server['name']}"),
                input_schema=t.get("input_schema") or {"type": "object", "additionalProperties": True},
                capability=t["capability"], vendor=self.vendor, access=access, environment=t.get("environment"),
                approval_kind=ApprovalKind(t["approval_kind"]) if t.get("approval_kind") else None,
                artifact_arg=t.get("artifact_arg"), bounded=bool(t.get("bounded")))
        self._remote = {spec_name: n for n, spec_name in zip(tags, self._specs, strict=True)}

    def tools(self) -> list[ToolSpec]:
        return list(self._specs.values())

    async def ping(self) -> str:
        tools = await list_remote_tools(self.tenant_id, self.secrets, self.server)
        return f"connected; server offers {len(tools)} tools, {len(self._specs)} exposed"

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        async with _session(self.tenant_id, self.secrets, self.server) as session:
            res = await session.call_tool(self._remote[tool], args)
        text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
        if getattr(res, "is_error", getattr(res, "isError", False)):
            raise SupdevError(f"MCP tool error: {text[:300]}")
        return text


def _http_transport(url: str, headers: dict[str, str]) -> Any:
    """The MCP SDK renamed its HTTP client between releases; support both."""
    from mcp.client import streamable_http as sh

    if hasattr(sh, "streamable_http_client"):  # mcp >= 2
        return sh.streamable_http_client(url, http_client=sh.create_mcp_http_client(headers=headers or None))
    return sh.streamablehttp_client(url, headers=headers)  # mcp 1.x


@asynccontextmanager
async def _session(tenant_id: str, secrets: SecretStore, srv: dict[str, Any]) -> AsyncIterator[Any]:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:  # pragma: no cover
        raise PluginError("mcp SDK not installed") from exc
    if srv.get("url"):
        check_url(srv["url"])
        headers = {h: v for h, s in srv.get("header_secrets", {}).items() if (v := secrets.get(tenant_id, s))}
        cm: Any = _http_transport(srv["url"], headers)
    else:
        env = {k: v for k, s in srv.get("env_secrets", {}).items() if (v := secrets.get(tenant_id, s))}
        cm = stdio_client(StdioServerParameters(command=srv["command"], args=srv.get("args", []), env=env or None))
    async with cm as streams, ClientSession(streams[0], streams[1]) as session:
        await asyncio.wait_for(session.initialize(), 30)
        yield session


async def list_remote_tools(tenant_id: str, secrets: SecretStore, srv: dict[str, Any]) -> list[dict[str, Any]]:
    """Used by Settings to let an admin tag each tool. Nothing discovered is exposed until it is tagged and saved."""
    async with _session(tenant_id, secrets, srv) as session:
        res = await session.list_tools()
    return [{"name": t.name, "description": (t.description or "")[:300], "input_schema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)}
            for t in res.tools]


class McpFactory:
    def create(self, tenant: TenantConfig, secrets: SecretStore) -> list[Any]:
        return [McpAdapter(tenant.tenant_id, secrets, s) for s in tenant.integrations["mcp"]["servers"]]
