"""The daily digest: what happened, what triage would have said, and whether it was right.

In shadow mode the digest is the whole output of triage. Each line can be answered with a
thumbs-up or a thumbs-down (`paeon feedback`, or `POST /incidents/{id}/feedback`); the answers are
events, and they are the labelled data that tells whether triage can be trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx2 as httpx

from paeon.rules import Ntfy
from paeon.schema import Incident
from paeon.store import Store


@dataclass(frozen=True)
class DigestLine:
    incident: Incident
    verdict: str | None  # None when the incident was not triaged
    effective_verdict: str | None
    confidence: float | None
    reason: str | None
    fail_open: str | None
    feedback: str | None  # "up", "down" or None

    @property
    def demoted(self) -> bool:
        """Would this have left the phone and gone to the digest only?"""
        return self.effective_verdict == "noise"


@dataclass(frozen=True)
class Digest:
    since: datetime
    until: datetime
    lines: tuple[DigestLine, ...]
    model_calls: int
    cost: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "model_calls": self.model_calls,
            "cost": self.cost,
            "incidents": [
                {
                    "incident": line.incident.model_dump(mode="json"),
                    "verdict": line.verdict,
                    "effective_verdict": line.effective_verdict,
                    "confidence": line.confidence,
                    "reason": line.reason,
                    "fail_open": line.fail_open,
                    "feedback": line.feedback,
                }
                for line in self.lines
            ],
        }


def _last(store: Store, incident_id: int, kind: str) -> dict[str, Any] | None:
    for event in store.events(incident_id=incident_id, kind=kind, newest_first=True, limit=1):
        return event.payload
    return None


def build(store: Store, *, since: datetime, until: datetime) -> Digest:
    lines = []
    for incident in store.incidents(active_since=since):
        if incident.opened_at > until:
            continue
        verdict = _last(store, incident.id, "triage.verdict") or _last(
            store, incident.id, "triage.skipped"
        )
        feedback = _last(store, incident.id, "feedback.recorded")
        verdict = verdict or {}
        skipped = "skip" in verdict
        lines.append(
            DigestLine(
                incident=incident,
                verdict=verdict.get("verdict"),
                effective_verdict=verdict.get("effective_verdict"),
                confidence=verdict.get("confidence"),
                reason=verdict.get("reason"),
                fail_open=verdict.get("skip") if skipped else verdict.get("fail_open"),
                feedback=None if feedback is None else feedback.get("vote"),
            )
        )
    calls = list(store.events(kind="model.call", recorded_since=since))
    return Digest(
        since=since,
        until=until,
        lines=tuple(lines),
        model_calls=len(calls),
        cost=round(sum(float(call.payload.get("cost") or 0) for call in calls), 6),
    )


def _line(line: DigestLine) -> str:
    incident = line.incident
    flags = [
        flag
        for flag, on in (
            ("flapping", incident.flapping),
            (f"maintenance: {incident.maintenance}", incident.maintenance is not None),
            (f"{len(incident.alerts)} alerts", len(incident.alerts) > 1),
            (incident.status.value, True),
        )
        if on
    ]
    if line.effective_verdict is None:
        verdict = "not triaged"
    elif line.verdict is None or line.fail_open:
        verdict = f"{line.effective_verdict} (fail open: {line.fail_open})"
    else:
        verdict = f"{line.verdict} {line.confidence:.2f}" if line.confidence is not None else ""
    vote = {"up": " 👍", "down": " 👎"}.get(line.feedback or "", "")
    text = f"#{incident.id} {incident.title} [{', '.join(flags)}] → {verdict}{vote}"
    return f"{text}\n    {line.reason}" if line.reason else text


def render(digest: Digest) -> str:
    """Plain text, for a terminal or a notification."""
    if not digest.lines:
        return "No incident in the period."
    demoted = [line for line in digest.lines if line.demoted]
    kept = [line for line in digest.lines if not line.demoted]
    parts = [
        f"{len(digest.lines)} incidents, {len(demoted)} that triage would have demoted"
        f" (shadow mode: nothing was). {digest.model_calls} model calls, cost {digest.cost:g}."
    ]
    if demoted:
        parts += ["", "Would have been demoted:", *(_line(line) for line in demoted)]
    if kept:
        parts += ["", "Kept:", *(_line(line) for line in kept)]
    parts += ["", "Was triage right? paeon feedback <id> up|down"]
    return "\n".join(parts)


def publish_ntfy(
    ntfy: Ntfy,
    token: str | None,
    *,
    title: str,
    message: str,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Publish to an ntfy topic. Raises RuntimeError without the URL or the token in it."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = {
        "topic": ntfy.topic,
        "title": title,
        "message": message[:3500],  # ntfy refuses bodies over 4096 bytes
        "priority": 2,
        "tags": ["clipboard"],
    }
    try:
        with httpx.Client(timeout=15, transport=transport) as http:
            status = http.post(ntfy.url, json=body, headers=headers).status_code
    except httpx.HTTPError as exc:
        raise RuntimeError(f"ntfy request failed: {type(exc).__name__}") from None
    if status >= 400:
        raise RuntimeError(f"ntfy answered HTTP {status}")
