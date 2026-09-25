"""Validation + normalisation of everything a tenant admin can configure. Returns (config, secret_writes).
Secret values never enter the config document — only their names."""
from __future__ import annotations

import re
import shlex
from typing import Any

from ..core import flags
from ..core.errors import SupdevError
from ..core.models import Access, ApprovalKind
from ..core.netguard import check_url

CAPABILITIES = ["ticketing", "source_control", "design", "execution", "metrics", "logs", "cluster", "traces", "docs"]
INTEGRATIONS = ["jira", "github", "figma", "grafana", "loki", "rancher", "local_exec", "mcp"]
_ENV = re.compile(r"^[a-z0-9][a-z0-9_-]{0,19}$")
_SRV = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_TOOL = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ENVVAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")

Result = tuple[dict[str, Any], dict[str, str]]


def _s(d: dict[str, Any], k: str, required: bool = False, max_len: int = 300) -> str | None:
    v = d.get(k)
    if v in (None, ""):
        if required:
            raise SupdevError(f"'{k}' is required")
        return None
    if not isinstance(v, str) or len(v) > max_len:
        raise SupdevError(f"'{k}' must be a string up to {max_len} chars")
    return v.strip()


def _secrets(sec: dict[str, str], mapping: dict[str, str]) -> dict[str, str]:
    """mapping: form-key -> stored secret name. Blank values mean 'unchanged'."""
    return {mapping[k]: v for k, v in sec.items() if k in mapping and v}


def _env(v: Any) -> str:
    if not isinstance(v, str) or not _ENV.match(v):
        raise SupdevError("environment must be lowercase letters/digits/-/_ (e.g. dev, staging, prod)")
    return v


def jira(p: dict[str, Any], sec: dict[str, str]) -> Result:
    cfg = {"base_url": check_url(_s(p, "base_url", True) or ""), "secret": "jira_token", "email_secret": "jira_email"}
    if proj := _s(p, "project", max_len=40):
        cfg["project"] = proj
    return cfg, _secrets(sec, {"token": "jira_token", "email": "jira_email"})


