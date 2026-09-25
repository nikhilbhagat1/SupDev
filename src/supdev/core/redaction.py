"""Secret + PII redaction (A4, A7). Applied to everything that leaves the engine."""

from __future__ import annotations

import re
from typing import Any

MASK = "****"

# (name, pattern, is_secret) — secrets are flagged as "exposed"; PII is just masked.
_PATTERNS: list[tuple[str, re.Pattern[str], bool]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), True),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), True),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b"), True),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b"), True),
    ("api_key_sk", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), True),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"), True),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), True),
    ("bearer", re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{16,}"), True),
    (
        "kv_secret",
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)"
            r"(\s*[=:]\s*)(['\"]?)([^\s'\",;]{4,})\3"
        ),
        True,
    ),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), False),
    ("card", re.compile(r"(?<![\w.-])(?:\d[ -]?){12,18}\d(?![\w-])"), False),  # Luhn-checked below (ids/epochs pass)
    # structured phone numbers only: dates (2026-10-14), IPs (10.0.0.1), versions and long ids must survive
    ("phone", re.compile(r"(?<![\w.:/-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?|\d{2,4}[\s.-])\d{3,4}[\s.-]\d{3,4}(?![\w.-])"), False),
]


def _luhn(s: str) -> bool:
    digits = [int(c) for c in s if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


class RegexRedactor:
    """Default redactor plugin. Also masks exact values registered from the SecretStore."""

    def __init__(self, known_secrets: list[str] | None = None) -> None:
        self._known: list[str] = [s for s in (known_secrets or []) if len(s) >= 6]

    def add_known_secret(self, value: str) -> None:
        if len(value) >= 6 and value not in self._known:
            self._known.append(value)

    def redact(self, text: str) -> tuple[str, list[str]]:
        """Return (redacted_text, findings). Findings names secrets that were *exposed*."""
        findings: list[str] = []
        for value in self._known:
            if value in text:
                text = text.replace(value, MASK)
                findings.append("known_secret")
        for name, pat, is_secret in _PATTERNS:
            if name == "kv_secret":
                text, n = pat.subn(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
            elif name == "bearer":
                text, n = pat.subn(lambda m: f"{m.group(1)} {MASK}", text)
            elif name == "card":
                text, n = pat.subn(lambda m: MASK if _luhn(m.group()) else m.group(), text)
            else:
                text, n = pat.subn(MASK, text)
            if n and is_secret:
                findings.append(name)
        return text, findings

    def redact_obj(self, obj: Any) -> tuple[Any, list[str]]:
        findings: list[str] = []

        def walk(o: Any) -> Any:
            if isinstance(o, str):
                t, f = self.redact(o)
                findings.extend(f)
                return t
            if isinstance(o, dict):
                return {k: walk(v) for k, v in o.items()}
            if isinstance(o, list):
                return [walk(v) for v in o]
            return o

        return walk(obj), findings
