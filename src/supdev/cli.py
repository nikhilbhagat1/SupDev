from __future__ import annotations

import argparse
import os
import sys

from .plugins.base import PluginKind
from .plugins.registry import default_registry

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def main() -> None:
    ap = argparse.ArgumentParser(prog="supdev")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plugins", help="list discovered plugins")
    sv = sub.add_parser("serve", help="run the API + web UI")
    sv.add_argument("--host", default=os.environ.get("SUPDEV_HOST", "127.0.0.1"))
    sv.add_argument("--port", type=int, default=int(os.environ.get("SUPDEV_PORT", "8000")))
    tk = sub.add_parser("token", help="create an API token (shown once)")
    tk.add_argument("--tenant", required=True)
    tk.add_argument("--user", required=True)
    tk.add_argument("--role", default="admin", choices=["viewer", "developer", "sre", "lead", "admin"])
    args = ap.parse_args()
    if args.cmd == "plugins":
        reg = default_registry()
        for kind in PluginKind:
            print(f"{kind.value}: {', '.join(reg.names(kind)) or '-'}")
    elif args.cmd == "token":
        from .auth.token import create_token
        from .core.db import get_database
        from .core.models import Role
        from .settings.store import SettingsStore, TenantDoc

        db = get_database()
        SettingsStore(db).create_if_absent(TenantDoc(id=args.tenant, name=args.tenant.title()))
        _, token = create_token(db, args.tenant, args.user, Role(args.role))
        print(token)
    elif args.cmd == "serve":
        if os.environ.get("SUPDEV_AUTH", "dev-header") == "dev-header" and args.host not in LOOPBACK:
            sys.exit("refusing to listen on a non-loopback address with dev-header auth (anyone could claim any role). "
                     "Set SUPDEV_AUTH=token, or bind to 127.0.0.1.")
        import uvicorn

        uvicorn.run("supdev.api.app:create_app", factory=True, host=args.host, port=args.port)
