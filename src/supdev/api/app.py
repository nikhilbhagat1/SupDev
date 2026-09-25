"""REST + SSE API and static web UI."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..core.approvals import ApprovalService
from ..core.errors import ApprovalError, PolicyViolation, SupdevError
from ..core.events import Event
from ..core.models import Principal, Session
from ..core.runtime import AgentRuntime
from ..plugins.base import Authenticator, PluginKind
from .config import build_runtime

WEB = Path(__file__).resolve().parent.parent / "web"


class MessageIn(BaseModel):
    text: str
    ticket_type: str | None = None
    ref: str | None = None


class GrantIn(BaseModel):
    seen_hash: str | None = None


class ModeIn(BaseModel):
    mode: str
    ref: str | None = None
    title: str = ""


class BudgetIn(BaseModel):
    kind: str
    n: int


def create_app(runtime: AgentRuntime | None = None, auth: Authenticator | None = None) -> FastAPI:
    if runtime is None:
        runtime, auth = build_runtime()
    assert auth is not None
    rt, authn = runtime, auth
    app = FastAPI(title="Supdev", version="0.1.0")
    locks: dict[str, asyncio.Lock] = {}

    def who(request: Request) -> Principal:
        try:
            return authn.authenticate(dict(request.headers))
        except PolicyViolation as exc:
            raise HTTPException(401, str(exc)) from exc

    def load(p: Principal, sid: str) -> Session:
        try:
            return rt.get_session(p, sid)
        except PolicyViolation as exc:
            raise HTTPException(404, "session not found") from exc

    def view(p: Principal, s: Session) -> dict[str, Any]:
        wi = s.active
        out: dict[str, Any] = {
            "id": s.id, "messages": [{"role": m.role, "text": m.text, "ts": m.ts} for m in s.messages
                                     if m.role in ("user", "assistant") and m.text],
            "awaiting_router": s.pending_router_question, "parked": [
                {"id": w.id, "mode": w.mode, "title": w.title, "status": w.status} for w in s.parked],
            "budget": {k: {"used": s.budget_used.get(k, 0)} for k in ("tool_calls", "query_units", "tokens")},
            "active": None}
        if wi:
            mode = rt.registry.get(PluginKind.MODE, wi.mode)
            svc = ApprovalService(rt.policy_for(rt._tenant(p)))
            out["active"] = {
                "id": wi.id, "mode": wi.mode, "title": wi.title, "ref": wi.ref, "phase": wi.phase + 1,
                "phases": [ph.title for ph in mode.phases()],
                "pending_approvals": [{
                    "id": a.id, "kind": a.kind.value, "hash": a.artifact_hash,
                    "content": wi.artifacts.get(a.kind.value),
                    "roles": svc.policy.roles_for(a.kind),
                    "can_approve": svc.policy.can_approve(p.role, a.kind)} for a in svc.pending(wi)],
                "evidence": [e.model_dump() for e in wi.evidence],
                "requirements": [r.model_dump() for r in wi.requirements],
            }
        return out

    async def stream(coro_factory: Any, s_id: str) -> AsyncIterator[dict[str, str]]:
        q: asyncio.Queue[Event | None] = asyncio.Queue()
        lock = locks.setdefault(s_id, asyncio.Lock())

        async def run() -> None:
            async with lock:
                try:
                    await coro_factory(q.put_nowait)
                except (PolicyViolation, ApprovalError) as exc:
                    q.put_nowait(Event(type="error", data={"message": str(exc)}))
                except Exception as exc:  # noqa: BLE001
                    detail = getattr(exc, "message", None) or str(exc)  # provider SDK errors carry a clean .message
                    q.put_nowait(Event(type="error", data={
                        "message": rt.redactor.redact(f"{type(exc).__name__}: {detail}")[0][:400]}))
                finally:
                    q.put_nowait(None)

        task = asyncio.create_task(run())
        try:
            while (ev := await q.get()) is not None:
                yield {"event": ev.type, "data": json.dumps(ev.data, default=str)}
        finally:
            await task

    @app.get("/api/auth/mode")
    def auth_mode() -> dict[str, str]:
        return {"mode": getattr(authn, "mode", "custom")}

    @app.get("/api/me")
    def me(p: Principal = Depends(who)) -> dict[str, str]:
        return {"tenant_id": p.tenant_id, "user_id": p.user_id, "role": p.role.value}

    if rt.settings is not None:
        from .admin import build_router
        app.include_router(build_router(rt, who))

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/sessions")
    def create_session(p: Principal = Depends(who)) -> dict[str, str]:
        return {"id": rt.create_session(p).id}

    @app.get("/api/sessions")
    def list_sessions(p: Principal = Depends(who)) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        svc = ApprovalService(rt.policy_for(rt._tenant(p)))
        for s in rt.store.list(p.tenant_id, p.user_id):
            wi = s.active
            item: dict[str, Any] = {
                "id": s.id, "title": (s.active.title if s.active and s.active.title else
                                      next((m.text for m in s.messages if m.role == "user"), "New chat"))[:80],
                "mode": wi.mode if wi else None, "ref": wi.ref if wi else None, "created_at": s.created_at,
                "phase": None, "phase_title": None, "pending": 0, "column": "todo"}
            if wi:
                phases = [ph.title for ph in rt.registry.get(PluginKind.MODE, wi.mode).phases()]
                item.update(phase=wi.phase + 1, phase_total=len(phases), phase_title=phases[wi.phase],
                            pending=len(svc.pending(wi)))
                item["column"] = ("approval" if item["pending"] else "review" if wi.phase == len(phases) - 1
                                  else "progress" if wi.phase > 0 else "todo")
            out.append(item)
        return out

    @app.get("/api/board")
    def board(mode: str, p: Principal = Depends(who)) -> dict[str, Any]:
        """Kanban data for one mode: a column per workflow phase; cards are that mode's work items (own sessions).
        Phases advance only through the agent's gated workflow, so the board is read-only (no drag between columns)."""
        if mode not in rt.modes():
            raise HTTPException(404, "unknown mode")
        phases = [ph.title for ph in rt.registry.get(PluginKind.MODE, mode).phases()]
        svc = ApprovalService(rt.policy_for(rt._tenant(p)))
        cards: list[dict[str, Any]] = []
        backlog: list[dict[str, Any]] = []
        for s in rt.store.list(p.tenant_id, p.user_id):
            items = ([s.active] if s.active else []) + s.parked
            if not items:
                backlog.append({"session_id": s.id, "created_at": s.created_at,
                                "title": next((m.text for m in s.messages if m.role == "user"), "New chat")[:80]})
                continue
            for wi in items:
                if wi.mode != mode:
                    continue
                cards.append({"session_id": s.id, "work_item_id": wi.id, "title": (wi.title or wi.ref or "Untitled")[:80],
                              "ref": wi.ref, "phase": wi.phase + 1, "status": "active" if wi is s.active else wi.status,
                              "pending": len(svc.pending(wi)), "created_at": s.created_at})
        return {"mode": mode, "phases": phases, "cards": cards, "backlog": backlog}

    releases_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}

    @app.get("/api/releases")
    async def releases(project: str | None = None, refresh: bool = False, p: Principal = Depends(who)) -> dict[str, Any]:
        """Jira releases for the board's side panel. Read-only, uses the tenant's own Jira integration, cached 60s."""
        adapter = next((a for a in rt.adapters_for(rt._tenant(p)) if hasattr(a, "releases")), None)
        if adapter is None:
            return {"configured": False, "releases": []}
        key = project or getattr(adapter, "project", None)
        if not key:
            return {"configured": True, "project": None, "releases": []}
        ck = (p.tenant_id, key.upper())
        hit = releases_cache.get(ck)
        if hit and not refresh and time.time() - hit[0] < 60:
            return hit[1]
        try:
            items = await asyncio.wait_for(adapter.releases(key), 25)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, rt.redactor.redact(getattr(exc, "message", None) or str(exc))[0][:300]) from exc
        clean = [{**it, "name": rt.redactor.redact(str(it["name"]))[0],  # external free text: redact before the browser
                  "description": rt.redactor.redact(str(it["description"]))[0]} for it in items]
        out = {"configured": True, "project": key.upper(), "releases": clean}
        releases_cache[ck] = (time.time(), out)
        return out

    @app.get("/api/releases/{vid}/issues")
    async def release_issues(vid: str, project: str | None = None, p: Principal = Depends(who)) -> dict[str, Any]:
        """Ticket keys in one Jira release — powers the board's Release dropdown (filters cards by ticket key)."""
        adapter = next((a for a in rt.adapters_for(rt._tenant(p)) if hasattr(a, "release_issues")), None)
        if adapter is None:
            raise HTTPException(404, "Jira is not connected")
        ck = (p.tenant_id, f"{(project or getattr(adapter, 'project', '') or '').upper()}#{vid}")
        hit = releases_cache.get(ck)
        if hit and time.time() - hit[0] < 60:
            return hit[1]
        try:
            out = await asyncio.wait_for(adapter.release_issues(vid, project), 25)
        except Exception as exc:  # noqa: BLE001
            code = 400 if isinstance(exc, SupdevError) and "invalid" in str(exc) else 502
            raise HTTPException(code, rt.redactor.redact(getattr(exc, "message", None) or str(exc))[0][:300]) from exc
        releases_cache[ck] = (time.time(), out)
        return out

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str, p: Principal = Depends(who)) -> dict[str, Any]:
        return view(p, load(p, sid))

    @app.post("/api/sessions/{sid}/messages")
    async def post_message(sid: str, body: MessageIn, p: Principal = Depends(who)) -> EventSourceResponse:
        load(p, sid)
        return EventSourceResponse(stream(lambda emit: rt.handle_message(
            p, sid, body.text, emit, ticket_type=body.ticket_type, ref=body.ref), sid))

    @app.post("/api/sessions/{sid}/approvals/{aid}/grant")
    def grant(sid: str, aid: str, body: GrantIn, p: Principal = Depends(who)) -> dict[str, Any]:
        s = load(p, sid)
        try:
            ap = rt.grant(p, s, aid, body.seen_hash)
        except ApprovalError as exc:
            raise HTTPException(403, str(exc)) from exc
        return {"granted": ap.kind.value, "by": p.user_id}

    @app.post("/api/sessions/{sid}/continue")
    async def cont(sid: str, p: Principal = Depends(who)) -> EventSourceResponse:
        load(p, sid)
        return EventSourceResponse(stream(lambda emit: rt.continue_turn(p, sid, emit), sid))

    @app.post("/api/sessions/{sid}/mode")
    def set_mode(sid: str, body: ModeIn, p: Principal = Depends(who)) -> dict[str, Any]:
        s = load(p, sid)
        try:
            rt.start_work_item(p, s, mode=body.mode, ref=body.ref, title=body.title)
        except PolicyViolation as exc:
            raise HTTPException(403, str(exc)) from exc
        rt.store.save(s)
        return view(p, s)

    @app.post("/api/sessions/{sid}/resume/{wid}")
    def resume(sid: str, wid: str, p: Principal = Depends(who)) -> dict[str, Any]:
        s = load(p, sid)
        try:
            rt.resume_work_item(p, s, wid)
        except PolicyViolation as exc:
            raise HTTPException(404, str(exc)) from exc
        rt.store.save(s)
        return view(p, s)

    @app.post("/api/sessions/{sid}/budget")
    def budget(sid: str, body: BudgetIn, p: Principal = Depends(who)) -> dict[str, str]:
        try:
            rt.extend_budget(p, load(p, sid), body.kind, body.n)
        except ApprovalError as exc:
            raise HTTPException(403, str(exc)) from exc
        return {"ok": "extended"}

    @app.get("/api/audit")
    def audit(session_id: str | None = None, p: Principal = Depends(who)) -> list[dict[str, Any]]:
        if p.role.value not in ("lead", "admin", "sre"):
            raise HTTPException(403, "audit log requires lead/admin/sre")
        return [r.model_dump() for r in rt.audit.query(p.tenant_id, session_id)]

    @app.get("/api/plugins")
    def plugins(p: Principal = Depends(who)) -> dict[str, list[str]]:
        return {k.value: rt.registry.names(k) for k in PluginKind}

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(WEB / "index.html")

        @app.get("/settings")
        def settings_page() -> FileResponse:
            return FileResponse(WEB / "settings.html")

    return app
