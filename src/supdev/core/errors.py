class SupdevError(Exception):
    """Base error."""


class PluginError(SupdevError):
    pass


class PolicyViolation(SupdevError):
    """A platform/tenant rule blocked an action. Message is safe to show the model."""


class ApprovalError(SupdevError):
    pass


class BudgetExceeded(SupdevError):
    def __init__(self, kind: str, used: int, limit: int) -> None:
        super().__init__(f"budget '{kind}' would exceed limit ({used}/{limit})")
        self.kind = kind
