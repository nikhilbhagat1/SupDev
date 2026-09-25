"""DEV mode (Part C): ticket/story/design -> clarify -> plan -> approved code -> tests -> PR."""

from __future__ import annotations

from typing import Any

from ...core.models import Access, ApprovalKind, ReqStatus, Requirement, ToolSpec
from ...plugins.base import Denial, ExitResult, InternalResult, ModeContext, PhaseSpec, ToolHandler
from ..base import ModeBase, _spec, fail

P_INTAKE, P_CLARIFY, P_PLAN, P_REVIEW, P_DEV, P_TEST, P_PR = range(7)

ACCEPTANCE = "acceptance"
CI_MARKERS = (".github/workflows/", ".gitlab-ci", "jenkinsfile", ".circleci/", "azure-pipelines", ".buildkite")
DEP_FILES = {"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "pyproject.toml", "poetry.lock",
             "uv.lock", "pipfile", "pipfile.lock", "go.mod", "go.sum", "cargo.toml", "cargo.lock", "gemfile",
             "gemfile.lock", "pom.xml", "build.gradle", "composer.json", "setup.py", "setup.cfg"}
PROTECTED = {"main", "master", "develop", "trunk", "production"}


def _blocking(ctx: ModeContext) -> list[Requirement]:
    return [r for r in ctx.work_item.requirements if r.blocking]


def _exit_intake(ctx: ModeContext) -> ExitResult:
    if not ctx.work_item.requirements:
        return ExitResult(False, "record the requirements model first (engine.set_requirements)")
    return ExitResult(True)


def _exit_clarify(ctx: ModeContext) -> ExitResult:
    if (b := _blocking(ctx)):
        return ExitResult(False, "unresolved Ambiguous/Assumed items: " + ", ".join(r.id for r in b)
                          + ". Never plan while anything is Ambiguous.")
    if ctx.approvals.valid(ctx.work_item, ApprovalKind.START_PLANNING) is None:
        return ExitResult(False, "the user has not confirmed that planning can start "
                                 "(engine.request_start_planning, then wait for the human)")
    return ExitResult(True)


def _exit_plan(ctx: ModeContext) -> ExitResult:
    plan = ctx.work_item.artifacts.get("plan_full")
    if not plan:
        return ExitResult(False, "save the internal plan first (engine.save_plan)")
    return ExitResult(True)


def _exit_review(ctx: ModeContext) -> ExitResult:
    if ctx.approvals.valid(ctx.work_item, ApprovalKind.PLAN) is None:
        return ExitResult(False, "no valid plan approval for the plan as currently presented")
    return ExitResult(True)


def _exit_dev(ctx: ModeContext) -> ExitResult:
    if not any(e.ok and e.access == Access.COMMIT.value for e in ctx.work_item.call_log):
        return ExitResult(False, "no successful commit yet")
    return ExitResult(True)


def _exit_test(ctx: ModeContext) -> ExitResult:
    wi = ctx.work_item
    if not wi.test_runs:
        return ExitResult(False, "no test run recorded (engine.record_test_run)")
    last = wi.test_runs[-1]
    if not last["passed"]:
        return ExitResult(False, "the latest recorded test run did not pass; fix the code, not the tests")
    acs = {r.id for r in wi.requirements if r.area == ACCEPTANCE}
    missing = acs - set(last["ac_map"])
    if missing:
        return ExitResult(False, "acceptance criteria without tests: " + ", ".join(sorted(missing)))
    return ExitResult(True)


def _exit_pr(ctx: ModeContext) -> ExitResult:
    if not any(e.ok and e.access == Access.PR.value for e in ctx.work_item.call_log):
        return ExitResult(False, "PR has not been opened")
    return ExitResult(True)


