"""Outbound URL guard for tenant-configured endpoints (SSRF). Blocks loopback/private/link-local/metadata ranges
unless the OPERATOR set SUPDEV_ALLOW_PRIVATE_URLS. Note: checked at save time and on every request (DNS is
re-resolved), which narrows but does not fully eliminate DNS-rebinding races."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from . import flags
from .errors import SupdevError


def check_url(url: str) -> str:
    u = urlsplit(url.strip())
    if u.scheme not in ("https", "http") or (u.scheme == "http" and not flags.allow_http()):
        raise SupdevError("URL must be https:// (operator can allow http with SUPDEV_ALLOW_HTTP=1)")
    if not u.hostname or u.username or u.password:
        raise SupdevError("URL must have a host and no embedded credentials")
    if not flags.allow_private_urls():
        try:
            infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise SupdevError(f"cannot resolve host '{u.hostname}'") from exc
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                raise SupdevError(f"'{u.hostname}' resolves to a non-public address; the platform operator must "
                                  "set SUPDEV_ALLOW_PRIVATE_URLS=1 to allow internal endpoints")
    return url.strip().rstrip("/")
