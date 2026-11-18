"""Signed action links: what a button in a notification carries.

A link is `<payload>.<signature>`: the payload is JSON (what the link does, to which plan, until
when, and a nonce), the signature an HMAC-SHA256 of it. The executor mints and checks them with a
key the core never holds. A link works once (the nonce is remembered), until it expires, and
only while the plan it names is still the plan that is waiting.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime
from typing import Any


class LinkError(Exception):
    """The link is malformed, forged or expired. The message is safe to show."""


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError as exc:
        raise LinkError("this link is not valid") from exc


class Signer:
    def __init__(self, key: str) -> None:
        if len(key) < 32:
            raise ValueError("the signing key must be 32 characters or more")
        self._key = key.encode()

    def _sign(self, body: str) -> str:
        return _encode(hmac.new(self._key, body.encode(), hashlib.sha256).digest())

    def mint(self, kind: str, *, expires_at: datetime, **data: Any) -> str:
        payload = {
            "k": kind,
            "exp": int(expires_at.timestamp()),
            "n": secrets.token_urlsafe(12),
            **data,
        }
        body = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        return f"{body}.{self._sign(body)}"

    def authentic(self, token: str) -> dict[str, Any]:
        """The payload of a link this key signed, whether or not it is still in time."""
        body, _, signature = token.partition(".")
        if not body or not hmac.compare_digest(signature, self._sign(body)):
            raise LinkError("this link is not valid")
        try:
            payload = json.loads(_decode(body))
        except ValueError as exc:  # pragma: no cover - a signed body is JSON we wrote
            raise LinkError("this link is not valid") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("exp"), int):
            raise LinkError("this link is not valid")  # pragma: no cover - same
        return payload

    def read(self, token: str, now: datetime) -> dict[str, Any]:
        """The payload of a link that is authentic and not expired."""
        payload = self.authentic(token)
        if now.timestamp() > payload["exp"]:
            raise LinkError("this link has expired")
        return payload