class DevMode(ModeBase):
    name = "dev"
    prompt_file = "dev_mode.md"
    router_signals = ["implement", "build", "develop", "add feature", "figma", "new feature", "user story"]
    ticket_types = {"story", "task", "feature", "epic"}
    generic_kinds = {ApprovalKind.TICKET_COMMENT, ApprovalKind.TICKET_STATUS, ApprovalKind.MERGE,
                     ApprovalKind.DESTRUCTIVE, ApprovalKind.DEPENDENCY, ApprovalKind.CI_CHANGE,
                     ApprovalKind.DEPLOY}
    template_phases = {"d1": {P_INTAKE, P_CLARIFY}, "d2": {P_REVIEW}, "d3": {P_PR}}

    def phases(self) -> list[PhaseSpec]:
        return [
            PhaseSpec("intake", "Intake & Analysis", _exit_intake),
            PhaseSpec("clarification", "Clarification", _exit_clarify),
            PhaseSpec("planning", "Planning", _exit_plan),
            PhaseSpec("plan_review", "Plan Review", _exit_review),
            PhaseSpec("development", "Development", _exit_dev),
            PhaseSpec("testing", "Testing", _exit_test),
            PhaseSpec("pr_gate", "PR Approval", _exit_pr),
        ]

    # -- guardrails ----------------------------------------------------------------------
    def check_tool(self, ctx: ModeContext, spec: ToolSpec, args: dict[str, Any]) -> Denial | None:
        ph = ctx.work_item.phase
        if spec.internal:
            return None
        if spec.access in (Access.BRANCH, Access.COMMIT) and ph not in (P_DEV, P_TEST):
            return Denial("code changes are only allowed in Development/Testing after plan approval")
        if spec.access in (Access.BRANCH, Access.COMMIT) and ctx.approvals.valid(
                ctx.work_item, ApprovalKind.PLAN) is None:
            return Denial("the plan approval is missing or void (plan changed); re-present the plan")
        if spec.access in (Access.PUSH, Access.PR) and ph != P_PR:
            return Denial("push/PR only at the PR Approval gate")
        branch = str(args.get("branch") or args.get("head") or "")
        if spec.access in (Access.BRANCH, Access.COMMIT, Access.PUSH, Access.PR) and (
                branch in PROTECTED or branch.startswith("release/")):
            return Denial(f"'{branch}' is a protected/default/release branch; never commit or push there")
        if spec.access == Access.COMMIT and isinstance(args.get("files"), dict):
            paths = list(args["files"])
            for kind, hit in ((ApprovalKind.CI_CHANGE, sorted(p for p in paths if any(
                    m in p.lower() for m in CI_MARKERS))),
                              (ApprovalKind.DEPENDENCY, sorted(p for p in paths if (b := p.lower().rsplit("/", 1)[-1])
                                                               in DEP_FILES or (b.startswith("requirements")
                                                                                and b.endswith(".txt"))))):
                if hit and ctx.approvals.valid(ctx.work_item, kind, hit) is None:
                    return Denial(f"changing {', '.join(hit)} needs its own per-action '{kind.value}' approval: "
                                  f"call engine.request_approval(kind='{kind.value}', content={hit}) first")
        if args.get("force"):
            return Denial("force-push / history rewrite needs its own per-action approval and is not offered")
        pr = ctx.work_item.artifacts.get(ApprovalKind.PR.value)
        if spec.access in (Access.PUSH, Access.PR) and isinstance(pr, dict) and branch and branch != pr.get("branch"):
            return Denial(f"only the previewed branch '{pr.get('branch')}' was approved")
        if spec.access == Access.EXECUTE and ph not in (P_DEV, P_TEST):
            return Denial("running tests/lint is a Development/Testing activity")
        if spec.access == Access.COMMENT and ph not in (P_INTAKE, P_CLARIFY, P_PR):
            return Denial("ticket comments are only for clarification questions or the PR link")
        return None

    # -- DEV internal tools --------------------------------------------------------------
    def mode_tools(self) -> list[tuple[ToolSpec, ToolHandler]]:
        req_item = {"type": "object", "properties": {
            "id": {"type": "string"}, "area": {"type": "string", "enum": [
                "functional", ACCEPTANCE, "non_functional", "constraint", "out_of_scope"]},
            "text": {"type": "string"}, "status": {"type": "string", "enum": [s.value for s in ReqStatus]},
            "resolved_by_message": {"type": "integer", "description":
                "index of the USER message that answered this (required to move ambiguous/assumed -> clear)"},
            "accept_assumption": {"type": "boolean"}}, "required": ["id", "area", "text", "status"]}
        return [
            (_spec("engine.set_requirements", "Upsert the requirements model (Clear/Assumed/Ambiguous).",
                   {"items": {"type": "array", "items": req_item}}, ["items"]), self._set_reqs),
            (_spec("engine.request_start_planning", "Ask the user to confirm planning can start. Only "
                   "when nothing is Ambiguous/unaccepted.", {}), self._req_start),
            (_spec("engine.save_plan", "Save the detailed internal plan (not shown to the user). Must "
                   "include ac_coverage mapping every acceptance criterion id to plan sections.",
                   {"plan": {"type": "object"}}, ["plan"]), self._save_plan),
            (_spec("engine.record_test_run", "Record test results. call_ids must be successful "
                   "execution-tool calls; `passed` is verified against them.",
                   {"call_ids": {"type": "array", "items": {"type": "string"}},
                    "ac_map": {"type": "object", "description": "AC id -> [test names]"}},
                   ["call_ids", "ac_map"]), self._record_tests),
            (_spec("engine.propose_mode_switch", "Development revealed a live production problem: "
                   "propose pausing DEV and switching to SUPPORT (needs user approval).",
                   {"reason": {"type": "string"}}, ["reason"]), self._propose_switch),
        ]

    async def _set_reqs(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        wi, users = ctx.work_item, [i for i, m in enumerate(ctx.session.messages) if m.role == "user"]
        by_id = {r.id: r for r in wi.requirements}
        for it in args.get("items", []):
            try:
                new = Requirement.model_validate({**it, "accepted_assumption": False})
            except Exception as exc:  # noqa: BLE001
                return fail(f"bad requirement: {exc}")
            old = by_id.get(new.id)
            ref = it.get("resolved_by_message")
            if old is not None and old.blocking and (
                    new.status == ReqStatus.CLEAR or it.get("accept_assumption")):
                if ref not in users:
                    return fail(f"{new.id}: moving Ambiguous/Assumed to Clear (or accepting an assumption) "
                                "needs `resolved_by_message` = index of a real user message that answered it")
                new.resolved_by_message = ref
                new.accepted_assumption = bool(it.get("accept_assumption")) or new.status == ReqStatus.CLEAR
            elif old is not None:
                new.accepted_assumption = old.accepted_assumption
                new.resolved_by_message = old.resolved_by_message
            by_id[new.id] = new
        wi.requirements = list(by_id.values())
        b = _blocking(ctx)
        return InternalResult(ok=True, content=f"{len(wi.requirements)} requirements; "
                              f"{len(b)} blocking: {', '.join(r.id for r in b) or 'none'}")

    async def _req_start(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        if ctx.work_item.phase != P_CLARIFY:
            return fail("only in the Clarification phase")
        if (b := _blocking(ctx)):
            return fail("still blocking: " + ", ".join(r.id for r in b))
        snap = [r.model_dump(mode="json", exclude={"resolved_by_message"}) for r in ctx.work_item.requirements]
        return self.raise_request(ctx, ApprovalKind.START_PLANNING, snap, "All items are Clear; confirm to start planning")

    async def _save_plan(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        wi, plan = ctx.work_item, args.get("plan") or {}
        if wi.phase not in (P_PLAN, P_REVIEW, P_DEV):
            return fail("plans are saved in Planning (or revised via go_back)")
        acs = {r.id for r in wi.requirements if r.area == ACCEPTANCE}
        cov = set((plan.get("ac_coverage") or {}).keys())
        if acs - cov:
            return fail("self-check failed: acceptance criteria not covered by the plan: "
                        + ", ".join(sorted(acs - cov)))
        wi.artifacts["plan_full"] = plan
        # If a summary was already presented, bind its approval to this plan so any change voids it.
        summ = wi.artifacts.get(ApprovalKind.PLAN.value)
        if isinstance(summ, dict):
            summ["plan_hash"] = self.hash_of(plan)
        return InternalResult(ok=True, content="Plan saved; self-check against acceptance criteria passed.")

    async def _record_tests(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        log = {e.call_id: e for e in ctx.work_item.call_log}
        ids = args.get("call_ids") or []
        runs = [log.get(i) for i in ids]
        if not ids or any(r is None or r.capability != "execution" or not r.ok for r in runs):
            return fail("call_ids must all be successful calls of execution tools from this work item")
        passed = all(r.passed is True for r in runs if r is not None)
        ctx.work_item.test_runs.append({"call_ids": ids, "passed": passed, "ac_map": args.get("ac_map") or {}})
        return InternalResult(ok=True, content=f"Recorded. Verified result from tool output: "
                              f"{'PASSED' if passed else 'NOT passing'}.")

    async def _propose_switch(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        return self.raise_request(ctx, ApprovalKind.MODE_SWITCH,
                                  {"to": "support", "reason": args.get("reason", "")},
                                  "DEV work is paused and kept so you can resume later")

    # -- template hooks ------------------------------------------------------------------
    def prepare_template(self, ctx: ModeContext, name: str, data: dict[str, Any]):  # type: ignore[override]
        wi = ctx.work_item
        if name == "d2":
            n, mx = wi.counters.get("plan_iterations", 0), ctx.policy.max_plan_iterations
            if "plan_full" not in wi.artifacts:
                return fail("save the internal plan before presenting the summary")
            if n >= mx:
                cont = ctx.approvals.valid(wi, ApprovalKind.CONTINUE_PLAN_ITERATIONS)
                if cont is None:
                    return self.raise_request(
                        ctx, ApprovalKind.CONTINUE_PLAN_ITERATIONS,
                        {"iterations_used": n, "options": ["continue", "narrow scope", "back to clarification"]},
                        f"Reached the {mx}-iteration limit: continue, narrow scope, or go back to clarification?")
                ctx.approvals.consume(cont)
                wi.counters["plan_iterations"] = mx - 1
                n = mx - 1
            data.update(iteration=n + 1, max_iterations=mx, work_item=data.get("work_item") or wi.ref or wi.id)
        return data

    def after_render(self, ctx: ModeContext, name: str, dump: dict[str, Any], text: str):  # type: ignore[override]
        wi = ctx.work_item
        if name == "d1":
            return None
        if name == "d2":
            wi.counters["plan_iterations"] = wi.counters.get("plan_iterations", 0) + 1
            artifact = {"summary": dump, "plan_hash": self.hash_of(wi.artifacts.get("plan_full"))}
            ap = ctx.approvals.request(wi, ApprovalKind.PLAN, artifact)
            ctx.emit("approval_request", {"id": ap.id, "kind": "plan", "hash": ap.artifact_hash,
                                          "roles": ctx.policy.roles_for(ApprovalKind.PLAN)})
        elif name == "d3":
            if not wi.test_runs or not wi.test_runs[-1]["passed"]:
                return fail("the PR preview needs a passing recorded test run")
            ap = ctx.approvals.request(wi, ApprovalKind.PR, dump)
            ctx.emit("approval_request", {"id": ap.id, "kind": "pr", "hash": ap.artifact_hash,
                                          "roles": ctx.policy.roles_for(ApprovalKind.PR)})
        return None
