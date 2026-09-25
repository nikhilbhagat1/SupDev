"""Builds the runtime from environment. Tenant configuration lives in the DB (edited via Settings); the JSON file
named by SUPDEV_CONFIG only *seeds* tenants that don't exist yet. Everything is a plugin lookup."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..auth.token import create_token, has_tokens
from ..core.db import get_database
from ..core.models import Role
from ..core.runtime import AgentRuntime
from ..llm.fake import FakeLLM, say
from ..plugins.base import PluginKind
from ..plugins.registry import default_registry
from ..settings.store import SettingsStore, TenantDoc


def build_runtime() -> tuple[AgentRuntime, Any]:
    """Env: SUPDEV_CONFIG (seed json), SUPDEV_LLM (platform default), SUPDEV_STORAGE, SUPDEV_AUDIT, SUPDEV_AUTH
    (dev-header|token), SUPDEV_SECRETS, SUPDEV_DB, SUPDEV_AUDIT_PATH, SUPDEV_PLUGINS, SUPDEV_MASTER_KEY(_FILE),
    SUPDEV_BOOTSTRAP_TOKEN / SUPDEV_BOOTSTRAP_TENANT (first admin token), plus operator flags in core/flags.py."""
    extra = dict(x.split("=", 1) for x in os.environ.get("SUPDEV_PLUGINS", "").split(",") if "=" in x)
    reg = default_registry(extra)
    llm_name = os.environ.get("SUPDEV_LLM") or ("anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "fake")
    llm = reg.get(PluginKind.LLM, llm_name)
    if isinstance(llm, FakeLLM):
        llm.script = [say("[demo] No LLM is configured. Add a key in Settings → LLM (or set ANTHROPIC_API_KEY).")] * 50
    db = get_database()
    store_name = os.environ.get("SUPDEV_STORAGE", "sqlite")
    store = reg.get(PluginKind.STORAGE, store_name)
    if store_name == "sqlite":
        from ..storage.sqlite import SqliteSessionStore
        store = SqliteSessionStore(os.environ.get("SUPDEV_DB", "supdev.db"))
    audit_name = os.environ.get("SUPDEV_AUDIT", "jsonl")
    audit = reg.get(PluginKind.AUDIT, audit_name)
    if audit_name == "jsonl":
        from ..core.audit import JsonlAuditSink
        audit = JsonlAuditSink(os.environ.get("SUPDEV_AUDIT_PATH", "audit.jsonl"))
    settings = SettingsStore(db)
    rt = AgentRuntime(reg, llm=llm, store=store, audit=audit, secrets=reg.get(PluginKind.SECRETS,
                      os.environ.get("SUPDEV_SECRETS", "db")), redactor=reg.get(PluginKind.REDACTOR, "regex"),
                      settings=settings)
    auth = reg.get(PluginKind.AUTH, os.environ.get("SUPDEV_AUTH", "dev-header"))

    if cfg := os.environ.get("SUPDEV_CONFIG"):  # seed only — the DB is the source of truth afterwards
        for t in json.loads(Path(cfg).read_text()).get("tenants", []):
            settings.create_if_absent(TenantDoc.model_validate({**t, "name": t.get("name", t["id"])}))
    boot_tenant = os.environ.get("SUPDEV_BOOTSTRAP_TENANT", "default")
    if not settings.ids():
        if getattr(auth, "mode", "") == "dev-header":
            settings.create_if_absent(TenantDoc(id="demo", name="Demo Tenant", integrations={"fake": {}}))
        else:
            settings.create_if_absent(TenantDoc(id=boot_tenant, name=boot_tenant.title()))
    if (tok := os.environ.get("SUPDEV_BOOTSTRAP_TOKEN")) and settings.get(boot_tenant) is not None \
            and not has_tokens(db, boot_tenant):
        create_token(db, boot_tenant, "admin", Role.ADMIN, raw=tok)
    return rt, auth
