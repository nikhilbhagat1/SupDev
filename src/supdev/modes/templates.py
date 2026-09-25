"""Structured versions of templates D1–D3, S1–S2 (Part E). The model supplies fields; the engine
renders the exact format, so structure is deterministic and validated (e.g. plan summary size)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def tag(mode: str, phase: int, name: str) -> str:
    return f"[{mode.upper()} · Phase {phase} · {name}]"


def _bullets(items: list[str], numbered: bool = False) -> str:
    return "\n".join(f"{i}. {x}" if numbered else f"- {x}" for i, x in enumerate(items, 1))


# ------------------------------------------------------------------------------ DEV


class Question(BaseModel):
    text: str
    blocking: bool = False
    options: list[str] = Field(default_factory=list)


class D1Clarification(BaseModel):
    work_item: str
    title: str
    understanding: str
    clear: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)

    def render(self) -> str:
        qs = sorted(self.questions, key=lambda q: not q.blocking)  # blocking first
        lines = [
            f"{tag('dev', 2, 'Clarification')} — {self.work_item}: {self.title}",
            f"My understanding: {self.understanding}",
            "✅ Clear: " + ("; ".join(self.clear) or "—"),
            "⚠️ Assumptions (confirm/correct):",
            _bullets(self.assumptions, True) or "—",
            "❓ Questions (blocking first):",
        ]
        for i, q in enumerate(qs, 1):
            opts = ""
            if q.options:
                opts = " Options: " + " ".join(f"({chr(96 + j)}) {o}" for j, o in enumerate(q.options, 1))
            lines.append(f"{i}. {'[Blocking] ' if q.blocking else ''}{q.text}{opts}")
        lines.append("🔀 Conflicts between sources: " + ("; ".join(self.conflicts) or "none"))
        lines.append("I won't start planning until these are resolved.")
        return "\n".join(lines)


class D2PlanSummary(BaseModel):
    work_item: str
    iteration: int
    max_iterations: int
    goal: str
    approach: list[str] = Field(min_length=2, max_length=4)
    changes: list[str] = Field(min_length=1, max_length=8)
    unit_tests: str
    integration_tests: str
    risks: list[str] = Field(min_length=1, max_length=3)
    new_deps: list[str] = Field(default_factory=list)
    estimate: Literal["S", "M", "L"]
    changed_since_last: str = "—"

    def render(self) -> str:
        return "\n".join([
            f"{tag('dev', 4, 'Plan Review')} — {self.work_item} · Iteration {self.iteration}/{self.max_iterations}",
            f"Goal: {self.goal}",
            "Approach:", _bullets(self.approach),
            "Changes:", _bullets(self.changes),
            f"Testing: Unit: {self.unit_tests} | Integration: {self.integration_tests}",
            "Risks:", _bullets(self.risks),
            f"New deps: {', '.join(self.new_deps) or 'none'} · Estimate: {self.estimate}",
            f"Changed since last iteration: {self.changed_since_last}",
            'Reply "approved" to start development, or tell me what to change.',
        ])


class D3PrPreview(BaseModel):
    work_item: str
    branch: str
    default_branch: str
    title: str
    summary: str
    changes_by_area: list[str]
    unit_tests_added: int
    integration_tests_added: int
    lint_typecheck_ok: bool
    ac_to_tests: dict[str, list[str]]
    head_sha: str = ""  # binds the approval to the exact commit being pushed
    deviations: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)

    def render(self) -> str:
        ac = " | ".join(f"{k} → {', '.join(v)}" for k, v in self.ac_to_tests.items())
        return "\n".join([
            f"{tag('dev', 7, 'PR Approval')} — {self.work_item}",
            f"Branch: {self.branch} → {self.default_branch} · Title: {self.title}",
            f"Summary: {self.summary}",
            "Changes:", _bullets(self.changes_by_area),
            f"Tests: {self.unit_tests_added} unit + {self.integration_tests_added} integration added; "
            f"all passing; lint/typecheck {'✅' if self.lint_typecheck_ok else '❌'}",
            f"AC → tests: {ac}",
            f"Deviations from plan (all approved): {'; '.join(self.deviations) or 'none'}",
            f"Observations (not fixed): {'; '.join(self.observations) or 'none'}",
            "Shall I push the branch and open this PR? (yes / edit / no)",
        ])


# --------------------------------------------------------------------------- SUPPORT


class S1Clarification(BaseModel):
    ticket: str
    title: str
    symptom: str
    service_env: str | None = None
    started: str | None = None
    ongoing: str | None = None
    impact: str = "unknown"
    questions: list[Question]
    ticket_created_at: str = ""

    def render(self) -> str:
        q = lambda v: v if v else "❓"  # noqa: E731
        lines = [
            f"{tag('support', 2, 'Clarification')} — {self.ticket}: {self.title}",
            f"Symptom: {self.symptom} · Service/env: {q(self.service_env)} · Started: {q(self.started)} · "
            f"Ongoing: {q(self.ongoing)} · Impact: {self.impact}",
            "I need these before querying production:",
        ]
        for i, x in enumerate(self.questions, 1):
            opts = " Options: " + ", ".join(x.options) if x.options else ""
            lines.append(f"{i}. {x.text}{opts}")
        return "\n".join(lines)


class TimelineEntry(BaseModel):
    utc: str
    local: str = ""
    event: str
    evidence: list[str] = Field(min_length=1)  # evidence ids — every entry must be sourced


class Claim(BaseModel):
    text: str
    label: Literal["Confirmed", "Likely", "Suspected"]
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _confirmed_needs_evidence(self) -> Claim:
        if self.label in ("Confirmed", "Likely") and not self.evidence:
            raise ValueError(f"a '{self.label}' claim must cite evidence ids")
        return self


class S2RcaSummary(BaseModel):
    ticket: str
    status: Literal["Resolved", "Ongoing", "Mitigated"]
    impact: str
    root_cause: Claim
    trigger: Claim | None = None
    contributing: list[Claim] = Field(default_factory=list)
    timeline: list[TimelineEntry]
    ruled_out: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    immediate_actions: list[str] = Field(default_factory=list)  # for humans
    corrective: list[str] = Field(default_factory=list)
    preventive: list[str] = Field(default_factory=list)
    other_observations: list[str] = Field(default_factory=list)

    def cited_ids(self) -> set[str]:
        ids = set(self.root_cause.evidence)
        for c in [self.trigger, *self.contributing]:
            if c:
                ids |= set(c.evidence)
        for t in self.timeline:
            ids |= set(t.evidence)
        return ids

    def render(self) -> str:
        c = lambda x: f"({x.label}): {x.text}" + (f" [{', '.join(x.evidence)}]" if x.evidence else "")  # noqa: E731
        return "\n".join([
            f"{tag('support', 5, 'Review')} — {self.ticket}",
            f"Status: {self.status} · Impact: {self.impact}",
            f"Root cause {c(self.root_cause)}",
            f"Trigger: {c(self.trigger) if self.trigger else 'not determined'} · Contributing factors: "
            + ("; ".join(c(x) for x in self.contributing) or "none identified"),
            "Key timeline (UTC/local):",
            *[f"  {t.utc}{'/' + t.local if t.local else ''} — {t.event} [{', '.join(t.evidence)}]" for t in self.timeline],
            f"Ruled out: {'; '.join(self.ruled_out) or '—'} · Open questions / missing data: "
            f"{'; '.join(self.open_questions) or '—'}",
            "Recommended actions — Immediate (humans): " + ("; ".join(self.immediate_actions) or "—")
            + " | Corrective: " + ("; ".join(self.corrective) or "—")
            + " | Preventive: " + ("; ".join(self.preventive) or "—"),
            f"Other observations: {'; '.join(self.other_observations) or '—'}",
            'Reply "accept" to finalize. I can then post to the ticket, write a postmortem,\n'
            "create follow-up tickets, or hand the fix to DEV mode (each with your approval).",
        ])


TEMPLATES: dict[str, type[BaseModel]] = {
    "d1": D1Clarification, "d2": D2PlanSummary, "d3": D3PrPreview,
    "s1": S1Clarification, "s2": S2RcaSummary,
}


def render(name: str, data: dict[str, Any]) -> tuple[BaseModel, str]:
    model = TEMPLATES[name].model_validate(data)
    return model, model.render()  # type: ignore[attr-defined]
