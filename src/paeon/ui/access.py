"""Who is asking, and what they may do.

Three roles, each holding the one before it: a viewer reads, an operator also says whether
triage was right and stops automation, an admin also changes what the console may change.

Where the role comes from depends on `ui.auth`:

- `proxy` (the default): a reverse proxy authenticates. With `ui.roles`, the groups it names
  for the request decide; without, everyone it lets in is an operator, as before roles existed,
  and nobody is an admin.
- `local`: the console asks for one password, and whoever has it is the admin. The session is
  a signed cookie; nothing about it is stored, and changing the password or the key ends it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from collections.abc import Callable
from typing import Literal

from paeon.rules import Roles
from paeon.vault import derive

Role = Literal["viewer", "operator", "admin"]
RANK: dict[str, int] = {"viewer": 0, "operator": 1, "admin": 2}
COOKIE = "paeon_session"
SESSION_SECONDS = 14 * 24 * 3600
MAX_GATES = 4096


def allows(role: str, needed: Role) -> bool:
    return RANK.get(role, 0) >= RANK[needed]


def role_from_groups(roles: Roles, header: str | None) -> Role:
    groups = {part.strip() for part in (header or "").split(roles.separator)} - {""}
    if groups & set(roles.admin):
        return "admin"
    if groups & set(roles.operator):
        return "operator"
    return "viewer"


class Sessions:
    """Signed, expiring cookies for the one local admin."""

    def __init__(
        self, secret_key: str, password: str, *, clock: Callable[[], float] = time.time
    ) -> None:
        # The password is part of the key: changing it signs everyone out.
        self._key = derive(secret_key, "session:" + hashlib.sha256(password.encode()).hexdigest())
        self._password = hashlib.sha256(password.encode()).digest()
        self._clock = clock
        self._gates: dict[str, Gate] = {}
        self._lock = threading.Lock()

    def _gate(self, client: str) -> Gate:
        """The gate of one client address. Counted by the address of the connection itself,
        never by a header, which anyone can write. Behind a proxy that is the proxy's address,
        so everyone shares one gate, as a single account would."""
        with self._lock:
            gate = self._gates.get(client)
            if gate is None:
                if len(self._gates) >= MAX_GATES:  # forget the oldest: memory stays bounded
                    self._gates.pop(next(iter(self._gates)))
                gate = self._gates[client] = Gate(clock=self._clock)
            return gate

    def sign_in(self, password: str, client: str = "") -> str | None:
        """A cookie value for the right password. None for a wrong one, or while too many
        wrong ones were just tried from this client (see `locked`)."""
        gate = self._gate(client)
        if gate.locked():
            return None
        given = hashlib.sha256(password.encode()).digest()
        if not hmac.compare_digest(given, self._password):
            gate.failed()
            return None
        gate.passed()
        body = json.dumps({"exp": int(self._clock()) + SESSION_SECONDS}).encode()
        payload = base64.urlsafe_b64encode(body).decode().rstrip("=")
        return f"{payload}.{self._sign(payload)}"

    def locked(self, client: str = "") -> int:
        """Seconds until a password from this client is looked at again; 0 when one can be
        tried now."""
        return self._gate(client).locked()

    def valid(self, cookie: str | None) -> bool:
        payload, _, signature = (cookie or "").partition(".")
        if not payload or not hmac.compare_digest(signature, self._sign(payload)):
            return False
        try:
            body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            return float(body["exp"]) > self._clock()
        except (ValueError, KeyError, TypeError):
            return False

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()


class Gate:
    """Slows guessing: after `tries` wrong passwords in a row, none is looked at for `wait`
    seconds. There is one gate per client address (see `Sessions`), so that someone guessing
    from elsewhere does not keep the admin out."""

    def __init__(
        self, *, tries: int = 5, wait: int = 60, clock: Callable[[], float] = time.time
    ) -> None:
        self._tries = tries
        self._wait = wait
        self._clock = clock
        self._lock = threading.Lock()
        self._wrong = 0
        self._until = 0.0

    def locked(self) -> int:
        with self._lock:
            return max(0, int(self._until - self._clock() + 0.999))

    def failed(self) -> None:
        with self._lock:
            self._wrong += 1
            if self._wrong >= self._tries:
                self._wrong = 0
                self._until = self._clock() + self._wait

    def passed(self) -> None:
        with self._lock:
            self._wrong = 0
