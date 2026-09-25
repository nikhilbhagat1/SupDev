"""Vendor-neutral capability contracts.

Each function returns the ToolSpecs a vendor adapter must implement for that capability. Adapters
(github, jira, grafana, ...) and the MCP bridge reuse these so the model, gates and prompts never
depend on a vendor. Result conventions the engine relies on:
  * execution tools return {"passed": bool, ...} so test claims can be verified (A10)
  * bounded query tools require `time_range` + a scope (service|namespace|labels)
"""

from __future__ import annotations

from typing import Any

from ..core.models import Access, ApprovalKind, ToolSpec

_S = {"type": "string"}


def _obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": req or []}


def _name(cap: str, op: str, env: str | None) -> str:
    return f"{cap}.{op}" + (f".{env}" if env else "")


def _t(cap: str, op: str, vendor: str, desc: str, schema: dict[str, Any], *, env: str | None = None,
       access: Access = Access.READ, kind: ApprovalKind | None = None, art: str | None = None,
       bounded: bool = False) -> ToolSpec:
    return ToolSpec(name=_name(cap, op, env), description=desc, input_schema=schema, capability=cap,
                    vendor=vendor, access=access, environment=env, approval_kind=kind, artifact_arg=art,
                    bounded=bounded)


def ticketing(vendor: str) -> list[ToolSpec]:
    c = "ticketing"
    return [
        _t(c, "get_item", vendor, "Fetch a work item with comments, links, sub-tasks.", _obj({"key": _S}, ["key"])),
        _t(c, "search", vendor, "Search work items (similar past tickets).", _obj({"query": _S}, ["query"])),
        _t(c, "add_comment", vendor, "Post a comment. Needs an approved artifact equal to `body`.",
           _obj({"key": _S, "body": _S}, ["key", "body"]), access=Access.COMMENT,
           kind=ApprovalKind.TICKET_COMMENT, art="body"),
        _t(c, "update_status", vendor, "Change ticket status. Needs approved artifact equal to `status`.",
           _obj({"key": _S, "status": _S}, ["key", "status"]), access=Access.UPDATE,
           kind=ApprovalKind.TICKET_STATUS, art="status"),
        _t(c, "create_items", vendor, "Create follow-up tickets. Needs approved artifact equal to `items`.",
           _obj({"items": {"type": "array", "items": {"type": "object"}}}, ["items"]),
           access=Access.WRITE, kind=ApprovalKind.FOLLOWUP_TICKETS, art="items"),
    ]


def source_control(vendor: str) -> list[ToolSpec]:
    c = "source_control"
    return [
        _t(c, "read_file", vendor, "Read a file at a ref.", _obj({"path": _S, "ref": _S}, ["path"])),
        _t(c, "search_code", vendor, "Search the repository.", _obj({"query": _S}, ["query"])),
        _t(c, "get_diff", vendor, "Diff between refs / of a PR.", _obj({"base": _S, "head": _S})),
        _t(c, "create_branch", vendor, "Create a work branch off the default branch (never main).",
           _obj({"branch": _S, "from_ref": _S}, ["branch"]), access=Access.BRANCH, kind=ApprovalKind.PLAN),
        _t(c, "commit", vendor, "Commit files to a work branch.",
           _obj({"branch": _S, "message": _S, "files": {"type": "object", "description": "path -> content"}},
                ["branch", "message", "files"]), access=Access.COMMIT, kind=ApprovalKind.PLAN),
        _t(c, "push", vendor, "Push the previewed branch (needs PR approval).", _obj({"branch": _S}, ["branch"]),
           access=Access.PUSH, kind=ApprovalKind.PR),
        _t(c, "open_pr", vendor, "Open the previewed PR (needs PR approval).",
           _obj({"branch": _S, "base": _S, "title": _S, "body": _S}, ["branch", "title", "body"]),
           access=Access.PR, kind=ApprovalKind.PR),
        _t(c, "merge_pr", vendor, "Merge a PR. Per-action approval of exactly this PR number.",
           _obj({"pr_number": {"type": "integer"}}, ["pr_number"]), access=Access.MERGE,
           kind=ApprovalKind.MERGE, art="pr_number"),
        _t(c, "delete_branch", vendor, "Delete a branch. Per-action approval of exactly this branch.",
           _obj({"branch": _S}, ["branch"]), access=Access.DELETE, kind=ApprovalKind.DESTRUCTIVE, art="branch"),
    ]


def design(vendor: str) -> list[ToolSpec]:
    return [_t("design", "get", vendor, "Read a design file/frame (structure, specs, comments).",
               _obj({"url": _S}, ["url"]))]


def execution(vendor: str) -> list[ToolSpec]:
    c = "execution"
    sch = _obj({"paths": {"type": "array", "items": _S}})
    return [
        _t(c, "run_tests", vendor, "Run test suites in the sandbox. Returns {passed: bool, ...}.", sch,
           access=Access.EXECUTE),
        _t(c, "run_lint", vendor, "Run linters. Returns {passed: bool, ...}.", sch, access=Access.EXECUTE),
        _t(c, "run_typecheck", vendor, "Run type checks. Returns {passed: bool, ...}.", sch, access=Access.EXECUTE),
    ]


_Q = {"service": _S, "namespace": _S, "labels": {"type": "object"},
      "time_range": {"type": "string", "description": "e.g. '2h' or ISO start/end"}, "query": _S, "step": _S}


def metrics(vendor: str, env: str) -> list[ToolSpec]:
    return [_t("metrics", "query", vendor, "Bounded metrics query (aggregate first).",
               _obj(_Q, ["time_range"]), env=env, bounded=True)]


def logs(vendor: str, env: str) -> list[ToolSpec]:
    return [_t("logs", "query", vendor, "Bounded log query; prefer aggregates/counts before raw lines.",
               _obj({**_Q, "limit": {"type": "integer", "maximum": 200}}, ["time_range"]), env=env, bounded=True)]


def cluster(vendor: str, env: str) -> list[ToolSpec]:
    return [_t("cluster", "events", vendor, "Cluster events (restarts, OOMKills, scaling, node pressure).",
               _obj(_Q, ["time_range"]), env=env, bounded=True)]


def traces(vendor: str, env: str) -> list[ToolSpec]:
    return [_t("traces", "search", vendor, "Bounded trace search.", _obj(_Q, ["time_range"]), env=env,
               bounded=True)]


def docs(vendor: str) -> list[ToolSpec]:
    return [_t("docs", "write_postmortem", vendor, "Write a blameless postmortem. Needs approved artifact "
               "equal to `content`.", _obj({"title": _S, "content": _S}, ["title", "content"]),
               access=Access.WRITE, kind=ApprovalKind.POSTMORTEM, art="content")]
