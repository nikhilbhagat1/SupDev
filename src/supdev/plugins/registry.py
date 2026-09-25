"""Plugin registry with entry-point discovery.

Third parties add a mode/integration/LLM by shipping a package with an entry point in one of the
`supdev.*` groups (see PluginKind) — or by listing a dotted path in config. No core edits needed.
"""

from __future__ import annotations

import importlib
import logging
from importlib.metadata import entry_points
from typing import Any

from ..core.errors import PluginError
from .base import PluginKind

log = logging.getLogger(__name__)

# Minimal structural validation per kind: attributes/methods a plugin object must expose.
_REQUIRED: dict[PluginKind, tuple[str, ...]] = {
    PluginKind.LLM: ("name", "complete"),
    PluginKind.CAPABILITY: ("create",),
    PluginKind.MODE: ("name", "phases", "internal_tools", "check_tool", "prompt_file"),
    PluginKind.STORAGE: ("save", "get", "list"),
    PluginKind.SECRETS: ("get",),
    PluginKind.AUDIT: ("write", "query"),
    PluginKind.AUTH: ("authenticate",),
    PluginKind.REDACTOR: ("redact", "redact_obj"),
}


class PluginRegistry:
    def __init__(self) -> None:
        self._items: dict[PluginKind, dict[str, Any]] = {k: {} for k in PluginKind}

    def register(self, kind: PluginKind, name: str, plugin: Any) -> None:
        if name in self._items[kind]:
            raise PluginError(f"duplicate {kind.value} plugin '{name}'")
        target = plugin() if isinstance(plugin, type) else plugin
        missing = [a for a in _REQUIRED[kind] if not hasattr(target, a)]
        if missing:
            raise PluginError(f"{kind.value} plugin '{name}' is missing: {', '.join(missing)}")
        self._items[kind][name] = target

    def get(self, kind: PluginKind, name: str) -> Any:
        try:
            return self._items[kind][name]
        except KeyError:
            known = ", ".join(sorted(self._items[kind])) or "none"
            raise PluginError(f"unknown {kind.value} plugin '{name}' (available: {known})") from None

    def names(self, kind: PluginKind) -> list[str]:
        return sorted(self._items[kind])

    def all(self, kind: PluginKind) -> dict[str, Any]:
        return dict(self._items[kind])

    # -- discovery -----------------------------------------------------------------------
    def discover(self, extra: dict[str, str] | None = None, strict: bool = False) -> list[str]:
        """Load plugins from entry points and `extra` ("kind:name" -> "pkg.mod:Attr").
        Broken third-party plugins are skipped and reported unless `strict`."""
        problems: list[str] = []
        for kind in PluginKind:
            for ep in entry_points(group=kind.value):
                try:
                    self.register(kind, ep.name, ep.load())
                except Exception as exc:  # noqa: BLE001 — isolate bad plugins
                    problems.append(f"{kind.value}:{ep.name}: {exc}")
        for key, dotted in (extra or {}).items():
            try:
                kind_s, name = key.split(":", 1)
                mod, attr = dotted.split(":", 1)
                self.register(PluginKind(kind_s), name, getattr(importlib.import_module(mod), attr))
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{key}: {exc}")
        for p in problems:
            log.warning("plugin load failed: %s", p)
        if strict and problems:
            raise PluginError("; ".join(problems))
        return problems


def default_registry(extra: dict[str, str] | None = None) -> PluginRegistry:
    reg = PluginRegistry()
    reg.discover(extra)
    return reg
