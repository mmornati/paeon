"""The cost ledger: every model call is a `model.call` event, and budgets are read from them."""

from __future__ import annotations

from datetime import UTC, datetime

from paeon.store import Store


def spent_today(store: Store, now: datetime) -> float:
    """Cost of every model call since midnight UTC, all stages."""
    midnight = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return sum(
        float(event.payload.get("cost") or 0)
        for event in store.events(kind="model.call", recorded_since=midnight)
    )


def spent_on(store: Store, incident_id: int) -> float:
    return sum(
        float(event.payload.get("cost") or 0)
        for event in store.events(kind="model.call", incident_id=incident_id)
    )
