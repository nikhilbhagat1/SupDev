"""Encryption of tenant secrets at rest (Fernet / AES-128-CBC + HMAC). Ciphertext is bound to (tenant, name) so
a row cannot be swapped to another tenant/name and still decrypt."""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .errors import SupdevError

log = logging.getLogger(__name__)


class Crypto:
    def __init__(self, key: bytes | str) -> None:
        try:
            self._f = Fernet(key)
        except (ValueError, TypeError) as exc:
            raise SupdevError("SUPDEV_MASTER_KEY must be a urlsafe-base64 32-byte Fernet key "
                              "(generate: python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')") from exc

    def encrypt(self, tenant: str, name: str, value: str) -> str:
        return self._f.encrypt(f"{tenant}\0{name}\0{value}".encode()).decode()

    def decrypt(self, tenant: str, name: str, token: str) -> str:
        try:
            t, n, v = self._f.decrypt(token.encode()).decode().split("\0", 2)
        except (InvalidToken, ValueError) as exc:
            raise SupdevError("cannot decrypt secret (wrong SUPDEV_MASTER_KEY or corrupted row)") from exc
        if (t, n) != (tenant, name):
            raise SupdevError("secret row does not belong to this tenant/name")
        return v


def load_master_key() -> bytes:
    """SUPDEV_MASTER_KEY (preferred; inject from your secret manager) or a 0600 key file that is created on first run."""
    env = os.environ.get("SUPDEV_MASTER_KEY")
    if env:
        return env.encode()
    path = Path(os.environ.get("SUPDEV_MASTER_KEY_FILE", ".supdev_master_key"))
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    log.warning("generated a master key at %s — back it up and prefer SUPDEV_MASTER_KEY in production", path)
    return key
