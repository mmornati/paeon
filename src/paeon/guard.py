"""What is checked before a request body is read: who sends it, and how large it is.

FastAPI reads and parses a body before it resolves a route's dependencies, so a bearer check
written as a dependency comes after the whole body is in memory. This middleware answers first:
a request with a body and without the token is refused unread, and no body goes past a size.
"""

from __future__ import annotations

import contextlib
import hmac
import json
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_WITH_BODY = ("POST", "PUT", "PATCH", "DELETE")


class _TooLarge(Exception):
    pass


class BodyGuard:
    """Pure ASGI: `open_prefixes` are the paths that carry their own proof (a form of the
    console behind the proxy, a signed link); they are limited in size, not asked for the token.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        max_bytes: int,
        open_prefixes: tuple[str, ...] = (),
    ) -> None:
        self._app = app
        self._expected = f"Bearer {token}".encode()
        self._max = max_bytes
        self._open = open_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _WITH_BODY:
            await self._app(scope, receive, send)
            return
        headers = {name: value for name, value in scope["headers"]}
        path = str(scope["path"])
        is_open = any(path == p or path.startswith(p + "/") for p in self._open)
        if not is_open and not hmac.compare_digest(
            headers.get(b"authorization", b""), self._expected
        ):
            await _answer(send, 401, "Not authenticated", {"WWW-Authenticate": "Bearer"})
            return
        declared = headers.get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self._max):
            await _answer(send, 413, "Request body too large")
            return

        seen = 0
        refused = False
        started = False

        async def counted() -> Message:
            nonlocal seen, refused
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self._max:
                    refused = True
                    raise _TooLarge
            return message

        async def forward(message: Message) -> None:
            nonlocal started
            if refused:
                return  # whatever the application makes of the cut body is not the answer
            started = started or message["type"] == "http.response.start"
            await send(message)

        with contextlib.suppress(_TooLarge):
            await self._app(scope, counted, forward)
        if refused and not started:
            await _answer(send, 413, "Request body too large")


async def _answer(
    send: Send, status: int, detail: str, headers: dict[str, str] | None = None
) -> None:
    body = json.dumps({"detail": detail}).encode()
    raw: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        *((name.lower().encode(), value.encode()) for name, value in (headers or {}).items()),
    ]
    start: dict[str, Any] = {"type": "http.response.start", "status": status, "headers": raw}
    await send(start)
    await send({"type": "http.response.body", "body": body})
