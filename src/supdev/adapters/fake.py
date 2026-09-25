"""In-memory adapters for tests, demos and offline development. Also the reference for how a real
adapter is structured: `tools()` from the capability contract + `call()` dispatch."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .. import capabilities as cap
from ..core.models import ToolSpec
from ..plugins.base import SecretStore, TenantConfig

Handler = Callable[[dict[str, Any]], Any]


class FakeAdapter:
    def __init__(self, capability: str, specs: list[ToolSpec], handlers: dict[str, Handler] | None = None,
                 vendor: str = "fake") -> None:
        self.capability, self.vendor = capability, vendor
        self._specs = specs
        self.handlers = handlers or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def tools(self) -> list[ToolSpec]:
        return self._specs

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        self.calls.append((tool, args))
        h = self.handlers.get(tool)
        if h is None:
            return {"ok": True}
        return h(args)


def fake_ticketing(items: dict[str, dict[str, Any]] | None = None) -> FakeAdapter:
    items = items if items is not None else {}
    comments: list[dict[str, Any]] = []
    a = FakeAdapter("ticketing", cap.ticketing("fake"), {
        "ticketing.get_item": lambda a: items.get(a["key"], {"error": "not found"}),
        "ticketing.search": lambda a: [],
        "ticketing.add_comment": lambda a: comments.append(a) or {"posted": True},
        "ticketing.update_status": lambda a: {"status": a["status"]},
        "ticketing.create_items": lambda a: {"created": len(a["items"])},
    })
    a.comments = comments  # type: ignore[attr-defined]
    return a


def fake_source_control() -> FakeAdapter:
    repo: dict[str, Any] = {"branches": ["main"], "commits": [], "pushed": [], "prs": []}
    a = FakeAdapter("source_control", cap.source_control("fake"), {
        "source_control.read_file": lambda a: f"# contents of {a['path']}",
        "source_control.search_code": lambda a: [],
        "source_control.get_diff": lambda a: "",
        "source_control.create_branch": lambda a: repo["branches"].append(a["branch"]) or {"branch": a["branch"]},
        "source_control.commit": lambda a: repo["commits"].append(a) or {"sha": f"c{len(repo['commits'])}"},
        "source_control.push": lambda a: repo["pushed"].append(a["branch"]) or {"pushed": True},
        "source_control.open_pr": lambda a: repo["prs"].append(a) or {"url": "https://example.test/pr/1", "number": 1},
        "source_control.merge_pr": lambda a: {"merged": True},
        "source_control.delete_branch": lambda a: {"deleted": True},
    })
    a.repo = repo  # type: ignore[attr-defined]
    return a


def fake_execution(passed: bool = True) -> FakeAdapter:
    res = lambda a: {"passed": passed, "summary": "ok" if passed else "1 failed"}  # noqa: E731
    return FakeAdapter("execution", cap.execution("fake"), {
        "execution.run_tests": res, "execution.run_lint": res, "execution.run_typecheck": res})


def fake_observability(env: str = "prod", data: dict[str, Any] | None = None) -> list[FakeAdapter]:
    data = data or {}
    return [
        FakeAdapter("metrics", cap.metrics("fake", env), {f"metrics.query.{env}": lambda a: data.get("metrics", {"error_rate": 0.02})}),
        FakeAdapter("logs", cap.logs("fake", env), {f"logs.query.{env}": lambda a: data.get("logs", [])}),
        FakeAdapter("cluster", cap.cluster("fake", env), {f"cluster.events.{env}": lambda a: data.get("cluster", [])}),
    ]


def fake_docs() -> FakeAdapter:
    return FakeAdapter("docs", cap.docs("fake"), {"docs.write_postmortem": lambda a: {"doc": "pm-1"}})


class FakeAdapterFactory:
    """Registered under `supdev.capabilities:fake`; enabled per tenant via integrations={'fake': {}}."""

    def create(self, tenant: TenantConfig, secrets: SecretStore) -> list[Any]:
        return [fake_ticketing(), fake_source_control(), fake_execution(), fake_docs(),
                *fake_observability("staging")]
