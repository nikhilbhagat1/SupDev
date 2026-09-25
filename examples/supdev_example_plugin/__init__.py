"""Example third-party plugin: adds a `pagerduty`-style ticketing-like read capability and a mode.

Ship this in its own package and declare entry points in ITS pyproject.toml:

    [project.entry-points."supdev.capabilities"]
    statuspage = "supdev_example_plugin:StatuspageFactory"

    [project.entry-points."supdev.modes"]
    review = "supdev_example_plugin:ReviewMode"

No change to supdev core is required.
"""
from __future__ import annotations

from typing import Any

from supdev.core.models import Access, ToolSpec
from supdev.modes.base import ModeBase
from supdev.plugins.base import ExitResult, PhaseSpec


class _Statuspage:
    capability, vendor = "statuspage", "example"

    def tools(self) -> list[ToolSpec]:
        return [ToolSpec(name="statuspage.get_incidents", description="Public status incidents",
                         capability="statuspage", vendor="example", access=Access.READ, environment="prod")]

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        return {"incidents": []}


class StatuspageFactory:
    def create(self, tenant: Any, secrets: Any) -> list[Any]:
        return [_Statuspage()]


class ReviewMode(ModeBase):
    """A one-phase read-only code-review mode, to show that modes are plugins too."""

    name = "review"
    label = "Review"
    prompt_file = "style.md"
    router_signals = ["review this pr", "code review"]
    template_phases: dict[str, set[int]] = {}

    def phases(self) -> list[PhaseSpec]:
        return [PhaseSpec("review", "Review", lambda ctx: ExitResult(True))]
