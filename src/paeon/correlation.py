"""Correlation: state changes in, incidents out. No model is involved.

Rules, in order, for an alert that starts firing:

1. the same alert (source and fingerprint) is already in an incident that is open, or that closed
   less than `correlation.window_minutes` ago: it is the same incident (reopened if needed);
2. an incident is open on the same host and opened less than `host_window_minutes` ago: the alert
   joins it (a reverse proxy outage fires ten monitors and three SLOs, and is one incident);
3. otherwise a new incident opens.

An incident resolves when none of its alerts is firing. Flapping (an alert firing `flap.threshold`
times within `flap.window_minutes`) and maintenance windows are flags on the incident: nothing is
dropped here, the later stages decide what a flag means.

Every decision is an event (`incident.opened`, `incident.correlated`, `incident.reopened`,
`incident.flapping`, `incident.resolved`), and time is the alert's own `observed_at`, so replaying
recorded alerts gives the same incidents.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from paeon.rules import Rules
from paeon.schema import (
    SEVERITY_RANK,
    Alert,
    Incident,
    IncidentStatus,
    Status,
    TriageState,
)
from paeon.store import Store

log = logging.getLogger(__name__)


class Correlator:
    def __init__(self, store: Store, rules: Rules) -> None:
        self._store = store
        self._rules = rules

    def assign(self, alert: Alert) -> int | None:
        """The incident a state change belongs to. Called before the alert event is appended."""
        if alert.status is Status.RESOLVED:
            return self._store.incident_of(alert.source, alert.fingerprint)
        at = alert.observed_at
        host = self._rules.correlation.host_of(alert)
        incident = self._same_alert(alert, at)
        reason = "fingerprint"
        if incident is None and host is not None:
            incident = self._same_host(host, at)
            reason = "host"
        if incident is None:
            incident_id = self._open(alert, host)
        else:
            incident_id = incident.id
            self._join(incident, alert, reason)
        self._store.attach_alert(
            incident_id,
            source=alert.source,
            fingerprint=alert.fingerprint,
            title=alert.title,
            status=Status.FIRING,
        )
        self._flap(incident_id, alert)
        return incident_id

    def settle(self, alert: Alert, incident_id: int | None) -> None:
        """Close the incident when its last firing alert resolves. Called after the alert event."""
        if alert.status is not Status.RESOLVED or incident_id is None:
            return
        self._store.attach_alert(
            incident_id,
            source=alert.source,
            fingerprint=alert.fingerprint,
            title=alert.title,
            status=Status.RESOLVED,
        )
        incident = self._store.incident(incident_id)
        if incident is None or incident.status is IncidentStatus.RESOLVED:
            return
        at = max(alert.observed_at, incident.last_activity_at)
        if any(member.status is Status.FIRING for member in incident.alerts):
            self._store.save_incident(incident.model_copy(update={"last_activity_at": at}))
            return
        self._store.save_incident(
            incident.model_copy(
                update={
                    "status": IncidentStatus.RESOLVED,
                    "last_activity_at": at,
                    "resolved_at": at,
                }
            )
        )
        minutes = round((at - incident.opened_at).total_seconds() / 60, 1)
        self._store.append(
            "incident.resolved",
            {"resolved_at": at.isoformat(), "duration_minutes": minutes},
            incident_id=incident_id,
        )
        log.info("incident %d resolved after %s min", incident_id, minutes)

    # --- rules --------------------------------------------------------------------------------

    def _same_alert(self, alert: Alert, at: datetime) -> Incident | None:
        incident_id = self._store.incident_of(alert.source, alert.fingerprint)
        incident = None if incident_id is None else self._store.incident(incident_id)
        if incident is None:
            return None
        if incident.status is IncidentStatus.OPEN:
            return incident
        window = timedelta(minutes=self._rules.correlation.window_minutes)
        closed_at = incident.resolved_at or incident.last_activity_at
        return incident if at - closed_at <= window else None

    def _same_host(self, host: str, at: datetime) -> Incident | None:
        window = timedelta(minutes=self._rules.correlation.host_window_minutes)
        candidates = [
            incident
            for incident in self._store.incidents(status=IncidentStatus.OPEN)
            if incident.host == host and abs(at - incident.opened_at) <= window
        ]
        return candidates[-1] if candidates else None

    def _open(self, alert: Alert, host: str | None) -> int:
        maintenance = self._rules.maintenance_for(alert)
        triage = TriageState.PENDING if self._rules.triage.enabled else TriageState.OFF
        investigation = (
            TriageState.PENDING if self._rules.investigation.enabled else TriageState.OFF
        )
        incident_id = self._store.open_incident(
            title=alert.title,
            severity=alert.severity,
            host=host,
            opened_at=alert.observed_at,
            maintenance=maintenance,
            triage=triage,
            investigation=investigation,
        )
        self._store.append(
            "incident.opened",
            {
                "title": alert.title,
                "severity": alert.severity.value,
                "host": host,
                "maintenance": maintenance,
                "opened_at": alert.observed_at.isoformat(),
                "alert": {"source": alert.source, "fingerprint": alert.fingerprint},
            },
            incident_id=incident_id,
        )
        log.info("incident %d opened: %s", incident_id, alert.title)
        return incident_id

    def _join(self, incident: Incident, alert: Alert, reason: str) -> None:
        at = max(alert.observed_at, incident.last_activity_at)
        severity = max(incident.severity, alert.severity, key=SEVERITY_RANK.__getitem__)
        reopened = incident.status is IncidentStatus.RESOLVED
        self._store.save_incident(
            incident.model_copy(
                update={
                    "status": IncidentStatus.OPEN,
                    "resolved_at": None,
                    "last_activity_at": at,
                    "severity": severity,
                }
            )
        )
        if reopened:
            self._store.append(
                "incident.reopened",
                {"reopened_at": alert.observed_at.isoformat()},
                incident_id=incident.id,
            )
        self._store.append(
            "incident.correlated",
            {
                "reason": reason,
                "alert": {
                    "source": alert.source,
                    "fingerprint": alert.fingerprint,
                    "title": alert.title,
                },
            },
            incident_id=incident.id,
        )
        log.info("incident %d: %s joined (same %s)", incident.id, alert.title, reason)

    def _flap(self, incident_id: int, alert: Alert) -> None:
        flap = self._rules.correlation.flap
        earlier = list(
            self._store.events(
                kind="alert.firing",
                source=alert.source,
                fingerprint=alert.fingerprint,
                newest_first=True,
                limit=flap.threshold - 1,
            )
        )
        if len(earlier) < flap.threshold - 1:
            return
        oldest = datetime.fromisoformat(str(earlier[-1].payload.get("observed_at")))
        if alert.observed_at - oldest > timedelta(minutes=flap.window_minutes):
            return
        incident = self._store.incident(incident_id)
        if incident is None or incident.flapping:
            return
        self._store.save_incident(incident.model_copy(update={"flapping": True}))
        self._store.append(
            "incident.flapping",
            {
                "alert": {"source": alert.source, "fingerprint": alert.fingerprint},
                "firings": flap.threshold,
                "window_minutes": flap.window_minutes,
            },
            incident_id=incident_id,
        )
