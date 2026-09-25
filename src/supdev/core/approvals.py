"""Approval service (A6).

Approvals are server-side records created ONLY by an authenticated human action. Each is bound to
the hash of the exact artifact that was shown; if the artifact changes, the approval is void.
The model can *request* an approval (by presenting an artifact) but can never grant one.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import ApprovalError
from .models import Approval, ApprovalKind, Principal, WorkItem, canonical_hash, now
from .policy import Policy

# Deliberately strict: silence and partial feedback are not approval.
_EXPLICIT = re.compile(
    r"^\s*(approved?|yes,?\s+go\s+ahead|yes|accept(ed)?|go\s+ahead|lgtm)\s*[.!]*\s*$", re.I
)


def is_explicit_approval(text: str) -> bool:
    return bool(_EXPLICIT.match(text))


class ApprovalService:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    # -- request -------------------------------------------------------------------------
    def request(self, wi: WorkItem, kind: ApprovalKind, artifact: Any) -> Approval:
        """Record that `artifact` was shown for approval. Replaces any older pending request."""
        h = canonical_hash(artifact)
        wi.artifacts[kind.value] = artifact
        wi.approvals = [a for a in wi.approvals if not (a.kind == kind and not a.granted)]
        # A previously granted approval for a different hash is now void; keep it for audit but
        # validity is always recomputed against the current artifact hash.
        ap = Approval(kind=kind, artifact_hash=h)
        wi.approvals.append(ap)
        return ap

    def pending(self, wi: WorkItem) -> list[Approval]:
        return [
            a
            for a in wi.approvals
            if not a.granted and a.artifact_hash == self._current_hash(wi, a.kind)
        ]

    # -- grant (human only) --------------------------------------------------------------
    def grant(
        self,
        wi: WorkItem,
        principal: Principal,
        approval_id: str,
        seen_hash: str | None = None,
    ) -> Approval:
        ap = next((a for a in wi.approvals if a.id == approval_id), None)
        if ap is None:
            raise ApprovalError("no such approval request")
        if ap.granted:
            raise ApprovalError("already granted")
        if not self.policy.can_approve(principal.role, ap.kind):
            allowed = ", ".join(self.policy.roles_for(ap.kind)) or "nobody (blocked by policy)"
            raise ApprovalError(
                f"role '{principal.role.value}' cannot approve '{ap.kind.value}'. "
                f"Roles that can: {allowed}."
            )
        if ap.artifact_hash != self._current_hash(wi, ap.kind):
            raise ApprovalError("the artifact changed since this request; review the new version")
        if seen_hash is not None and seen_hash != ap.artifact_hash:
            raise ApprovalError("approval does not match the version you were shown")
        ap.granted_by, ap.granted_role, ap.granted_at = principal.user_id, principal.role, now()
        return ap

    def grant_from_text(self, wi: WorkItem, principal: Principal, text: str) -> Approval | None:
        """Explicit chat approvals map to the single pending request; ambiguity => no grant."""
        if not is_explicit_approval(text):
            return None
        pend = self.pending(wi)
        if len(pend) != 1:
            return None
        return self.grant(wi, principal, pend[0].id)

    # -- validity ------------------------------------------------------------------------
    @staticmethod
    def _current_hash(wi: WorkItem, kind: ApprovalKind) -> str | None:
        art = wi.artifacts.get(kind.value)
        return canonical_hash(art) if art is not None else None

    def valid(self, wi: WorkItem, kind: ApprovalKind, artifact: Any | None = None) -> Approval | None:
        """Granted, unconsumed approval whose hash matches the artifact currently on record
        (or an explicit artifact, e.g. the exact args about to be written)."""
        want = canonical_hash(artifact) if artifact is not None else self._current_hash(wi, kind)
        if want is None:
            return None
        for a in reversed(wi.approvals):
            if a.kind == kind and a.granted and not a.consumed and a.artifact_hash == want:
                # still bound to the presented artifact too
                if artifact is None or self._current_hash(wi, kind) == want:
                    return a
        return None

    def consume(self, ap: Approval) -> None:
        ap.consumed = True
