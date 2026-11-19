"""The one way a source's API is read: fixed paths, no redirect, and errors without a URL.

An adapter says which paths it reads when it builds its `Reader`, and nothing else can be
asked: the model never writes a URL, a path or a method. A few services answer a read to a
POST (a JSON-RPC call, a login); the adapter writes that body, and lists the path like any other.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx2 as httpx


class ReadError(Exception):
    """A read that did not succeed. Shown to the model and to the owner: the service and the
    status, never a URL or a credential."""


class Reader:
    def __init__(
        self,
        kind: str,
        url: str,
        *,
        paths: Sequence[str],
        user: str | None = None,
        password: str | None = None,
        token: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.kind = kind
        self._paths = tuple(paths)
        # Kept apart from the client's headers, so that a request can be sent without it.
        self._proof: dict[str, str] = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.Client(
            base_url=url.rstrip("/"),
            auth=(user, password or "") if user is not None else None,
            headers=dict(headers or {}),
            timeout=timeout,
            transport=transport,
        )

    def authorize(self, headers: Mapping[str, str]) -> None:
        """What proves who asks, from now on: a session a service gave after a sign-in."""
        self._proof = dict(headers)

    def get(self, path: str, **params: Any) -> httpx.Response:
        return self._ask("GET", path, params=_given(params))

    def post(self, path: str, body: Any, *, bare: bool = False) -> httpx.Response:
        """A read that the service takes as a POST. `bare`: sent without the token."""
        return self._ask("POST", path, bare=bare, json=body)

    def _ask(self, method: str, path: str, *, bare: bool = False, **request: Any) -> httpx.Response:
        if not any(path == known or path.startswith(known + "/") for known in self._paths):
            raise ReadError(f"{self.kind}: {path} is not a path this source reads")
        try:
            answer = self._http.request(
                method, path, headers={} if bare else self._proof, **request
            )
        except httpx.HTTPError as exc:
            raise ReadError(f"{self.kind} is unreachable: {type(exc).__name__}") from None
        if answer.status_code >= 400:
            raise ReadError(f"{self.kind} answered HTTP {answer.status_code}")
        return answer

    def json(self, path: str, **params: Any) -> Any:
        return decoded(self.kind, self.get(path, **params))

    def close(self) -> None:
        self._http.close()


def decoded(kind: str, answer: httpx.Response) -> Any:
    try:
        return answer.json()
    except ValueError:
        raise ReadError(f"{kind} did not answer with JSON") from None


def _given(params: Mapping[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in params.items() if value is not None}


def mapping(value: Any) -> dict[str, Any]:
    """A JSON object, or an empty one for anything else a service sent in its place."""
    return value if isinstance(value, dict) else {}


def listing(value: Any) -> list[Any]:
    """A JSON array, or an empty one for anything else a service sent in its place."""
    return value if isinstance(value, list) else []
