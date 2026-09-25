"""GitHub source-control adapter: a LOCAL git clone does the work, the GitHub API opens/merges PRs.

Commits stay local until `push` — so the "ask before pushing" PR gate (D3) is real: nothing reaches the
remote before the human approves. Never force-pushes; protected branches are refused here too (defense in
depth: DevMode and the gateway enforce the same rules)."""
from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
from typing import Any

from .. import capabilities as cap
from ..core.errors import SupdevError
from ..core.models import ToolSpec
from ..plugins.base import SecretStore, TenantConfig
from .util import Http, workdir_for

MAX_FILE = 200_000
MAX_OUT = 20_000
DEFAULT_PROTECTED = {"main", "master", "develop", "trunk", "production"}


class GitHubAdapter:
    capability, vendor = "source_control", "github"

    def __init__(self, *, tenant_id: str, secrets: SecretStore, repo: str, workdir: str | Path,
                 clone_url: str | None = None, secret: str | None = "github_token", default_branch: str = "main",
                 protected: set[str] | None = None, identity: tuple[str, str] = ("Supdev Agent", "agent@supdev.local"),
                 api_url: str = "https://api.github.com") -> None:
        self.tenant_id, self.secrets, self.repo = tenant_id, secrets, repo
        self.workdir = Path(workdir)
        self.clone_url = clone_url or f"https://github.com/{repo}.git"
        self.secret, self.default_branch = secret, default_branch
        self.protected = (protected or set()) | DEFAULT_PROTECTED | {default_branch}
        self.identity = identity
        self.api = Http(api_url, tenant_id, secrets, auth="bearer", secret=secret)

    def tools(self) -> list[ToolSpec]:
        return cap.source_control("github")

    async def ping(self) -> str:
        d = await self.api.request("GET", f"/repos/{self.repo}")
        return f"repo reachable (default branch {d.get('default_branch')}, {'private' if d.get('private') else 'public'})"

    async def call(self, tool: str, a: dict[str, Any]) -> Any:
        return await getattr(self, f"_{tool.split('.', 1)[1]}")(a)

    # -- git plumbing --------------------------------------------------------------------
    def _env(self) -> dict[str, str]:
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.workdir.parent), "GIT_TERMINAL_PROMPT": "0",
               "GIT_CONFIG_NOSYSTEM": "1"}
        tok = self.secrets.get(self.tenant_id, self.secret) if self.secret else None
        if tok and self.clone_url.startswith("https://"):  # via env, never argv/URL
            b = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
            env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraheader",
                       GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {b}")
        return env

    async def _git(self, *args: str, cwd: Path | None = None) -> str:
        p = await asyncio.create_subprocess_exec(
            "git", "-c", f"user.name={self.identity[0]}", "-c", f"user.email={self.identity[1]}", *args,
            cwd=cwd or self.workdir, env=self._env(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), 120)
        if p.returncode != 0:
            raise SupdevError(f"git {args[0]} failed: {err.decode()[:300]}")
        return out.decode(errors="replace")

    async def _ensure(self) -> None:
        if not (self.workdir / ".git").exists():
            self.workdir.parent.mkdir(parents=True, exist_ok=True)
            await self._git("clone", self.clone_url, str(self.workdir), cwd=self.workdir.parent)

    def _safe(self, rel: str) -> Path:
        p = (self.workdir / rel).resolve()
        if not p.is_relative_to(self.workdir.resolve()) or ".git" in p.relative_to(self.workdir.resolve()).parts:
            raise SupdevError(f"path '{rel}' is outside the repository")
        return p

    def _no_protected(self, branch: str) -> None:
        if branch in self.protected or branch.startswith("release/"):
            raise SupdevError(f"'{branch}' is a protected branch; work on a feature branch")

    # -- reads ---------------------------------------------------------------------------
    async def _read_file(self, a: dict[str, Any]) -> Any:
        await self._ensure()
        self._safe(a["path"])
        return (await self._git("show", f"{a.get('ref') or 'HEAD'}:{a['path']}"))[:MAX_FILE]

    async def _search_code(self, a: dict[str, Any]) -> Any:
        await self._ensure()
        try:
            return (await self._git("grep", "-n", "-I", "-F", "--max-count=5", "-e", a["query"]))[:MAX_OUT]
        except SupdevError:
            return ""  # git grep exits 1 on no match

    async def _get_diff(self, a: dict[str, Any]) -> Any:
        await self._ensure()
        return (await self._git("diff", f"{a.get('base') or 'origin/' + self.default_branch}...{a.get('head') or 'HEAD'}"))[:MAX_OUT]

    # -- gated writes --------------------------------------------------------------------
    async def _create_branch(self, a: dict[str, Any]) -> Any:
        await self._ensure()
        b = a["branch"]
        self._no_protected(b)
        await self._git("check-ref-format", "--branch", b)
        await self._git("fetch", "origin")
        await self._git("switch", "-c", b, a.get("from_ref") or f"origin/{self.default_branch}")
        return {"branch": b}

    async def _commit(self, a: dict[str, Any]) -> Any:
        await self._ensure()
        b = a["branch"]
        self._no_protected(b)
        await self._git("switch", b)
        for rel, content in a["files"].items():
            p = self._safe(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        await self._git("add", "--", *[str(self._safe(r).relative_to(self.workdir.resolve())) for r in a["files"]])
        await self._git("commit", "-m", a["message"])
        return {"sha": (await self._git("rev-parse", "HEAD")).strip(), "branch": b, "pushed": False}

    async def _push(self, a: dict[str, Any]) -> Any:
        await self._ensure()
        self._no_protected(a["branch"])
        await self._git("push", "origin", f"{a['branch']}:{a['branch']}")  # never --force
        return {"pushed": a["branch"]}

    async def _open_pr(self, a: dict[str, Any]) -> Any:
        d = await self.api.request("POST", f"/repos/{self.repo}/pulls", json={
            "title": a["title"], "head": a["branch"], "base": a.get("base") or self.default_branch, "body": a["body"]})
        return {"number": d["number"], "url": d["html_url"]}

    async def _merge_pr(self, a: dict[str, Any]) -> Any:
        d = await self.api.request("PUT", f"/repos/{self.repo}/pulls/{a['pr_number']}/merge", json={})
        return {"merged": d.get("merged", True)}

    async def _delete_branch(self, a: dict[str, Any]) -> Any:
        self._no_protected(a["branch"])
        await self.api.request("DELETE", f"/repos/{self.repo}/git/refs/heads/{a['branch']}")
        return {"deleted": a["branch"]}


class GitHubFactory:
    def create(self, tenant: TenantConfig, secrets: SecretStore) -> list[Any]:
        c = tenant.integrations["github"]
        return [GitHubAdapter(
            tenant_id=tenant.tenant_id, secrets=secrets, repo=c["repo"], clone_url=c.get("clone_url"),
            workdir=c.get("workdir") or workdir_for(tenant.tenant_id, c["repo"]),
            secret=c.get("secret", "github_token"), default_branch=c.get("default_branch", "main"),
            protected=set(c.get("protected", [])))]
