"""Heuristic prompt-injection detector (A5). Flags — never blocks: content stays data either way."""
from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_previous", re.compile(r"(?i)\b(ignore|disregard|forget)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all)\b[^.\n]{0,40}\b(instruction|prompt|rule|message)s?")),
    ("role_override", re.compile(r"(?i)\b(you are now|from now on you|act as|new instructions?:|system prompt)\b")),
    ("fake_approval", re.compile(r"(?i)\b(approve|approved|lgtm|auto-?approve)\b[^.\n]{0,30}\b(this|it|the plan|the pr|automatically)\b|\bapproval (has been|is) (granted|given)\b")),
    ("exfiltrate", re.compile(r"(?i)\b(send|post|forward|email|upload)\b[^.\n]{0,40}\b(this|it|the (data|logs|secrets?|keys?|token))\b[^.\n]{0,20}\bto\b")),
    ("run_command", re.compile(r"(?i)\b(run|execute)\s+(this|the following)\b|\bcurl\s+[^\n|]*\|\s*(ba)?sh\b")),
    ("hide_from_user", re.compile(r"(?i)\b(do not|don't|never)\s+(tell|inform|mention|show)\b[^.\n]{0,20}\b(user|human)\b")),
    ("fake_tag", re.compile(r"(?i)</?\s*(system|assistant|untrusted|tool_result)\b")),
]


def scan(text: str) -> list[str]:
    return sorted({name for name, pat in _PATTERNS if pat.search(text)})
