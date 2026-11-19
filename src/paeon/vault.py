"""What the console keeps secret: a channel's token typed in the browser, and its sessions.

Both come from one key, `PAEON_SECRET_KEY`, which lives in the environment like every other
secret. A token is encrypted before it reaches the database (Fernet: AES-128-CBC with an HMAC),
so a copy of the SQLite file alone gives nothing away. Each use derives its own key from the
one in the environment, so a session's signature can never stand in for anything else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken


class VaultError(ValueError):
    """A stored secret cannot be read: the key changed, or the value was altered."""


def derive(secret_key: str, purpose: str) -> bytes:
    """32 bytes for one purpose. A different purpose gives an unrelated key."""
    return hmac.new(secret_key.encode(), f"paeon:{purpose}".encode(), hashlib.sha256).digest()


class Vault:
    def __init__(self, secret_key: str) -> None:
        self._fernet = Fernet(base64.urlsafe_b64encode(derive(secret_key, "channel-secrets")))

    def seal(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def open(self, sealed: str) -> str:
        try:
            return self._fernet.decrypt(sealed.encode()).decode()
        except (InvalidToken, ValueError) as exc:
            raise VaultError("a stored secret cannot be read with this PAEON_SECRET_KEY") from exc
