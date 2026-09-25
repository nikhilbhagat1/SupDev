"""Runs the repo's own test/lint/typecheck commands in the workdir (non-prod only).

SECURITY: this executes repository code on the platform host. Run the platform in a sandbox/container with
no network credentials. The environment passed to the child is minimal — no platform secrets."""
from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path
from typing import Any

from .. import capabilities as cap
from ..core.errors import SupdevError
from ..core.models import ToolSpec
from ..plugins.base import SecretStore, TenantConfig
from .util import workdir_for


class LocalExecAdapter:
    capability, vendor = "execution", "local"

    def __init__(self, workdir: str | Path, commands: dict[str, str], timeout: float = 600) -> None:
        self.workdir, self.commands, self.timeout = Path(workdir), commands, timeout

    def tools(self) -> list[ToolSpec]:
        return [t for t in cap.execution("local") if t.name.split(".")[1].removeprefix("run_") in self.commands]

    async def ping(self) -> str:
        return f"commands: {', '.join(self.commands)} (workdir {'present' if self.workdir.exists() else 'not cloned yet'})"

    async def call(self, tool: str, a: dict[str, Any]) -> Any:
        key = tool.split(".")[1].removeprefix("run_")
        argv = shlex.split(self.commands[key])
        for p in a.get("paths") or []:
            if p.startswith("-") or not (self.workdir / p).resolve().is_relative_to(self.workdir.resolve()):
                raise SupdevError(f"bad path '{p}'")
            argv.append(p)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.workdir), "CI": "1"}
        proc = await asyncio.create_subprocess_exec(*argv, cwd=self.workdir, env=env, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), self.timeout)
        except TimeoutError:
            proc.kill()
            return {"passed": False, "exit_code": None, "output_tail": "timed out"}
        tail = "\n".join(out.decode(errors="replace").splitlines()[-60:])
        return {"passed": proc.returncode == 0, "exit_code": proc.returncode, "output_tail": tail}


class LocalExecFactory:
    def create(self, tenant: TenantConfig, secrets: SecretStore) -> list[Any]:
        c = tenant.integrations["local_exec"]
        gh = tenant.integrations.get("github") or {}
        workdir = c.get("workdir") or (workdir_for(tenant.tenant_id, gh["repo"]) if gh.get("repo") else None)
        if workdir is None:
            raise SupdevError("local_exec needs a github integration (it runs in the repository clone)")
        return [LocalExecAdapter(workdir, c["commands"], c.get("timeout", 600))]
