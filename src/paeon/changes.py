"""The change calendar as it stands: each change once, as last recorded.

A change sent with an `id` can be recorded again under that id (to give it an end, for
example): the store keeps every version, and readers see only the latest. The id is kept as
the event's fingerprint.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from paeon.store import Store

KIND = "change.recorded"


def recent(store: Store, since: datetime, *, limit: int) -> list[dict[str, Any]]:
    """The changes recorded since then, oldest first, the latest version of each, at most
    `limit` of the most recent ones."""
    seen: set[str] = set()
    found: list[dict[str, Any]] = []
    for event in store.events(kind=KIND, recorded_since=since, newest_first=True):
        if event.fingerprint is not None:
            if event.fingerprint in seen:
                continue
            seen.add(event.fingerprint)
        found.append(event.payload)
        if len(found) >= limit:
            break
    found.reverse()
    return found


def latest(store: Store, change_id: str) -> dict[str, Any] | None:
    """The change recorded under this id, as last recorded, or None."""
    for event in store.events(kind=KIND, fingerprint=change_id, newest_first=True, limit=1):
        return event.payload
    return None
