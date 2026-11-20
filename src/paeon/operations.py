"""What a person does to an incident from the console: ask again, or close it.

An incident closes by itself when every one of its alerts resolves. Some never send that
resolution: OpenObserve writes a `normal` row only if the alert is evaluated while healthy,
uptime-kuma notifies a change of state and nothing else. Two ways out, both recorded:

- **Re-check** asks each source that can answer what the alert is right now. An alert found
  healthy goes through the recorder like any resolution, so the incident resolves the usual way.
- **Close** is a person's word: each alert still firing is marked resolved, the incident
  resolves, and the log says who and why. If the alert fires again, it opens an incident again.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from paeon.recorder import Recorder
from paeon.schema import Alert, IncidentStatus, Status
from paeon.store import Store

log = logging.getLogger(__name__)

Answer = Literal["firing", "resolved", "unknown"]
# One source's read of one alert: what it is now, and a short line saying what was asked.
Checker = Callable[[str, str], tuple[Answer, str]]  # (fingerprint, title)


class OperationError(ValueError):
    """The incident cannot take this: it is unknown, or already resolved."""


@dataclass(frozen=True)
class Checked:
    source: str
    title: str
    answer: Answer
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "title": self.title,
            "answer": self.answer,
            "detail": self.detail,
        }


class Incidents:
    def __init__(
        self,
        store: Store,
        recorder: Recorder,
        checkers: Callable[[str], Checker | None],
        clock: Callable[[], datetime],
    ) -> None:
        """`checkers`: the reader of a source, by its name, or None if it cannot be asked.
        Looked up each time: sources added in the console can be asked too."""
        self._store = store
        self._recorder = recorder
        self._checker = checkers
        self._clock = clock

    def can_check(self, source: str) -> bool:
        return self._checker(source) is not None

    def recheck(self, incident_id: int, *, by: str) -> list[Checked]:
        """Ask each firing alert's source what it is now. Resolutions are recorded."""
        incident = self._open(incident_id)
        found: list[Checked] = []
        for member in incident.alerts:
            if member.status is not Status.FIRING:
                continue
            checker = self._checker(member.source)
            if checker is None:
                found.append(
                    Checked(member.source, member.title, "unknown", "this source cannot be asked")
                )
                continue
            try:
                answer, detail = checker(member.fingerprint, member.title)
            except Exception as exc:  # a source that fails is an answer too: unknown
                log.warning("re-check of %s/%s failed", member.source, member.fingerprint)
                answer, detail = "unknown", f"the source did not answer: {type(exc).__name__}"
            found.append(Checked(member.source, member.title, answer, detail))
            if answer == "resolved":
                self._resolve(member.source, member.fingerprint, reason="re-check")
        self._store.append(
            "incident.rechecked",
            {"by": by, "alerts": [one.as_dict() for one in found]},
            incident_id=incident_id,
        )
        return found

    def close(self, incident_id: int, *, by: str, note: str) -> None:
        """Resolve an incident on a person's word, with the reason they gave."""
        incident = self._open(incident_id)
        firing = [m for m in incident.alerts if m.status is Status.FIRING]
        self._store.append(
            "incident.closed",
            {"by": by, "note": note.strip()[:1000], "alerts": len(firing)},
            incident_id=incident_id,
        )
        for member in firing:
            self._resolve(member.source, member.fingerprint, reason="closed by hand")
        if (found := self._store.incident(incident_id)) is not None and (
            found.status is not IncidentStatus.RESOLVED
        ):  # an incident without a firing alert left, but still open: settle it here
            now = self._clock()
            self._store.save_incident(
                found.model_copy(update={"status": IncidentStatus.RESOLVED, "resolved_at": now})
            )
            minutes = round((now - found.opened_at).total_seconds() / 60, 1)
            self._store.append(
                "incident.resolved",
                {"resolved_at": now.isoformat(), "duration_minutes": minutes},
                incident_id=incident_id,
            )

    def _open(self, incident_id: int) -> Any:
        incident = self._store.incident(incident_id)
        if incident is None:
            raise OperationError("no such incident")
        if incident.status is IncidentStatus.RESOLVED:
            raise OperationError("the incident is already resolved")
        return incident

    def _resolve(self, source: str, fingerprint: str, *, reason: str) -> None:
        """Feed a resolution through the recorder, built from the alert's last firing."""
        last = next(
            iter(
                self._store.events(
                    kinds=("alert.firing",),
                    source=source,
                    fingerprint=fingerprint,
                    newest_first=True,
                    limit=1,
                )
            ),
            None,
        )
        state = self._store.alert_state(source, fingerprint)
        now = self._clock()
        if state is not None and state.last_seen >= now:
            now = state.last_seen + timedelta(microseconds=1)  # the recorder keeps time moving
        base: dict[str, Any] = dict(last.payload) if last is not None else {}
        fields = set(Alert.model_fields)
        base = {key: value for key, value in base.items() if key in fields}
        labels = {str(k): str(v) for k, v in (base.get("labels") or {}).items()}
        alert = Alert(
            **{
                **base,
                "source": source,
                "fingerprint": fingerprint,
                "status": Status.RESOLVED,
                "title": str(base.get("title") or fingerprint),
                "labels": {**labels, "resolved_by": reason},
                "starts_at": base.get("starts_at") or now,
                "observed_at": now,
                "raw": {},
            }
        )
        self._recorder.observe(alert)


