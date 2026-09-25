"""Operator-level (platform) switches. Tenant admins can never change these — they gate the features that would
otherwise let a tenant admin run code or reach internal networks from the platform host."""
from __future__ import annotations

import os
from pathlib import Path


def _on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def allow_private_urls() -> bool:  # SSRF guard off (needed for in-cluster Grafana/Rancher etc.)
    return _on("SUPDEV_ALLOW_PRIVATE_URLS")


def allow_http() -> bool:  # plain http:// for integration URLs
    return _on("SUPDEV_ALLOW_HTTP")


def allow_stdio_mcp() -> bool:  # tenant-configured MCP servers that spawn a local process (= code execution)
    return _on("SUPDEV_ALLOW_STDIO_MCP")


def mcp_allowed_commands() -> set[str]:
    return {c.strip() for c in os.environ.get("SUPDEV_MCP_ALLOWED_COMMANDS", "").split(",") if c.strip()}


def allow_local_exec() -> bool:  # run repo test/lint commands on the platform host (= code execution)
    return _on("SUPDEV_ALLOW_LOCAL_EXEC")


def workdir_root() -> Path:
    return Path(os.environ.get("SUPDEV_WORKDIR_ROOT", "work")).resolve()
