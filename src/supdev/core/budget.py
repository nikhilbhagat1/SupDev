"""Session budget (A8): tool calls, query volume, tokens. Exceeding pauses and asks the user."""

from __future__ import annotations

from .errors import BudgetExceeded
from .models import Session


class BudgetTracker:
    def __init__(self, session: Session, limits: dict[str, int]) -> None:
        self.session = session
        self.limits = limits

    def limit(self, kind: str) -> int:
        return self.limits.get(kind, 0) + self.session.budget_extra.get(kind, 0)

    def used(self, kind: str) -> int:
        return self.session.budget_used.get(kind, 0)

    def check(self, kind: str, n: int = 1) -> None:
        if self.used(kind) + n > self.limit(kind):
            raise BudgetExceeded(kind, self.used(kind), self.limit(kind))

    def charge(self, kind: str, n: int = 1) -> None:
        self.check(kind, n)
        self.session.budget_used[kind] = self.used(kind) + n

    def record_tokens(self, n: int) -> None:
        """Tokens are recorded after the fact; exceeding blocks the *next* step."""
        self.session.budget_used["tokens"] = self.used("tokens") + n

    def extend(self, kind: str, n: int) -> None:
        """Only reachable from an authenticated user action (never from the model)."""
        self.session.budget_extra[kind] = self.session.budget_extra.get(kind, 0) + n
