"""The recorder: observations in, state changes out.

Every adapter hands its observations to `Recorder.observe`. An event is appended only when an
alert's status changes (`alert.firing`, `alert.resolved`); a repeat of the current status only
moves `last_seen`. This is what makes 90 "firing" rows from one OpenObserve alert one firing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from paeon.schema import Alert, Severity, Status
from paeon.store import Store

log = logging.getLogger(__name__)

INTERNAL_SOURCE = "paeon"


class Recorder:
    def __init__(self, store: Store) -> None:
        self._store = store

    def observe(self, alert: Alert) -> int | None:
        """Record one observation. Returns the event id if it was a state change, else None."""
        state = self._store.alert_state(alert.source, alert.fingerprint)
        if state is not None and alert.observed_at <= state.last_seen:
            return None  # already seen (poll overlap, duplicate row) or older than what we know
        if state is not None and state.status == alert.status.value:
            self._store.set_alert_state(
                alert.source,
                alert.fingerprint,
                status=state.status,
                since=state.since,
                last_seen=alert.observed_at,
            )
            return None
        self._store.set_alert_state(
            alert.source,
            alert.fingerprint,
            status=alert.status.value,
            since=alert.observed_at,
            last_seen=alert.observed_at,
        )
        if state is None and alert.status is Status.RESOLVED:
            return None  # first sight of a healthy alert: a baseline, not a recovery
        payload = alert.model_dump(mode="json")
        if alert.status is Status.RESOLVED and state is not None:
            payload["fired_at"] = state.since.isoformat()
        event_id = self._store.append(
            f"alert.{alert.status.value}",
            payload,
            source=alert.source,
            fingerprint=alert.fingerprint,
        )
        log.info("%s %s/%s (%s)", alert.status.value, alert.source, alert.fingerprint, alert.title)
        return event_id

    def source_health(
        self, source: str, *, last_data_at: datetime | None, now: datetime, stale_after: timedelta
    ) -> int | None:
        """Raise or clear the internal `source_stale` alert for a pull source.

        A source that cannot be polled, or that answers with no data at all, must not look like a
        quiet day: Paeon learns about OpenObserve's alerts from OpenObserve.
        """
        stale = last_data_at is None or now - last_data_at > stale_after
        seen = "never" if last_data_at is None else last_data_at.isoformat()
        return self.observe(
            Alert(
                source=INTERNAL_SOURCE,
                fingerprint=f"source_stale:{source}",
                status=Status.FIRING if stale else Status.RESOLVED,
                severity=Severity.CRITICAL,
                title=f"Source {source} is stale",
                description=f"No data from {source} since {seen}; its alerts are not being seen.",
                labels={"source": source},
                starts_at=last_data_at or now,
                observed_at=now,
            )
        )
