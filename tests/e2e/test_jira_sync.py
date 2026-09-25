"""Phase -> Jira status sync: the ENGINE moves the ticket when the agent moves a work item forward (opt-in)."""
from supdev import capabilities as cap
from supdev.adapters.fake import FakeAdapter
from supdev.core.models import ApprovalKind, Role
from supdev.core.policy import Policy
from supdev.llm.fake import say

from ..conftest import Env, principal
from .test_dev_flow import D1, D2, PLAN, last_user_idx, multi, tc  # noqa: F401


class Tracker(FakeAdapter):
    """Ticketing adapter with the phase-sync capability; records what the engine asked for."""

    def __init__(self, fail=None):
        super().__init__("ticketing", cap.ticketing("fake"))
        self.moves, self.fail = [], fail

    async def transition_to_named(self, key, names):
        self.moves.append((key, list(names)))
        if self.fail:
            raise RuntimeError(self.fail)
        return {"key": key, "from": "To Do", "to": names[0], "changed": True}


def script(then):
    def resolve(system, messages, tools):
        i = last_user_idx(messages)
        return multi(tc("engine.set_requirements", items=[
            dict(id="AC1", area="acceptance", text="CSV has header", status="clear", resolved_by_message=i),
            dict(id="N1", area="non_functional", text="10k rows", status="assumed", accept_assumption=True, resolved_by_message=i)]))
    return [
        multi(tc("engine.set_requirements", items=[dict(id="F1", area="functional", text="x", status="clear"),
                                                  dict(id="AC1", area="acceptance", text="c", status="ambiguous"),
                                                  dict(id="N1", area="non_functional", text="n", status="assumed")]),
              tc("engine.advance_phase")),
        multi(tc("engine.present", **D1)),
        resolve,
        multi(tc("engine.request_start_planning")),
        multi(tc("engine.advance_phase"), tc("engine.save_plan", plan=PLAN), tc("engine.advance_phase"), tc("engine.present", **D2)),
        *then,
    ]


async def drive(e, ref="OPS-1"):
    """Ticket -> clarification -> plan approved; returns after the plan approval turn."""
    await e.say("Implement CSV export", ticket_type="story", ref=ref)
    await e.say("Comma; assumption fine.")
    await e.say("approved")            # start planning
    return await e.say("approved")     # plan -> the script then advances into Development


MAP = {"dev": {"5": ["In Progress", "Development"], "7": ["In Review"]}}      # chosen by the tenant in Settings — nothing built in
ENABLED = {"enabled": True, "map": MAP}


async def test_moving_into_development_moves_the_ticket_to_in_progress():
    t = Tracker()
    e = Env(script([multi(tc("engine.advance_phase")), say("ok")]), adapters=[t], jira_sync=ENABLED)
    out = await drive(e)
    assert e.session.active.phase == 4 and t.moves == [("OPS-1", ["In Progress", "Development"])]
    assert out.of("jira_status")[0].data == {"key": "OPS-1", "from": "To Do", "to": "In Progress", "changed": True, "reason": None}
    assert any("Will change Jira ticket OPS-1 status to 'In Progress'" in n.data["line"] for n in out.of("audit_notice"))   # A9: announced first
    rec = [r for r in e.audit.records if r.kind == "phase_status_sync"]
    assert len(rec) == 1 and rec[0].detail["to"] == "In Progress" and rec[0].detail["phase"] == 5


async def test_enabled_without_a_mapping_moves_nothing_because_no_status_names_are_assumed():
    t = Tracker()
    e = Env(script([multi(tc("engine.advance_phase")), say("ok")]), adapters=[t], jira_sync={"enabled": True, "map": None})
    await drive(e)
    assert e.session.active.phase == 4 and t.moves == []


async def test_sync_is_off_by_default():
    t = Tracker()
    e = Env(script([multi(tc("engine.advance_phase")), say("ok")]), adapters=[t])            # no jira_sync configured
    await drive(e)
    assert e.session.active.phase == 4 and t.moves == []


async def test_earlier_phases_and_tickets_without_a_key_do_not_sync():
    t = Tracker()
    e = Env(script([say("ok")]), adapters=[t], jira_sync=ENABLED)
    await drive(e)                                                        # reaches Plan Review (phase 4): unmapped
    assert t.moves == []
    t2 = Tracker()
    e2 = Env(script([multi(tc("engine.advance_phase")), say("ok")]), adapters=[t2], jira_sync=ENABLED)
    await drive(e2, ref=None)                                             # no ticket key => nothing to move
    assert e2.session.active.phase == 4 and t2.moves == []


async def test_custom_mapping_replaces_the_defaults_and_blank_means_no_change():
    t = Tracker()
    e = Env(script([multi(tc("engine.advance_phase")), say("ok")]), adapters=[t],
            jira_sync={"enabled": True, "map": {"dev": {"5": ["Coding"]}}})
    await drive(e)
    assert t.moves == [("OPS-1", ["Coding"])]
    t2 = Tracker()
    e2 = Env(script([multi(tc("engine.advance_phase")), say("ok")]), adapters=[t2], jira_sync={"enabled": True, "map": {"dev": {}}})
    await drive(e2)
    assert t2.moves == []


async def test_going_back_never_moves_the_ticket():
    t = Tracker()
    e = Env(script([multi(tc("engine.advance_phase"), tc("engine.go_back", phase=3)), say("ok")]), adapters=[t], jira_sync=ENABLED)
    await drive(e)
    assert e.session.active.phase == 2 and len(t.moves) == 1              # one forward move into Development, none for the step back


async def test_jira_failure_never_blocks_the_workflow_and_is_reported():
    t = Tracker(fail="HTTP 400: transition needs a resolution")
    e = Env(script([multi(tc("engine.advance_phase")), say("ok")]), adapters=[t], jira_sync=ENABLED)
    out = await drive(e)
    assert e.session.active.phase == 4                                    # the phase change still happened
    assert "resolution" in out.of("jira_sync_failed")[0].data["reason"]
    assert [r.kind for r in e.audit.records if r.kind.startswith("phase_status_sync")] == ["phase_status_sync_failed"]


async def test_roles_that_cannot_change_ticket_status_do_not_trigger_writes():
    pol = Policy()
    pol.approver_roles[ApprovalKind.TICKET_STATUS] = {Role.LEAD, Role.ADMIN}      # developers may not change ticket status here
    t = Tracker()
    e = Env(script([multi(tc("engine.advance_phase")), say("ok")]), adapters=[t], jira_sync=ENABLED, tenant_policy=pol)
    e.p = principal(Role.DEVELOPER)
    e.sid = e.rt.create_session(e.p).id
    out = await drive(e)
    assert e.session.active.phase == 4 and t.moves == [] and out.of("jira_sync_skipped")
