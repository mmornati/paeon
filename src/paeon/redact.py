"""Redaction of what is sent to a model: secret patterns out, long strings cut.

This is a net, not a guarantee: it catches the common shapes of credentials in alert text. The
first protection is what the context pack is built from (alert titles, descriptions and labels,
never raw payloads or log lines).
"""

from __future__ import annotations

import re
from typing import Any

MASK = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        MASK,
    ),
    # user:password@ in a URL
    (re.compile(r"(?<=://)[^\s/@:]+:[^\s/@]+(?=@)"), MASK),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {MASK}"),
    # key = value, key: value, "key": "value", key=value in a query string. A quoted value is
    # masked up to its closing quote (it may hold spaces), or to the end of the line without one;
    # a bare value runs to the next space or separator, quotes inside it included. The key's
    # prefix and suffix are bounded: unbounded, a long run of word characters costs quadratic time.
    (
        re.compile(
            r"(?i)\b([\w-]{0,64}(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|credential)"
            r"[\w-]{0,64})([\"']?\s*[:=]\s*)"
            r"(\"[^\"\n]*\"?|'[^'\n]*'?|[^\s\"'&,;][^\s&,;]*)"
        ),
        rf"\1\2{MASK}",
    ),
    # well-known token shapes
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), MASK),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), MASK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), MASK),
    (re.compile(r"\btk_[A-Za-z0-9]{20,}"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
)
_SECRET_KEY = re.compile(r"(?i)password|passwd|pwd|secret|token|api[_-]?key|credential|auth")


def redact_text(text: str, *, max_length: int = 2000) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    if len(text) > max_length:
        text = text[:max_length] + f"… [{len(text) - max_length} characters cut]"
    return text


def redact(value: Any, *, max_length: int = 2000) -> Any:
    """A copy of a JSON-like value with every string redacted and cut."""
    if isinstance(value, str):
        return redact_text(value, max_length=max_length)
    if isinstance(value, dict):
        return {
            str(key): MASK
            if isinstance(item, str) and _SECRET_KEY.search(str(key))
            else redact(item, max_length=max_length)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item, max_length=max_length) for item in value]
    return value
