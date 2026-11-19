"""Redaction of what is sent to a model: secret patterns out, long strings cut.

This is a net, not a guarantee: it catches the common shapes of credentials in alert text. The
first protection is what the context pack is built from (alert titles, descriptions and labels,
never raw payloads or log lines).
"""

from __future__ import annotations

import re
from typing import Any

MASK = "[REDACTED]"

_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PEM_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")
# Patterns run on at most this many characters beyond what is kept: the text is an alert's, of
# any length, and what is cut anyway is not worth reading.
_MARGIN = 20_000
_EDGE = 256

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # user:password@ in a URL; the user may be empty (`redis://:password@host`)
    (re.compile(r"(?<=://)[^\s/@:]*:[^\s/@]+(?=@)"), MASK),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {MASK}"),
    # a header that carries a credential, whatever its scheme
    (
        re.compile(r"(?i)\b((?:proxy-)?authorization|cookie|set-cookie)(\s*[:=]\s*)[^\r\n]+"),
        rf"\1\2{MASK}",
    ),
    # key = value, key: value, "key": "value", key=value in a query string. A quoted value is
    # masked up to its closing quote (it may hold spaces), or to the end of the line without one;
    # a bare value runs to the next space or separator, quotes inside it included. The key is a
    # whole run of word characters that names a secret, looked at once from its start: the time
    # is linear whatever its length.
    (
        re.compile(
            r"(?i)(?<![\w-])"
            r"(?=[\w-]*?(?:"
            r"password|passwd|passphrase|pwd|secret|token|api[_-]?key|apikey"
            r"|access[_-]?key|signing[_-]?key|private[_-]?key|encryption[_-]?key|credential))"
            r"([\w-]+)(\\?[\"']?\s*[:=]\s*\\?)"
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
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}"), MASK),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{30,}"), MASK),
)
_SECRET_KEY = re.compile(
    r"(?i)password|passwd|passphrase|pwd|secret|token|api[_-]?key|credential|auth|cookie|session"
    r"|(?:access|signing|private|encryption)[_-]?key|^pass$"
)
_PASSWORD_KEY = re.compile(r"(?i)password|passwd|passphrase|pwd|secret|^pass$")


def _without_keys(text: str) -> str:
    """The text with every PEM private key masked, in one pass whatever the text holds."""
    parts: list[str] = []
    position = 0
    while (begin := _PEM_BEGIN.search(text, position)) is not None:
        end = _PEM_END.search(text, begin.end())
        if end is None:
            break
        parts += [text[position : begin.start()], MASK]
        position = end.end()
    return "".join(parts) + text[position:] if parts else text


def redact_text(text: str, *, max_length: int = 2000) -> str:
    length = len(text)
    beyond = length > max_length + _MARGIN
    if beyond:
        text = text[: max_length + _MARGIN]
    text = _without_keys(text)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    if beyond:
        # The end of what was looked at may be half a secret no pattern could recognise.
        text = text[:-_EDGE]
        length -= _EDGE
    else:
        length = len(text)
    if length > max_length:
        text = text[:max_length] + f"… [{length - max_length} characters cut]"
    return text


def _masked(key: str, item: Any) -> bool:
    if isinstance(item, str):
        return _SECRET_KEY.search(key) is not None
    # A number is masked only under a key that cannot be a count (`input_tokens` is one).
    number = isinstance(item, int | float) and not isinstance(item, bool)
    return number and _PASSWORD_KEY.search(key) is not None


def redact(value: Any, *, max_length: int = 2000) -> Any:
    """A copy of a JSON-like value with every string redacted and cut."""
    if isinstance(value, str):
        return redact_text(value, max_length=max_length)
    if isinstance(value, dict):
        return {
            str(key): MASK if _masked(str(key), item) else redact(item, max_length=max_length)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item, max_length=max_length) for item in value]
    return value
