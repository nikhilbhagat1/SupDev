"""Tenant admin API (Settings page). All routes: authenticated, tenant-scoped to the caller, ADMIN role for writes.

Secrets are write-only: they go straight into the encrypted store; responses only ever say which secret *names* are
configured. Every change is audited (names/keys only — never values)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..adapters.mcp_bridge import list_remote_tools
from ..auth.token import create_token, list_tokens, revoke_token
from ..core import flags
from ..core.audit import AuditRecord
from ..core.errors import SupdevError
from ..core.models import Access, ApprovalKind, Message, Principal, Role
from ..core.runtime import AgentRuntime
from ..plugins.base import PluginKind, TenantConfig
from ..secrets.db import NAME, OverlaySecrets
from ..settings import validate as V
from ..settings.store import LLMDoc, PolicyDoc, TenantDoc

TESTABLE_TIMEOUT = 20


class GeneralIn(BaseModel):
    name: str = Field(max_length=100)
    environments: list[str] = Field(min_length=1, max_length=8)
    timezone: str = Field(default="UTC", max_length=60)
    repo_context: str = Field(default="", max_length=4000)
    conventions: str = Field(default="", max_length=4000)
    service_catalog: str = Field(default="", max_length=8000)


class LLMIn(BaseModel):
    provider: str | None = None  # None => platform default
    model: str | None = Field(default=None, max_length=100)
    api_key: str | None = Field(default=None, max_length=8192)  # write-only; blank = keep existing


class IntegrationIn(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)  # form-key -> value (blank/absent = unchanged)


class DiscoverIn(BaseModel):
    server: dict[str, Any]
    secrets: dict[str, str] = Field(default_factory=dict)


class TokenIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=80)
    role: Role


def build_router(rt: AgentRuntime, who: Callable[..., Principal]) -> APIRouter:
    r = APIRouter(prefix="/api/admin")
    store, secrets, db = rt.settings, rt.secrets, rt.settings.db if rt.settings else None

    def admin(p: Principal = Depends(who)) -> Principal:
        if p.role != Role.ADMIN:
            raise HTTPException(403, "requires the admin role")
        return p

    def viewer(p: Principal = Depends(who)) -> Principal:
        if p.role not in (Role.ADMIN, Role.LEAD):
            raise HTTPException(403, "requires the admin or lead role")
        return p

    def audit(p: Principal, action: str, detail: dict[str, Any]) -> None:
        rt.audit.write(AuditRecord(tenant_id=p.tenant_id, session_id="-", user_id=p.user_id, kind="admin_config",
                                   action=action, detail=detail))

    def learn(values: list[str]) -> None:
        for v in values:
            rt.redactor.add_known_secret(v)

    def store_secrets(p: Principal, writes: dict[str, str]) -> None:
        try:
            for name, value in writes.items():
                secrets.set(p.tenant_id, name, value)
        except SupdevError as exc:
            raise HTTPException(400, str(exc)) from exc
        learn(list(writes.values()))

    def update(p: Principal, fn: Callable[[TenantDoc], None]) -> TenantDoc:
        try:
            return store.update(p.tenant_id, fn)
        except KeyError as exc:
            raise HTTPException(404, "tenant is not managed by the settings store") from exc

    def masked(p: Principal) -> dict[str, Any]:
        d = store.get(p.tenant_id)
        if d is None:
            raise HTTPException(404, "tenant is not managed by the settings store")
        configured = set(secrets.names(p.tenant_id))
        integ: dict[str, Any] = {}
        for name, cfg in d.integrations.items():
            integ[name] = {"config": cfg, "missing_secrets": [s for s in V.required_secrets(name, cfg)
                                                              if s not in configured]}
            if name == "mcp":
                integ[name]["missing_secrets"] = [
                    s for srv in cfg.get("servers", []) for s in
                    [*srv.get("env_secrets", {}).values(), *srv.get("header_secrets", {}).values()]
                    if s not in configured]
        return {
            "tenant": d.model_dump(exclude={"integrations"}), "integrations": integ,
            "llm_key_configured": "llm_api_key" in configured,
            "secrets_configured": sorted(configured),
            "options": {
                "integrations": V.INTEGRATIONS, "capabilities": V.CAPABILITIES,
                "access": [a.value for a in Access], "approval_kinds": [k.value for k in ApprovalKind],
                "modes": rt.registry.names(PluginKind.MODE), "llm_providers": rt.registry.names(PluginKind.LLM),
                "roles": [x.value for x in Role]},
            "platform": {"stdio_mcp_allowed": flags.allow_stdio_mcp(), "local_exec_allowed": flags.allow_local_exec(),
                         "private_urls_allowed": flags.allow_private_urls(),
                         "mcp_allowed_commands": sorted(flags.mcp_allowed_commands())},
        }

    # ------------------------------------------------------------------------ read
    @r.get("/config")
    def get_config(p: Principal = Depends(viewer)) -> dict[str, Any]:
        return masked(p)

    # ---------------------------------------------------------------------- general
    @r.put("/general")
    def put_general(body: GeneralIn, p: Principal = Depends(admin)) -> dict[str, Any]:
        for e in body.environments:
            try:
                V._env(e)
            except SupdevError as exc:
                raise HTTPException(400, str(exc)) from exc

        def fn(d: TenantDoc) -> None:
            for k, v in body.model_dump().items():
                setattr(d, k, v)
        update(p, fn)
        audit(p, "general.update", {"fields": list(body.model_dump())})
        return masked(p)

    # ----------------------------------------------------------------------- policy
    @r.put("/policy")
    def put_policy(body: PolicyDoc, p: Principal = Depends(admin)) -> dict[str, Any]:
        modes = set(rt.registry.names(PluginKind.MODE))
        if body.enabled_modes is not None and (not body.enabled_modes or set(body.enabled_modes) - modes):
            raise HTTPException(400, f"enabled_modes must be a non-empty subset of {sorted(modes)}")
        if bad := [a for a in body.denied_access if a not in {x.value for x in Access}]:
            raise HTTPException(400, f"unknown access levels: {bad}")
        if any(v <= 0 for v in body.budget.values()):
            raise HTTPException(400, "budget values must be positive")
        update(p, lambda d: setattr(d, "policy", body))  # merged with platform policy at use → can only tighten
        audit(p, "policy.update", {"policy": body.model_dump()})
        return masked(p)

    # -------------------------------------------------------------------------- LLM
    @r.put("/llm")
    def put_llm(body: LLMIn, p: Principal = Depends(admin)) -> dict[str, Any]:
        if body.provider and body.provider not in rt.registry.names(PluginKind.LLM):
            raise HTTPException(400, "unknown provider")
        if body.provider and not body.api_key and "llm_api_key" not in secrets.names(p.tenant_id):
            raise HTTPException(400, "an API key is required the first time you pick a provider")
        if body.api_key:
            store_secrets(p, {"llm_api_key": body.api_key})
        update(p, lambda d: setattr(d, "llm", LLMDoc(provider=body.provider, model=body.model or None)))
        audit(p, "llm.update", {"provider": body.provider, "model": body.model, "key_changed": bool(body.api_key)})
        return masked(p)

    @r.delete("/llm/key")
    def delete_llm_key(p: Principal = Depends(admin)) -> dict[str, Any]:
        secrets.delete(p.tenant_id, "llm_api_key")
        update(p, lambda d: setattr(d, "llm", LLMDoc()))
        audit(p, "llm.key_deleted", {})
        return masked(p)

    @r.post("/llm/test")
    async def test_llm(p: Principal = Depends(admin)) -> dict[str, Any]:
        try:
            llm = rt.llm_for(rt.tenant_by_id(p.tenant_id))
            resp = await asyncio.wait_for(llm.complete("Reply with the single word OK.",
                                                       [Message(role="user", text="ping")], []), 30)
            return {"ok": True, "detail": (resp.text or "")[:80], "tokens": resp.usage.input_tokens + resp.usage.output_tokens}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": rt.redactor.redact(f"{type(exc).__name__}: {exc}")[0][:300]}

    # ----------------------------------------------------------------- integrations
    @r.put("/integrations/{name}")
    def put_integration(name: str, body: IntegrationIn, p: Principal = Depends(admin)) -> dict[str, Any]:
        if name not in V.VALIDATORS:
            raise HTTPException(404, "unknown integration")
        try:
            cfg, writes = V.VALIDATORS[name](body.config, body.secrets)
        except SupdevError as exc:
            raise HTTPException(400, str(exc)) from exc
        store_secrets(p, writes)
        update(p, lambda d: d.integrations.__setitem__(name, cfg))
        audit(p, "integration.update", {"integration": name, "secrets_written": sorted(writes)})
        return masked(p)

    @r.delete("/integrations/{name}")
    def delete_integration(name: str, p: Principal = Depends(admin)) -> dict[str, Any]:
        update(p, lambda d: d.integrations.pop(name, None))
        audit(p, "integration.delete", {"integration": name})
        return masked(p)

    @r.post("/integrations/{name}/test")
    async def test_integration(name: str, p: Principal = Depends(admin)) -> dict[str, Any]:
        d = store.get(p.tenant_id)
        if d is None or name not in d.integrations:
            raise HTTPException(404, "integration not configured")
        results: list[dict[str, Any]] = []
        try:
            factory = rt.registry.get(PluginKind.CAPABILITY, name)
            adapters = factory.create(TenantConfig(p.tenant_id, {name: d.integrations[name]}), secrets)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "results": [{"ok": False, "detail": rt.redactor.redact(str(exc))[0][:300]}]}
        for ad in adapters:
            label = f"{ad.vendor}" + (f" ({getattr(ad, 'env', '')})" if getattr(ad, "env", "") else "")
            try:
                detail = await asyncio.wait_for(ad.ping(), TESTABLE_TIMEOUT) if hasattr(ad, "ping") else "configured"
                results.append({"adapter": label, "ok": True, "detail": detail})
            except Exception as exc:  # noqa: BLE001
                results.append({"adapter": label, "ok": False,
                                "detail": rt.redactor.redact(f"{type(exc).__name__}: {exc}")[0][:300]})
        return {"ok": all(x["ok"] for x in results), "results": results}

    # -------------------------------------------------------------------------- MCP
    @r.post("/mcp/discover")
    async def discover(body: DiscoverIn, p: Principal = Depends(admin)) -> dict[str, Any]:
        try:
            srv, _ = V.validate_mcp_server({**body.server, "tools": {}}, body.secrets)
        except SupdevError as exc:
            raise HTTPException(400, str(exc)) from exc
        name = srv["name"]
        overlay = OverlaySecrets(secrets, {f"mcp_{name}_{k}": v for k, v in body.secrets.items() if v})
        try:
            tools = await asyncio.wait_for(list_remote_tools(p.tenant_id, overlay, srv), TESTABLE_TIMEOUT + 20)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, rt.redactor.redact(f"{type(exc).__name__}: {exc}")[0][:300]) from exc
        audit(p, "mcp.discover", {"server": name, "tools": len(tools)})
        return {"tools": tools, "note": "Nothing is exposed to the agent until you tag a tool and save."}

    # ----------------------------------------------------------------------- secrets
    @r.delete("/secrets/{name}")
    def delete_secret(name: str, p: Principal = Depends(admin)) -> dict[str, Any]:
        if not NAME.match(name):
            raise HTTPException(400, "invalid secret name")
        secrets.delete(p.tenant_id, name)
        audit(p, "secret.delete", {"name": name})
        return masked(p)

    # ------------------------------------------------------------------------ tokens
    @r.get("/tokens")
    def get_tokens(p: Principal = Depends(admin)) -> list[dict[str, Any]]:
        return list_tokens(db, p.tenant_id)  # type: ignore[arg-type,return-value]

    @r.post("/tokens")
    def post_token(body: TokenIn, p: Principal = Depends(admin)) -> dict[str, str]:
        tid, token = create_token(db, p.tenant_id, body.user_id, body.role)  # type: ignore[arg-type]
        audit(p, "token.create", {"id": tid, "user": body.user_id, "role": body.role.value})
        return {"id": tid, "token": token, "note": "shown once — copy it now"}

    @r.delete("/tokens/{tid}")
    def delete_token(tid: str, p: Principal = Depends(admin)) -> dict[str, str]:
        revoke_token(db, p.tenant_id, tid)  # type: ignore[arg-type]
        audit(p, "token.revoke", {"id": tid})
        return {"ok": "revoked"}

    return r

