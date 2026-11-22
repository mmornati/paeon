"""Maintenance holds: an incident that opens in a window with `hold` waits for the window to end.

A weekly update restarts a dozen services, and their monitors say so. Correlation flags the
incident with the window (nothing is dropped); this module decides what the flag means:

- while the window is open, and for `grace_minutes` after, the incident is **held**: triage and
  investigation leave it alone (so no postmortem either), nobody is woken up, no model is paid;
- then it is **released**, once: each alert still firing is re-checked with its source (the same
  re-check as the console's, so an uptime-kuma recovery that was never sent is caught too).
  An incident that is over by then closes as `expected`: its stages are skipped, and the digest
  still lists it. One that is not goes on as usual, and the stages see `maintenance.released`
  with what was still firing: a service that has not come back after an update is news.

Every release is a `maintenance.released` event. A window removed from the rules, or one whose
`hold` was turned off, holds nothing any more: its incidents go on at once, without a re-check.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from paeon.operations import Incidents, OperationError
from paeon.rules import Rules
from paeon.schema import Incident, IncidentStatus, StageState, Status
from paeon.store import Store

log = logging.getLogger(__name__)

RELEASED = "maintenance.released"
_STAGES = ("triage", "investigation")  # a postmortem follows an investigation


def held(store: Store, rules: Rules, incident: Incident) -> bool:
    """Whether a stage must leave this incident alone for now."""
    if incident.maintenance is None:
        return False
    window = rules.window(incident.maintenance)
    if window is None or not window.hold:
        return False
    return not any(store.events(incident_id=incident.id, kind=RELEASED, limit=1))


def released(store: Store, incident: Incident) -> dict[str, Any] | None:
    """What the release of a held incident found, for the stages that come after it."""
    for event in store.events(incident_id=incident.id, kind=RELEASED, newest_first=True, limit=1):
        return {
            "window": event.payload.get("window"),
            "outcome": event.payload.get("outcome"),
            "still_firing": event.payload.get("still_firing") or [],
            "released_at": event.recorded_at.isoformat(),
        }
    return None


class Holds:
    def __init__(
        self,
        store: Store,
        rules: Rules,
        incidents: Incidents | None,
    ) -> None:
        """`incidents`: what re-checks an alert with its source; None, and nothing is asked."""
        self._store = store
        self._rules = rules
        self._incidents = incidents

    def run_pending(self, now: datetime) -> int:
        """Release every held incident whose window and grace are over. Returns how many."""
        released = 0
        seen: set[int] = set()
        for stage in _STAGES:
            for incident in self._store.incidents(**{stage: StageState.PENDING}):  # type: ignore[arg-type]
                if incident.id in seen or not held(self._store, self._rules, incident):
                    continue
                seen.add(incident.id)
                if now < self.release_at(incident):
                    continue
                self.release(incident, now)
                released += 1
        return released

    def release_at(self, incident: Incident) -> datetime:
        window = self._rules.window(incident.maintenance or "")
        if window is None:
            return incident.opened_at
        closes = window.closes_at(incident.opened_at) or incident.opened_at
        grace = timedelta(minutes=window.grace_minutes)
        # `opened_at` is the alert's own time, sent by the source: a time far ahead must not
        # hold an incident for longer than one window, counted from when Paeon recorded it.
        seen = next(
            iter(self._store.events(incident_id=incident.id, kind="incident.opened", limit=1)),
            None,
        )
        if seen is not None:
            longest = seen.recorded_at + timedelta(minutes=window.duration_minutes) + grace
            return min(closes + grace, longest)
        return closes + grace

    def release(self, incident: Incident, now: datetime) -> None:
        checked: list[dict[str, str]] = []
        if incident.status is IncidentStatus.OPEN and self._incidents is not None:
            try:
                found = self._incidents.recheck(
                    incident.id, by=f"maintenance: {incident.maintenance}"
                )
            except OperationError:  # resolved in the meantime
                found = []
            checked = [one.as_dict() for one in found]
        after = self._store.incident(incident.id) or incident
        firing = [m.title for m in after.alerts if m.status is Status.FIRING]
        expected = after.status is IncidentStatus.RESOLVED
        payload: dict[str, Any] = {
            "window": incident.maintenance,
            "outcome": "expected" if expected else "still_firing",
            "rechecked": checked,
            "still_firing": firing,
        }
        self._store.append(RELEASED, payload, incident_id=incident.id, now=now)
        if expected:
            for stage in _STAGES:
                if getattr(after, stage) is StageState.PENDING:
                    self._store.append(
                        f"{stage}.skipped",
                        {"skip": "maintenance", "effective_verdict": "noise"},
                        incident_id=incident.id,
                        now=now,
                    )
                    self._store.set_stage(incident.id, stage, StageState.SKIPPED)  # type: ignore[arg-type]
            log.info(
                "incident %d: over by the end of %s, closed as expected",
                incident.id,
                incident.maintenance,
            )
        else:
            log.info(
                "incident %d: still firing after %s: %s",
                incident.id,
                incident.maintenance,
                ", ".join(firing),
            )