_GH_URL = re.compile(r"^(?:https?://(?:www\.)?github\.com/|git@github\.com:|ssh://git@github\.com/|github\.com/)"
                     r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?(?:[/?#].*)?$")


def normalize_repo(v: str) -> str:
    """'org/name', 'org/name.git', 'https://github.com/org/name(.git)', 'git@github.com:org/name.git' -> 'org/name'."""
    v = v.strip()
    if m := _GH_URL.match(v):
        return f"{m[1]}/{m[2]}"
    v = v.removesuffix(".git").strip("/")
    if _REPO.match(v):
        return v
    raise SupdevError("Repository must be 'org/name' or a github.com URL such as https://github.com/org/name "
                      "(other Git hosts aren't supported yet)")


def github(p: dict[str, Any], sec: dict[str, str]) -> Result:
    repo = normalize_repo(_s(p, "repo", True) or "")
    cfg: dict[str, Any] = {"repo": repo, "secret": "github_token"}
    if b := _s(p, "default_branch", max_len=100):
        if not _BRANCH.match(b):
            raise SupdevError("invalid default_branch")
        cfg["default_branch"] = b
    if cu := _s(p, "clone_url", max_len=300):
        if not cu.startswith("https://"):
            raise SupdevError("clone_url must be https://")
        cfg["clone_url"] = check_url(cu)
    if p.get("workdir"):
        raise SupdevError("workdir is managed by the platform (SUPDEV_WORKDIR_ROOT) and cannot be set here")
    return cfg, _secrets(sec, {"token": "github_token"})


def figma(p: dict[str, Any], sec: dict[str, str]) -> Result:
    return {"secret": "figma_token"}, _secrets(sec, {"token": "figma_token"})


def _instances(name: str, p: dict[str, Any], sec: dict[str, str], extra: str) -> Result:
    items = p.get("instances")
    if not isinstance(items, list) or not items or len(items) > 10:
        raise SupdevError("add 1–10 instances")
    out, writes, seen = [], {}, set()
    for it in items:
        env = _env(it.get("env"))
        if env in seen:
            raise SupdevError(f"duplicate environment '{env}'")
        seen.add(env)
        inst = {"env": env, "url": check_url(_s(it, "url", True) or ""), "secret": f"{name}_{env}_token"}
        if (v := _s(it, extra, True, 100)) and not re.match(r"^[A-Za-z0-9_.:-]+$", v):
            raise SupdevError(f"invalid {extra}")
        inst[extra] = v
        out.append(inst)
        if sec.get(env):
            writes[f"{name}_{env}_token"] = sec[env]
    return {"instances": out}, writes


def grafana(p: dict[str, Any], sec: dict[str, str]) -> Result:
    return _instances("grafana", p, sec, "datasource_uid")


def rancher(p: dict[str, Any], sec: dict[str, str]) -> Result:
    return _instances("rancher", p, sec, "cluster_id")


def loki(p: dict[str, Any], sec: dict[str, str]) -> Result:
    items = p.get("instances")
    if not isinstance(items, list) or not items or len(items) > 10:
        raise SupdevError("add 1–10 instances")
    out, writes = [], {}
    for it in items:
        env = _env(it.get("env"))
        out.append({"env": env, "url": check_url(_s(it, "url", True) or ""), "secret": f"loki_{env}_token"})
        if sec.get(env):
            writes[f"loki_{env}_token"] = sec[env]
    return {"instances": out}, writes


def local_exec(p: dict[str, Any], sec: dict[str, str]) -> Result:
    if not flags.allow_local_exec():
        raise SupdevError("local test execution runs repository code on the platform host and is disabled. "
                          "The platform operator must set SUPDEV_ALLOW_LOCAL_EXEC=1 (and run in a sandbox).")
    cmds = p.get("commands")
    if not isinstance(cmds, dict) or not cmds or set(cmds) - {"tests", "lint", "typecheck"}:
        raise SupdevError("commands must contain some of: tests, lint, typecheck")
    for k, v in cmds.items():
        if not isinstance(v, str) or not v.strip() or len(v) > 300:
            raise SupdevError(f"command '{k}' must be a non-empty string")
        shlex.split(v)  # must parse
    return {"commands": {k: v.strip() for k, v in cmds.items()}}, {}


def validate_mcp_server(s: dict[str, Any], values: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    name = s.get("name")
    if not isinstance(name, str) or not _SRV.match(name):
        raise SupdevError("server name: lowercase letters/digits/-/_")
    srv: dict[str, Any] = {"name": name}
    writes: dict[str, str] = {}
    cmd, url = _s(s, "command", max_len=200), _s(s, "url", max_len=300)
    if bool(cmd) == bool(url):
        raise SupdevError("set exactly one of command (stdio) or url (streamable HTTP)")
    if cmd:
        if not flags.allow_stdio_mcp():
            raise SupdevError("stdio MCP servers spawn a process on the platform host and are disabled. The operator "
                              "must set SUPDEV_ALLOW_STDIO_MCP=1 and SUPDEV_MCP_ALLOWED_COMMANDS=<comma list>. "
                              "Use a remote (url) server instead.")
        if cmd not in flags.mcp_allowed_commands():
            raise SupdevError(f"command '{cmd}' is not in SUPDEV_MCP_ALLOWED_COMMANDS")
        args = s.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) and len(a) < 300 for a in args) or len(args) > 30:
            raise SupdevError("args must be a list of short strings")
        srv.update(command=cmd, args=args)
        m = s.get("env_secrets") or {}
        if not isinstance(m, dict) or not all(_ENVVAR.match(k) for k in m):
            raise SupdevError("env_secrets keys must be environment variable names")
        srv["env_secrets"] = {k: f"mcp_{name}_{k}" for k in m}
        writes.update({f"mcp_{name}_{k}": values[k] for k in m if values.get(k)})
    else:
        srv["url"] = check_url(url or "")
        m = s.get("header_secrets") or {}
        if not isinstance(m, dict) or not all(re.match(r"^[A-Za-z0-9-]{1,60}$", k) for k in m):
            raise SupdevError("header_secrets keys must be HTTP header names")
        srv["header_secrets"] = {k: f"mcp_{name}_{k}" for k in m}
        writes.update({f"mcp_{name}_{k}": values[k] for k in m if values.get(k)})
    tools: dict[str, Any] = {}
    for tname, t in (s.get("tools") or {}).items():
        if not _TOOL.match(tname) or "__" in tname:
            raise SupdevError(f"tool name '{tname}' is not allowed")
        cap = t.get("capability")
        if cap not in CAPABILITIES:
            raise SupdevError(f"{tname}: capability must be one of {', '.join(CAPABILITIES)}")
        try:
            access = Access(t.get("access", "read"))
        except ValueError as exc:
            raise SupdevError(f"{tname}: unknown access level") from exc
        env = _env(t["environment"]) if t.get("environment") else None
        if env in ("prod", "production", "prd") and access != Access.READ:
            raise SupdevError(f"{tname}: production tools can only be 'read' (prod is read-only)")
        kind = t.get("approval_kind") or None
        if kind:
            try:
                ApprovalKind(kind)
            except ValueError as exc:
                raise SupdevError(f"{tname}: unknown approval_kind") from exc
        if access not in (Access.READ, Access.EXECUTE) and not kind:
            raise SupdevError(f"{tname}: write tools need an approval_kind (the gateway refuses ungated writes)")
        tools[tname] = {"capability": cap, "access": access.value, "environment": env, "bounded": bool(t.get("bounded")),
                        "approval_kind": kind, "artifact_arg": _s(t, "artifact_arg", max_len=60),
                        "description": (_s(t, "description", max_len=300) or ""),
                        "input_schema": t.get("input_schema") if isinstance(t.get("input_schema"), dict) else None}
    srv["tools"] = tools
    return srv, writes


def mcp(p: dict[str, Any], sec: dict[str, str]) -> Result:
    servers, writes, names = [], {}, set()
    for s in p.get("servers", []):
        vals = {k.split(".", 1)[1]: v for k, v in sec.items() if k.startswith(f"{s.get('name')}.")}
        srv, w = validate_mcp_server(s, vals)
        if srv["name"] in names:
            raise SupdevError("duplicate server name")
        names.add(srv["name"])
        servers.append(srv)
        writes.update(w)
    if len(servers) > 10:
        raise SupdevError("at most 10 MCP servers")
    return {"servers": servers}, writes


VALIDATORS = {"jira": jira, "github": github, "figma": figma, "grafana": grafana, "loki": loki,
              "rancher": rancher, "local_exec": local_exec, "mcp": mcp}


def required_secrets(name: str, cfg: dict[str, Any]) -> list[str]:
    if name == "jira":
        return ["jira_token", "jira_email"]
    if name in ("github", "figma"):
        return [cfg["secret"]]
    if name in ("grafana", "loki", "rancher"):
        return [i["secret"] for i in cfg.get("instances", [])]
    return []
