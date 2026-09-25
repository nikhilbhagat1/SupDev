from supdev.core.models import LLMResponse, ToolCall
from supdev.llm.fake import call, say

from ..conftest import Env


def multi(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(tool_calls=list(calls))


def tc(name, id=None, **args):
    return ToolCall(id=id or f"tc_{name}", name=name, args=args)


def last_user_idx(messages):
    return max(i for i, m in enumerate(messages) if m.role == "user")


D1 = dict(template="d1", data=dict(
    work_item="T-1", title="Export", understanding="Export orders as CSV.",
    clear=["F1"], assumptions=["Max 10k rows"],
    questions=[dict(text="Which delimiter?", blocking=True, options=["comma", "semicolon"])]))

D2 = dict(template="d2", data=dict(
    work_item="T-1", iteration=1, max_iterations=3, goal="CSV export endpoint",
    approach=["Add exporter", "Wire endpoint"], changes=["api: /export"], unit_tests="3", integration_tests="1",
    risks=["Large files"], estimate="S"))

PLAN = {"approach": "x", "ac_coverage": {"AC1": "exporter tests"}}

D3 = dict(template="d3", data=dict(
    work_item="T-1", branch="feature/T-1-export", default_branch="main", title="Add export", summary="s",
    changes_by_area=["api"], unit_tests_added=3, integration_tests_added=1, lint_typecheck_ok=True,
    ac_to_tests={"AC1": ["test_export"]}))


async def test_full_dev_flow():
    def resolve(system, messages, tools):
        i = last_user_idx(messages)
        return multi(tc("engine.set_requirements", items=[
            dict(id="AC1", area="acceptance", text="CSV has header", status="clear", resolved_by_message=i),
            dict(id="N1", area="non_functional", text="10k rows", status="assumed", accept_assumption=True,
                 resolved_by_message=i)]))

    e = Env([
        multi(tc("ticketing.get_item", key="T-1"),
              tc("engine.set_requirements", items=[
                  dict(id="F1", area="functional", text="export", status="clear"),
                  dict(id="AC1", area="acceptance", text="CSV has header", status="ambiguous"),
                  dict(id="N1", area="non_functional", text="10k rows", status="assumed")]),
              tc("engine.advance_phase")),
        call("engine.present", **D1),
        # turn 2
        resolve,
        call("engine.request_start_planning"),
        # turn 3 (user says approved)
        multi(tc("engine.advance_phase"), tc("engine.save_plan", plan=PLAN), tc("engine.advance_phase"),
              tc("engine.present", **D2)),
        # turn 4
        multi(tc("engine.advance_phase"),
              tc("source_control.create_branch", branch="feature/T-1-export"),
              tc("source_control.commit", branch="feature/T-1-export", message="feat", files={"a.py": "x"}),
              tc("engine.advance_phase"),
              tc("execution.run_tests", id="tc_tests"),
              tc("engine.record_test_run", call_ids=["tc_tests"], ac_map={"AC1": ["test_export"]}),
              tc("engine.advance_phase"),
              tc("engine.present", **D3)),
        # turn 5
        multi(tc("source_control.push", branch="feature/T-1-export"),
              tc("source_control.open_pr", branch="feature/T-1-export", title="Add export", body="b")),
        say("PR opened."),
    ])
    out = await e.say("Implement CSV export from the ticket", ticket_type="story")
    s = e.session
    assert s.active.mode == "dev" and s.active.phase == 1
    assert "[DEV · Phase 2 · Clarification]" in out.of("assistant")[-1].data["text"]
    assert "I won't start planning" in out.of("assistant")[-1].data["text"]

    await e.say("Comma. And the 10k assumption is fine.")
    assert e.session.active.phase == 1                       # still gated: no approval yet
    out = await e.say("approved")                            # START_PLANNING
    assert e.session.active.phase == 3 and out.of("approval_request")
    assert e.session.active.counters["plan_iterations"] == 1

    await e.say("approved")                                  # PLAN
    assert e.session.active.phase == 6
    assert e.sc.repo["commits"] and not e.sc.repo["pushed"]  # nothing pushed before PR approval

    await e.say("yes")                                       # PR
    assert e.sc.repo["pushed"] == ["feature/T-1-export"] and e.sc.repo["prs"]
    assert e.audit.query("acme")  # audit trail exists
    assert e.audit.verify(e.audit.records)


async def test_cannot_plan_while_ambiguous():
    e = Env([
        multi(tc("engine.set_requirements", items=[dict(id="A", area="functional", text="?", status="ambiguous")]),
              tc("engine.advance_phase"), tc("engine.advance_phase")),
        say("stuck"),
    ])
    await e.say("Implement a thing", ticket_type="story")
    tool_msgs = [m.text for m in e.session.messages if m.role == "tool"]
    assert any("cannot leave Phase 2" in t and "Ambiguous" in t for t in tool_msgs)
    assert e.session.active.phase == 1


async def test_model_cannot_self_resolve_ambiguity():
    e = Env([
        multi(tc("engine.set_requirements", items=[dict(id="A", area="functional", text="?", status="ambiguous")]),
              tc("engine.advance_phase")),
        multi(tc("engine.set_requirements", items=[dict(id="A", area="functional", text="?", status="clear")])),
        say("ok"),
    ])
    await e.say("Implement a thing", ticket_type="story")
    await e.say("hello")
    assert any("resolved_by_message" in m.text for m in e.session.messages if m.role == "tool")
    assert e.session.active.requirements[0].status.value == "ambiguous"


async def test_bug_ticket_asks_which_mode():
    e = Env([])
    out = await e.say("Fix login", ticket_type="bug")
    assert "Investigate the root cause first" in out.of("assistant")[0].data["text"]
    assert e.session.active is None
