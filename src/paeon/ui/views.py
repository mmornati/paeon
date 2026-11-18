"""What the console shows, computed from the store. No HTML here.

Everything is read from the event log and its projections. Payloads are redacted again before
they are shown: a webhook body is stored as it came, and the console is a second place where it
could be read.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from paeon import ledger
from paeon.redact import redact
from paeon.rules import Rules
from paeon.schema import Incident, IncidentStatus, StageState, Status
from paeon.store import Event, Store

# Who speaks in a line of the record.
#   in:    it came from outside (an alert, a webhook, an announced change, your feedback)
#   code:  Paeon's own code decided or noted it
#   model: a model said it, or a tool read it for a model
#   out:   it left the server (a notification, a digest, a file written)
Voice = Literal["in", "code", "model", "out"]

_VOICES: dict[str, Voice] = {
    "alert": "in",
    "webhook": "in",
    "change": "in",
    "feedback": "in",
    "incident": "code",
    "triage": "model",
    "investigation": "model",
    "model": "model",
    "tool": "model",
    "notification": "out",
    "digest": "out",
    "postmortem": "out",
}
_CODE_KINDS = {
    "triage.skipped",
    "investigation.skipped",
    "investigation.started",
    "investigation.failed",
    "notification.failed",
    "postmortem.failed",
}

# The filters of the log page: the question each one answers, and the kinds behind it.
FILTERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "in": (
        "Came in",
        ("alert.firing", "alert.resolved", "webhook.received", "change.recorded"),
    ),
    "code": (
        "Decided by code",
        (
            "incident.opened",
            "incident.correlated",
            "incident.reopened",
            "incident.resolved",
            "incident.flapping",
            "triage.skipped",
            "investigation.skipped",
            "investigation.started",
            "investigation.failed",
        ),
    ),
    "model": (
        "Said by a model",
        ("model.call", "triage.verdict", "investigation.completed"),
    ),
    "tool": ("Read by a tool", ("tool.call",)),
    "out": (
        "Left the server",
        (
            "notification.sent",
            "notification.failed",
            "digest.sent",
            "postmortem.drafted",
            "postmortem.failed",
        ),
    ),
    "feedback": ("Your feedback", ("feedback.recorded",)),
}

BAYS: tuple[tuple[str, str, str], ...] = (
    ("triage", "Triage", "No incident is waiting for a verdict."),
    ("investigation", "Investigation", "Nothing is being investigated."),
    ("open", "Open", "No open incident. What fires next lands here or in a bay above."),
    ("resolved", "Resolved", "Nothing was resolved in the last 7 days."),
)
RESOLVED_DAYS = 7
DETAIL_LIMIT = 20_000


def voice_of(kind: str) -> Voice:
    if kind in _CODE_KINDS:
        return "code"
    return _VOICES.get(kind.split(".", 1)[0], "code")


def money(value: float) -> str:
    """A cost in the currency of the configured prices, which Paeon does not know."""
    if value == 0:
        return "0"
    return f"{value:.4f}" if value < 0.1 else f"{value:.2f}"


def span(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 90:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h {minutes % 60:02d}"
    return f"{hours // 24} d"


def _percent(value: Any) -> str:
    return f"{float(value):.0%}" if isinstance(value, int | float) else "?"


def _text(value: Any, fallback: str = "?") -> str:
    return fallback if value is None or value == "" else str(value)


def _plain(value: Any) -> str:
    """A reason code as words: `low_confidence` wraps badly in a narrow cell."""
    return _text(value).replace("_", " ")


def _compact(value: Any, limit: int = 90) -> str:
    text = json.dumps(value, separators=(", ", ": "), sort_keys=True, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def describe(event: Event) -> str:
    """One line for one event. Unknown kinds are shown by name: nothing is hidden."""
    p = event.payload
    alert = _dict(p.get("alert"))
    match event.kind:
        case "alert.firing":
            return f"{_text(p.get('title'))} is firing ({_text(p.get('severity'))})"
        case "alert.resolved":
            return f"{_text(p.get('title'))} resolved"
        case "webhook.received":
            return f"Webhook received from {_text(event.source)}"
        case "change.recorded":
            where = f" on {p['host']}" if p.get("host") else ""
            return f"{_text(p.get('kind'))}: {_text(p.get('title'))}{where}"
        case "incident.opened":
            window = f", in maintenance window {p['maintenance']}" if p.get("maintenance") else ""
            return f"Incident opened{window}"
        case "incident.correlated":
            return f"{_text(alert.get('title'))} joined (same {_text(p.get('reason'))})"
        case "incident.reopened":
            return "Incident reopened: the same alert fired again"
        case "incident.resolved":
            return f"Incident resolved after {_text(p.get('duration_minutes'))} min"
        case "incident.flapping":
            return (
                f"Flagged as flapping: {_text(p.get('firings'))} firings"
                f" within {_text(p.get('window_minutes'))} min"
            )
        case "triage.verdict":
            if p.get("fail_open"):
                said = f" (model said {p['verdict']})" if p.get("verdict") else ""
                return f"Triage failed open ({p['fail_open']}): treated as real{said}"
            return f"Triage verdict: {_text(p.get('verdict'))}, {_percent(p.get('confidence'))}"
        case "triage.skipped":
            return f"Triage skipped ({_text(p.get('skip'))}): treated as real"
        case "investigation.started":
            tools = p.get("tools")
            count = len(tools) if isinstance(tools, list) else 0
            return f"Investigation started with {count} tools"
        case "investigation.skipped":
            return f"Investigation skipped ({_text(p.get('skip'))})"
        case "investigation.failed":
            return (
                f"Investigation failed ({_text(p.get('reason'))})"
                f" after {_text(p.get('tool_calls'), '0')} tool calls"
            )
        case "investigation.completed":
            findings = p.get("findings") if isinstance(p.get("findings"), dict) else {}
            cause = findings.get("root_cause") if isinstance(findings, dict) else None
            sure = _percent(cause.get("confidence")) if isinstance(cause, dict) else "?"
            return (
                f"Investigation completed: {_text(p.get('tool_calls'), '0')} tool calls,"
                f" {sure} sure of the cause"
            )
        case "model.call":
            stage = _text(p.get("stage"))
            if p.get("error"):
                return f"Model call for {stage} failed: {p['error']}"
            return (
                f"Model call for {stage}: {_text(p.get('model'))},"
                f" {_text(p.get('input_tokens'), '0')} in,"
                f" {_text(p.get('output_tokens'), '0')} out"
            )
        case "tool.call":
            return f"{_text(p.get('call'))} {_text(p.get('name'))} {_compact(p.get('input'))}"
        case "notification.sent":
            return f"Sent to ntfy topic {_text(p.get('topic'))}: the {_text(p.get('about'))}"
        case "notification.failed":
            return f"Notification not sent ({_text(p.get('error'))})"
        case "digest.sent":
            return f"Digest sent: {_text(p.get('incidents'), '0')} incidents"
        case "postmortem.drafted":
            prose = "" if p.get("narrative") else ", without the model's prose"
            return f"Postmortem draft written to {_text(p.get('path'))}{prose}"
        case "postmortem.failed":
            return f"Postmortem draft not written ({_text(p.get('error'))})"
        case "feedback.recorded":
            right = "right" if p.get("vote") == "up" else "wrong"
            note = f": {p['note']}" if p.get("note") else ""
            return f"You said triage was {right}{note}"
    return event.kind


@dataclass(frozen=True)
class Row:
    """One event, as a line of the record."""

    id: int
    at: datetime
    kind: str
    voice: Voice
    text: str
    incident_id: int | None
    cost: float | None = None
    duration_ms: int | None = None
    detail: str | None = None
    call: str | None = None  # the citation id of a tool call (c1, c2…)
    pen: bool = False  # the line is the model's own words; a fact about a call is not
    note: str = ""  # what code observed about it, printed after the model's words

    @property
    def tag(self) -> str:
        if self.kind == "tool.call":
            return "tool"
        if self.kind == "feedback.recorded":
            return "you"
        return "sent" if self.voice == "out" else self.voice


def row_of(event: Event, *, detail: bool = False) -> Row:
    p = event.payload
    cost = p.get("cost") if event.kind == "model.call" else None
    took = p.get("duration_ms")
    body: str | None = None
    if detail:
        body = json.dumps(redact(p, max_length=DETAIL_LIMIT), indent=2, sort_keys=True, default=str)
        if len(body) > DETAIL_LIMIT * 2:
            body = body[: DETAIL_LIMIT * 2] + "\n… cut"
    return Row(
        id=event.id,
        at=event.recorded_at,
        kind=event.kind,
        voice=voice_of(event.kind),
        text=str(redact(describe(event), max_length=300)),
        incident_id=event.incident_id,
        cost=float(cost) if isinstance(cost, int | float) else None,
        duration_ms=int(took) if isinstance(took, int | float) else None,
        detail=body,
        call=str(p["call"]) if event.kind == "tool.call" and p.get("call") else None,
        note="the tool answered with an error"
        if event.kind == "tool.call" and p.get("is_error")
        else "",
        pen=event.kind == "tool.call"
        or (event.kind == "triage.verdict" and not p.get("fail_open")),
    )


@dataclass(frozen=True)
class Cell:
    label: str
    value: str  # short: one line of a board strip
    pen: bool = False  # a model said it
    more: str = ""  # the same with its reason, where there is room

    @property
    def full(self) -> str:
        return self.more or self.value


@dataclass(frozen=True)
class Strip:
    """One incident as a progress strip: where it stands and what was said about it."""

    incident: Incident
    bay: str
    standing: str
    cells: tuple[Cell, ...]
    flags: tuple[str, ...]
    rev: int  # id of its last event: changes whenever anything happens to it


def _last(events: Sequence[Event], *kinds: str) -> Event | None:
    return next((event for event in reversed(events) if event.kind in kinds), None)


def _triage_cell(incident: Incident, events: Sequence[Event]) -> Cell:
    match incident.triage:
        case StageState.OFF:
            return Cell("Triage", "off")
        case StageState.PENDING:
            return Cell("Triage", "waiting")
        case StageState.SKIPPED:
            skipped = _last(events, "triage.skipped")
            reason = _plain(skipped.payload.get("skip")) if skipped else "?"
            return Cell("Triage", "skipped", more=f"skipped ({reason})")
    verdict = _last(events, "triage.verdict")
    if verdict is None:
        return Cell("Triage", incident.triage.value)
    p = verdict.payload
    if p.get("fail_open"):
        reason = _plain(p["fail_open"])
        return Cell("Triage", "failed open", more=f"real, failed open ({reason})")
    return Cell("Triage", f"{_text(p.get('verdict'))} {_percent(p.get('confidence'))}", pen=True)


def _investigation_cell(incident: Incident, events: Sequence[Event]) -> Cell:
    label = "Investigation"
    match incident.investigation:
        case StageState.OFF:
            return Cell(label, "off")
        case StageState.PENDING:
            started = _last(events, "investigation.started")
            return Cell(label, "running" if started else "queued")
        case StageState.SKIPPED:
            skipped = _last(events, "investigation.skipped")
            reason = _plain(skipped.payload.get("skip")) if skipped else "?"
            return Cell(label, "skipped", more=f"skipped ({reason})")
        case StageState.FAILED:
            failed = _last(events, "investigation.failed")
            reason = _plain(failed.payload.get("reason")) if failed else "?"
            return Cell(label, "failed", more=f"failed ({reason.split(':', 1)[0]})")
    done = _last(events, "investigation.completed")
    findings = findings_of(done)
    if done is None or findings is None:
        return Cell(label, incident.investigation.value)
    sure = _percent(findings.get("root_cause", {}).get("confidence"))
    calls = _text(done.payload.get("tool_calls"), "0")
    return Cell(label, f"{sure}, {calls} calls", pen=True, more=f"{sure} sure, {calls} calls")


def findings_of(event: Event | None) -> dict[str, Any] | None:
    if event is None:
        return None
    findings = event.payload.get("findings")
    if not isinstance(findings, dict) or not isinstance(findings.get("root_cause"), dict):
        return None
    return findings


def bay_of(incident: Incident) -> str:
    if incident.status is IncidentStatus.RESOLVED:
        return "resolved"
    if incident.triage is StageState.PENDING:
        return "triage"
    if incident.investigation is StageState.PENDING:
        return "investigation"
    return "open"


def strip_of(incident: Incident, events: Sequence[Event], now: datetime) -> Strip:
    firing = sum(1 for alert in incident.alerts if alert.status is Status.FIRING)
    cost = sum(float(e.payload.get("cost") or 0) for e in events if e.kind == "model.call")
    cells = [
        Cell("Host", incident.host or "none"),
        Cell("Alerts", f"{firing} of {len(incident.alerts)} firing"),
        _triage_cell(incident, events),
        _investigation_cell(incident, events),
    ]
    # Always the same six cells in the same order: a box sits at the same place on every strip.
    wording = {StageState.DONE: "draft written", StageState.PENDING: "waiting"}
    cells.append(Cell("Postmortem", wording.get(incident.postmortem, incident.postmortem.value)))
    cells.append(Cell("Cost", money(cost)))
    if incident.resolved_at is not None:
        standing = f"resolved in {span(incident.resolved_at - incident.opened_at)}"
    else:
        standing = f"open {span(now - incident.opened_at)}"
    flags = []
    if incident.flapping:
        flags.append("flapping")
    if incident.maintenance:
        flags.append(f"maintenance: {incident.maintenance}")
    return Strip(
        incident=incident,
        bay=bay_of(incident),
        standing=standing,
        cells=tuple(cells),
        flags=tuple(flags),
        rev=events[-1].id if events else 0,
    )


@dataclass(frozen=True)
class Bay:
    key: str
    name: str
    empty: str
    strips: tuple[Strip, ...]


def board(store: Store, now: datetime) -> tuple[Bay, ...]:
    """Every open incident, and those resolved in the last days, each in its bay."""
    wanted = {incident.id: incident for incident in store.incidents(status=IncidentStatus.OPEN)}
    recent = now - timedelta(days=RESOLVED_DAYS)
    for incident in store.incidents(status=IncidentStatus.RESOLVED, active_since=recent):
        wanted[incident.id] = incident
    strips: dict[str, list[Strip]] = defaultdict(list)
    for incident in wanted.values():
        events = list(store.events(incident_id=incident.id))
        strip = strip_of(incident, events, now)
        strips[strip.bay].append(strip)
    for found in strips.values():
        found.sort(key=lambda strip: strip.incident.last_activity_at, reverse=True)
    return tuple(Bay(key, name, empty, tuple(strips[key])) for key, name, empty in BAYS)


@dataclass(frozen=True)
class Analysis:
    """What the investigator handed in, with the facts of the run around it."""

    findings: dict[str, Any]
    model: str
    tool_calls: int
    duration: str
    unverified: int


@dataclass(frozen=True)
class IncidentView:
    strip: Strip
    rows: tuple[Row, ...]
    verdict: dict[str, Any] | None
    triage_skip: str | None
    feedback: str | None
    analysis: Analysis | None
    failure: str | None
    postmortem: str | None
    cost: float
    calls: frozenset[str]


def incident_view(store: Store, incident: Incident, now: datetime) -> IncidentView:
    events = list(store.events(incident_id=incident.id))
    verdict = _last(events, "triage.verdict")
    skipped = _last(events, "triage.skipped")
    voted = _last(events, "feedback.recorded")
    done = _last(events, "investigation.completed")
    failed = _last(events, "investigation.failed", "investigation.skipped")
    drafted = _last(events, "postmortem.drafted")
    findings = findings_of(done)
    analysis = None
    if done is not None and findings is not None:
        findings = redact(findings, max_length=4000)
        evidence = [item for item in findings.get("evidence") or [] if isinstance(item, dict)]
        analysis = Analysis(
            findings=findings,
            model=_text(done.payload.get("model")),
            tool_calls=int(done.payload.get("tool_calls") or 0),
            duration=span(timedelta(seconds=float(done.payload.get("duration_seconds") or 0))),
            unverified=sum(1 for item in evidence if not item.get("verified")),
        )
    failure = None
    if analysis is None and failed is not None:
        failure = str(redact(describe(failed), max_length=300))
    return IncidentView(
        strip=strip_of(incident, events, now),
        rows=tuple(row_of(event, detail=True) for event in events),
        verdict=None if verdict is None else redact(verdict.payload),
        triage_skip=None if skipped is None else _text(skipped.payload.get("skip")),
        feedback=None if voted is None else str(voted.payload.get("vote")),
        analysis=analysis,
        failure=failure,
        postmortem=None if drafted is None else _text(drafted.payload.get("path")),
        cost=sum(float(e.payload.get("cost") or 0) for e in events if e.kind == "model.call"),
        calls=frozenset(str(e.payload.get("call")) for e in events if e.kind == "tool.call"),
    )


# --- the ledger -----------------------------------------------------------------------------


@dataclass
class Tally:
    calls: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0

    def add(self, payload: dict[str, Any]) -> None:
        self.calls += 1
        self.errors += 1 if payload.get("error") else 0
        self.input_tokens += int(payload.get("input_tokens") or 0)
        self.output_tokens += int(payload.get("output_tokens") or 0)
        self.cached_tokens += int(payload.get("cached_tokens") or 0)
        self.cost += float(payload.get("cost") or 0)


@dataclass(frozen=True)
class Budget:
    """Where one stage stops calling a model: when the day's total, all stages, reaches it."""

    stage: str
    limit: float
    spent: float  # the day's total, not this stage's share of it

    @property
    def share(self) -> float:
        """The day's total on the way to this stop, from 0 to 100 for the bar."""
        return 100.0 if self.limit <= 0 else min(self.spent / self.limit * 100, 100.0)

    @property
    def left(self) -> float:
        return max(self.limit - self.spent, 0.0)

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.limit