# --- what each kind of source can be asked ---------------------------------------------------


_ALERT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def openobserve_checker(
    search: Callable[[str, int, int, int], list[dict[str, Any]]],
    clock: Callable[[], datetime],
    *,
    days: int = 7,
) -> Checker:
    """The latest evaluation of the alert in OpenObserve's `triggers` stream."""
    from paeon.schema import from_micros, to_micros
    from paeon.sources.openobserve import row_to_alert

    def check(fingerprint: str, title: str) -> tuple[Answer, str]:
        if _ALERT_ID.fullmatch(fingerprint) is None:
            return "unknown", "the alert id is not one OpenObserve gives"
        now = clock()
        # The id was checked against _ALERT_ID above: letters, digits, - and _ only.
        sql = (
            "SELECT _timestamp, key, status, start_time, end_time FROM triggers"  # noqa: S608
            f" WHERE key LIKE '%/{fingerprint}' ORDER BY _timestamp DESC"
        )
        rows = search(
            sql,
            to_micros(now - timedelta(days=days)),
            to_micros(now),
            1,
        )
        if not rows:
            return "unknown", f"no evaluation in the last {days} days"
        row = rows[0]
        alert = row_to_alert(row, source="openobserve")
        when = (
            from_micros(row["_timestamp"]).strftime("%d %b %H:%M")
            if isinstance(row.get("_timestamp"), int)
            else "?"
        )
        if alert is None:
            return "unknown", f"its last evaluation ({when}) says {row.get('status')}"
        if alert.status is Status.RESOLVED:
            return "resolved", f"normal at its last evaluation, {when}"
        return "firing", f"still firing at its last evaluation, {when}"

    return check


def tool_checker(run: Callable[[dict[str, Any]], Any], kind: str) -> Checker:
    """A source's own read-only tool, asked about one alert: uptime-kuma's monitor states or
    Alertmanager's active alerts."""

    def check(fingerprint: str, title: str) -> tuple[Answer, str]:
        if kind == "alertmanager":
            # Narrowed by the alert's name, then read whole: "absent" means resolved only when
            # nothing was cut from the list and the answer has the expected shape.
            found = run({"text": title.lower()})
            alerts = found.get("alerts") if isinstance(found, dict) else None
            count = found.get("count") if isinstance(found, dict) else None
            if not isinstance(alerts, list) or not isinstance(count, int):
                return "unknown", "Alertmanager's answer was not the expected list"
            for entry in alerts:
                if isinstance(entry, dict) and entry.get("fingerprint") == fingerprint:
                    return "firing", f"Alertmanager lists it as {entry.get('state') or 'active'}"
            if count > len(alerts):
                return "unknown", "Alertmanager lists more alerts of that name than were read"
            return "resolved", "Alertmanager no longer lists it"
        monitors = (run({"text": title.lower()}) or {}).get("monitors") or []
        for monitor in monitors:
            if str(monitor.get("name")) == title:
                state = str(monitor.get("status") or "unknown")
                answer: Answer = (
                    "resolved" if state == "up" else "firing" if state == "down" else "unknown"
                )
                return answer, f"uptime-kuma says {state}"
        return "unknown", "uptime-kuma has no monitor of that name"

    return check
