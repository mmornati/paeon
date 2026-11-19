"""Who is asking, and what they may do.

Three roles, each holding the one before it: a viewer reads, an operator also says whether
triage was right and stops automation, an admin also changes what the console may change.

Where the role comes from depends on `ui.auth`:

- `proxy` (the default): a reverse proxy authenticates. With `ui.roles`, the groups it names
  for the request decide; without, everyone it lets in is an operator, as before roles existed,
  and nobody is an admin.
- `local`: the console asks for one password, and whoever has it is the admin. The password
  is the environment's, or is chosen in the browser the first time, with a code Paeon prints
  when it starts. The session is a signed cookie; nothing about it is stored, and changing
  the password or the key ends it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from paeon.rules import Roles
from paeon.vault import derive

if TYPE_CHECKING:
    from paeon.store import Store

Role = Literal["viewer", "operator", "admin"]
RANK: dict[str, int] = {"viewer": 0, "operator": 1, "admin": 2}
COOKIE = "paeon_session"
SESSION_SECONDS = 14 * 24 * 3600
MAX_GATES = 4096
EVERYONE_TRIES = 50  # wrong passwords in a row, from anywhere, before everyone waits


def _network(client: str) -> str:
    """What a client is counted by: its address, or its /64 for IPv6, where one subscriber
    holds that many addresses."""
    try:
        address = ipaddress.ip_address(client)
    except ValueError:
        return client
    if address.version == 6:
        if address.ipv4_mapped is not None:
            # An IPv4 client seen through a dual-stack socket: all of IPv4 shares one /64
            # there, and must not share one gate.
            return str(address.ipv4_mapped)
        if address.is_global and address.sixtofour is None and address.teredo is None:
            return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def allows(role: str, needed: Role) -> bool:
    return RANK.get(role, 0) >= RANK[needed]


def role_from_groups(roles: Roles, header: str | None) -> Role:
    groups = {part.strip() for part in (header or "").split(roles.separator)} - {""}
    if groups & set(roles.admin):
        return "admin"
    if groups & set(roles.operator):
        return "operator"
    return "viewer"


class Passwords:
    """The admin's password. The environment's, when it gives one (PAEON_UI_ADMIN_PASSWORD):
    that is also the way back in when the other is forgotten. Otherwise the one chosen in the
    browser at setup, of which only a salted scrypt hash is kept."""

    KEY = "admin_password"

    def __init__(self, store: Store | None, fixed: str | None = None) -> None:
        self._store = store
        self._fixed = fixed or None

    def ready(self) -> bool:
        return self._fixed is not None or self._stored() is not None

    def check(self, password: str) -> bool:
        if self._fixed is not None:
            given = hashlib.sha256(password.encode()).digest()
            return hmac.compare_digest(given, hashlib.sha256(self._fixed.encode()).digest())
        stored = self._stored()
        if stored is None:
            return False
        try:
            _, n, r, p, salt, wanted = stored.split("$")
            found = hashlib.scrypt(
                password.encode(),
                salt=base64.b64decode(salt),
                n=int(n),
                r=int(r),
                p=int(p),
                dklen=32,
            )
            return hmac.compare_digest(found, base64.b64decode(wanted))
        except ValueError:
            return False

    def choose(self, password: str) -> None:
        if self._store is None:
            raise RuntimeError("no store to keep a password in")
        salt = os.urandom(16)
        n, r, p = 2**14, 8, 1
        found = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
        encoded = (base64.b64encode(part).decode() for part in (salt, found))
        self._store.set_console_state(
            self.KEY, "$".join(["scrypt", str(n), str(r), str(p), *encoded])
        )

    def stamp(self) -> str:
        """Changes when the password does: it is part of what signs a session."""
        return hashlib.sha256((self._fixed or self._stored() or "").encode()).hexdigest()

    def _stored(self) -> str | None:
        return None if self._store is None else self._store.console_state(self.KEY)


class Sessions:
    """Signed, expiring cookies for the one local admin."""

    def __init__(
        self,
        secret_key: str,
        password: str | Passwords,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._secret = secret_key
        self.passwords = password if isinstance(password, Passwords) else Passwords(None, password)
        self._clock = clock
        self._gates: dict[str, Gate] = {}
        # Whatever the address: many addresses are cheap (a /64 of IPv6 is one network), so
        # wrong passwords are also counted together, with a wider allowance.
        self._everyone = Gate(tries=EVERYONE_TRIES, clock=clock)
        self._lock = threading.Lock()

    def _gate(self, client: str) -> Gate:
        """The gate of one client. Counted by the address of the connection, never by a header
        Paeon read itself. Behind a proxy that is the proxy's address, so everyone shares one
        gate, as a single account would."""
        key = _network(client)
        with self._lock:
            gate = self._gates.get(key)
            if gate is None:
                if len(self._gates) >= MAX_GATES:
                    # Room is made from the gates that are open: a closed one is never
                    # forgotten, or a flood of addresses would reopen it.
                    self._gates = {k: g for k, g in self._gates.items() if g.locked()}
                gate = self._gates[key] = Gate(clock=self._clock)
            return gate

    def attempt(self, client: str, right: Callable[[], bool]) -> bool:
        """One guess at a secret, counted. False without looking while too many wrong ones
        were just made, from this client or from all of them (see `locked`)."""
        gate = self._gate(client)
        if gate.locked() or self._everyone.locked():
            return False
        if not right():
            gate.failed()
            self._everyone.failed()
            return False
        gate.passed()
        self._everyone.passed()
        return True

    def sign_in(self, password: str, client: str = "") -> str | None:
        """A cookie value for the right password, or None."""
        if not self.attempt(client, lambda: self.passwords.check(password)):
            return None
        return self.issue()

    def issue(self) -> str:
        """A cookie value, for someone who has just proved who they are."""
        body = json.dumps({"exp": int(self._clock()) + SESSION_SECONDS}).encode()
        payload = base64.urlsafe_b64encode(body).decode().rstrip("=")
        return f"{payload}.{self._sign(payload)}"

    def locked(self, client: str = "") -> int:
        """Seconds until a password from this client is looked at again; 0 when one can be
        tried now."""
        return max(self._gate(client).locked(), self._everyone.locked())

    def valid(self, cookie: str | None) -> bool:
        payload, _, signature = (cookie or "").partition(".")
        if not self.passwords.ready():
            return False  # no password yet: nobody can have signed in
        if not payload or not hmac.compare_digest(signature, self._sign(payload)):
            return False
        try:
            body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            return float(body["exp"]) > self._clock()
        except (ValueError, KeyError, TypeError):
            return False

    def _sign(self, payload: str) -> str:
        # The password is part of the key: changing it signs everyone out.
        key = derive(self._secret, "session:" + self.passwords.stamp())
        return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


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
