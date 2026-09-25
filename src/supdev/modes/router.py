"""Mode router (Part B1). Deterministic first; ask the user when unclear — never guess on Bugs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RouteDecision:
    mode: str | None = None
    question: str | None = None


BUG_QUESTION = ("This is a Bug ticket. Investigate the root cause first (Support), or implement a "
                "known fix (Dev)? Reply \"support\" or \"dev\".")


def route(text: str, ticket_type: str | None, modes: dict[str, Any], enabled: set[str] | None) -> RouteDecision:
    usable = {n: m for n, m in modes.items() if enabled is None or n in enabled}
    if not usable:
        return RouteDecision(question="No modes are enabled for your tenant/role. Ask an admin to enable one.")
    tt = (ticket_type or "").strip().lower()
    if tt == "bug":
        return RouteDecision(question=BUG_QUESTION) if len(usable) > 1 else RouteDecision(mode=next(iter(usable)))
    for n, m in usable.items():
        if tt and tt in m.ticket_types:
            return RouteDecision(mode=n)
    low = text.lower()
    scores = {n: sum(1 for s in m.router_signals if s in low) for n, m in usable.items()}
    best = max(scores.values(), default=0)
    winners = [n for n, v in scores.items() if v == best]
    if best > 0 and len(winners) == 1:
        return RouteDecision(mode=winners[0])
    if len(usable) == 1:
        return RouteDecision(mode=next(iter(usable)))
    return RouteDecision(question="Is this new work to implement (Dev), or a production issue to "
                                  "investigate (Support)? Reply \"dev\" or \"support\".")


def parse_choice(text: str, modes: dict[str, Any]) -> str | None:
    t = text.strip().lower().strip(".!\"' ")
    return t if t in modes else None
