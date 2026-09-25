from supdev.core.models import LLMResponse, ToolCall
from supdev.llm.fake import say

from ..conftest import Env as _Env

PROD = ("dev", "staging", "prod")


def Env(script, **kw):  # noqa: N802 — support tests run with prod read access
    kw.setdefault("envs", PROD)
    return _Env(script, **kw)


def multi(*calls):
    return LLMResponse(tool_calls=list(calls))


def tc(name, id=None, **args):
    return ToolCall(id=id or f"tc_{name}", name=name, args=args)


SCOPE = dict(symptom="5xx on checkout", services=["checkout"], environment="prod", region=None,
             start_time="2026-09-25T10:00:00Z", ongoing=True, impact="20% failing", suspected_changes=None)

S2 = dict(template="s2", data=dict(
    ticket="INC-9", status="Ongoing", impact="20% of checkouts failing",
    root_cause=dict(text="Bad config in deploy 812", label="Likely", evidence=["E1"]),
    timeline=[dict(utc="10:00", event="error rate rises", evidence=["E1"])],
    immediate_actions=["Roll back deploy 812 (human)"]))

COMMENT = "RCA: deploy 812 config caused elevated 5xx (evidence E1)."


async def test_full_support_flow_and_prod_is_read_only():
    e = Env([
        multi(tc("engine.record_triage", ongoing=True, impact="20% failing"), tc("engine.advance_phase"),
              tc("engine.record_scope", fields=SCOPE), tc("engine.advance_phase"), tc("engine.advance_phase"),
              # unbounded query is refused, bounded one runs
              tc("logs.query.prod", id="tc_bad", service="checkout"),
              tc("metrics.query.prod", id="tc_m", service="checkout", time_range="2h"),
              tc("engine.record_evidence", tool_call_id="tc_m", finding="error_rate 2%"),
              tc("engine.advance_phase"),
              tc("engine.record_hypotheses", new_round=True, hypotheses=[
                  dict(id="H1", statement="bad deploy", supporting=["E1"], confidence="high"),
                  dict(id="H2", statement="db saturation", confidence="low")]),
              tc("engine.advance_phase"),
              tc("engine.present", **S2)),
        # turn 2: user accepts
        multi(tc("engine.advance_phase"),
              tc("engine.request_approval", kind="ticket_comment", content=COMMENT)),
        # turn 3: user approves comment
        multi(tc("ticketing.add_comment", key="INC-9", body="something else entirely"),   # different => denied
              tc("ticketing.add_comment", key="INC-9", body=COMMENT)),
        say("Posted."),
    ])
    await e.say("Production incident: checkout returning 5xx", ticket_type="incident")
    s = e.session
    tools = [m.text for m in s.messages if m.role == "tool"]
    assert s.active.mode == "support" and s.active.phase == 5
    assert any("time_range" in t and t.startswith("DENIED") for t in tools)
    assert s.active.evidence[0].id == "E1"
    await e.say("accept")
    assert e.session.active.phase == 6
    await e.say("approved")
    assert e.tk.comments == [{"key": "INC-9", "body": COMMENT}]   # only the approved text was written
    assert any("needs an approval" in m.text for m in e.session.messages if m.role == "tool")


async def test_support_never_writes_to_prod_or_source_control():
    from supdev.adapters.fake import FakeAdapter
    from supdev.core.models import Access, ToolSpec

    restart = ToolSpec(name="cluster.restart_pod.prod", description="restart", capability="cluster",
                       vendor="rancher", access=Access.UPDATE, environment="prod")
    rancher = FakeAdapter("cluster", [restart])
    e = Env([multi(tc("cluster.restart_pod.prod", service="checkout"),
                   tc("source_control.commit", branch="x", message="m", files={})), say("no")],
            envs=("dev", "prod"))
    e.adapters.append(rancher)
    await e.say("Production incident: outage", ticket_type="incident")
    outs = [m.text for m in e.session.messages if m.role == "tool"]
    assert "production is read-only" in outs[0] and not rancher.calls
    assert "not an operator" in outs[1] or "DENIED" in outs[1]


async def test_query_denied_until_scope_is_bounded():
    e = Env([multi(tc("metrics.query.prod", service="checkout", time_range="2h")), say("ok")])
    await e.say("Production incident: outage", ticket_type="incident")
    out = [m.text for m in e.session.messages if m.role == "tool"][0]
    assert out.startswith("DENIED") and "bounded" in out


async def test_evidence_cannot_be_invented():
    e = Env([multi(tc("engine.record_evidence", tool_call_id="tc_nope", finding="made up")), say("ok")])
    await e.say("Production incident: outage", ticket_type="incident")
    assert "never invent evidence" in [m.text for m in e.session.messages if m.role == "tool"][0]


async def test_handoff_starts_dev_with_fresh_gates():
    e = Env([
        multi(tc("engine.record_triage", ongoing=False), tc("engine.advance_phase"),
              tc("engine.record_scope", fields=SCOPE), tc("engine.advance_phase"), tc("engine.advance_phase"),
              tc("metrics.query.prod", id="tc_m", service="checkout", time_range="2h"),
              tc("engine.record_evidence", tool_call_id="tc_m", finding="f"), tc("engine.advance_phase"),
              tc("engine.record_hypotheses", new_round=True, hypotheses=[
                  dict(id="H1", statement="a", supporting=["E1"], confidence="high"),
                  dict(id="H2", statement="b")]),
              tc("engine.advance_phase"), tc("engine.present", **S2)),
        multi(tc("engine.propose_handoff", proposed_fix="revert config")),
        say("DEV started"),
    ])
    await e.say("Production incident", ticket_type="incident")
    await e.say("accept")
    await e.say("approved")
    s = e.session
    assert s.active.mode == "dev" and s.active.phase == 0 and not s.active.approvals
    assert s.active.context["proposed_fix"] == "revert config"
    assert s.parked[-1].mode == "support"