@dataclass(frozen=True)
class Ledger:
    days: int
    today: float
    budgets: tuple[Budget, ...]
    total: Tally
    by_model: tuple[tuple[str, str, Tally], ...]  # stage, model that answered
    by_day: tuple[tuple[str, Tally, float], ...]  # date, tally, share of the dearest day
    verdicts: tuple[tuple[str, int], ...]
    fail_open: tuple[tuple[str, int], ...]
    rated: int
    right: int
    recent: tuple[Row, ...]


def _counted(values: Iterable[str]) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def ledger_view(store: Store, rules: Rules, now: datetime, *, days: int = 14) -> Ledger:
    """Model calls of the last days. Days are UTC, like the budgets that are enforced."""
    midnight = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    since = midnight - timedelta(days=days - 1)
    calls = list(store.events(kind="model.call", recorded_since=since))
    total = Tally()
    by_model: dict[tuple[str, str], Tally] = defaultdict(Tally)
    by_day: dict[str, Tally] = {
        (since + timedelta(days=offset)).date().isoformat(): Tally() for offset in range(days)
    }
    for event in calls:
        p = event.payload
        total.add(p)
        who = _text(p.get("model") or p.get("model_requested"))
        by_model[(_text(p.get("stage")), who)].add(p)
        day = event.recorded_at.astimezone(UTC).date().isoformat()
        by_day.setdefault(day, Tally()).add(p)
    dearest = max((tally.cost for tally in by_day.values()), default=0.0)
    today = ledger.spent_today(store, now)
    budgets = tuple(
        Budget(stage, limit, today)
        for stage, enabled, limit in (
            ("triage", rules.triage.enabled, rules.triage.daily_budget),
            ("investigation", rules.investigation.enabled, rules.investigation.daily_budget),
            ("postmortems", rules.postmortems.enabled, rules.postmortems.daily_budget),
        )
        if enabled and limit is not None
    )
    verdicts = list(store.events(kind="triage.verdict", recorded_since=since))
    votes: dict[int, str] = {}
    for event in store.events(kind="feedback.recorded", recorded_since=since):
        if event.incident_id is not None:
            votes[event.incident_id] = str(event.payload.get("vote"))
    return Ledger(
        days=days,
        today=today,
        budgets=budgets,
        total=total,
        by_model=tuple(
            (stage, model, tally)
            for (stage, model), tally in sorted(by_model.items(), key=lambda item: -item[1].cost)
        ),
        by_day=tuple(
            (day, tally, 0.0 if dearest <= 0 else tally.cost / dearest * 100)
            for day, tally in sorted(by_day.items(), reverse=True)
            if tally.calls
        ),
        verdicts=_counted(
            str(e.payload.get("effective_verdict"))
            for e in verdicts
            if not e.payload.get("fail_open")
        ),
        fail_open=_counted(
            str(e.payload["fail_open"]) for e in verdicts if e.payload.get("fail_open")
        ),
        rated=len(votes),
        right=sum(1 for vote in votes.values() if vote == "up"),
        recent=tuple(
            row_of(event) for event in sorted(calls, key=lambda e: e.id, reverse=True)[:25]
        ),
    )


def stages(rules: Rules) -> tuple[tuple[str, str], ...]:
    """What is switched on, for the bar: the console must say what Paeon is not doing."""
    return (
        ("Triage", "shadow" if rules.triage.enabled else "off"),
        ("Investigation", "read-only" if rules.investigation.enabled else "off"),
        ("Postmortems", "drafts" if rules.postmortems.enabled else "off"),
    )
