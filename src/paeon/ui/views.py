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

from paeon import autonomy, ledger, remediation
from paeon import postmortem as postmortems
from paeon.channels import Channels
from paeon.console_sources import Sources
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
    "approval": "in",
    "console": "in",
}
_CODE_KINDS = {
    "triage.skipped",
    "investigation.skipped",
    "investigation.started",
    "investigation.failed",
    "notification.failed",
    "postmortem.failed",
    "postmortem.commit_failed",
    "approval.requested",
    "approval.expired",
    "investigation.fallback",
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
    "act": (
        "Plans and actions",
        (
            "runbook.matched",
            "runbook.skipped",
            "runbook.failed",
            "plan.proposed",
            "plan.refused",
            "plan.not_proposed",
            "plan.cancelled",
            "plan.completed",
            "change.proposed",
            "change.failed",
            "approval.requested",
            "approval.granted",
            "approval.declined",
            "approval.expired",
            "action.started",
            "action.verified",
            "action.failed",
            "action.rolled_back",
            "action.undone",
            "autonomy.demoted",
            "autonomy.reinstated",
            "killswitch.engaged",
            "killswitch.released",
        ),
    ),
    "out": (
        "Left the server",
        (
            "notification.sent",
            "notification.failed",
            "digest.sent",
            "postmortem.drafted",
            "postmortem.failed",
            "postmortem.committed",
            "postmortem.commit_failed",
        ),
    ),
    "feedback": ("Your feedback", ("feedback.recorded",)),
}

BAYS: tuple[tuple[str, str, str], ...] = (
    ("triage", "Triage", "No incident is waiting for a verdict."),
    ("investigation", "Investigation", "Nothing is being investigated."),
    ("approval", "Your answer", "No plan is waiting for your Go."),
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
            sent_to = _text(p.get("to") or p.get("topic"))  # `topic`: older entries
            return f"Sent by {_text(p.get('channel'))} to {sent_to}: the {_text(p.get('about'))}"
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
            what = {"root_cause": "the root cause", "action": "the action"}.get(
                str(p.get("about")), "triage"
            )
            return f"You said {what} was {right}{note}"
        case "investigation.fallback":
            return (
                f"First model failed ({_text(p.get('reason'))}): starting again with"
                f" {_text(p.get('to'))}"
            )
        case "postmortem.committed":
            pushed = " and pushed" if p.get("pushed") else ""
            return f"Postmortem draft committed{pushed}"
        case "postmortem.commit_failed":
            return f"Postmortem draft not committed (git {_text(p.get('step'))} failed)"
        case "runbook.matched":
            return f"Runbook {_text(p.get('runbook'))} matched: both models are skipped"
        case "runbook.skipped":
            return f"Runbook not run ({_text(p.get('reason'))})"
        case "runbook.failed":
            return f"Runbook gave up ({_text(p.get('reason'))})"
        case "plan.proposed":
            held = p.get("held_back_by")
            why = f" ({'; '.join(map(str, held))})" if isinstance(held, list) and held else ""
            return f"Plan at level {_text(p.get('level'))}: {_steps(p)}{why}"
        case "plan.refused":
            return f"The executor refused the plan: {_text(p.get('reason'))}"
        case "plan.not_proposed":
            return f"The plan did not reach the executor ({_text(p.get('reason'))})"
        case "plan.cancelled":
            return f"Plan cancelled: {_text(p.get('reason'))}"
        case "plan.completed":
            how = "without asking" if p.get("level") == "auto" else "after your Go"
            if p.get("outcome") == "verified":
                return f"Plan ran {how} and verified: {_steps(p)}"
            return f"Plan ran {how} and failed ({_text(p.get('reason'))})"
        case "change.proposed":
            where = "a pull request is open" if p.get("url") else "the branch is pushed"
            return f"Change proposed, nothing applied: {_steps(p)} ({where})"
        case "change.failed":
            return f"The change could not be proposed: {_text(p.get('reason'))}"
        case "approval.requested":
            return "Go or No-go asked on your phone"
        case "approval.granted":
            return "You said Go"
        case "approval.declined":
            return "You said No-go"
        case "approval.expired":
            return "No answer in time: nothing was run"
        case "action.started":
            return f"Running {_text(p.get('step'))}"
        case "action.verified":
            return f"{_text(p.get('step'))} worked: the container is up and healthy"
        case "action.failed":
            return f"{_text(p.get('step'))} failed: {_text(p.get('reason'))}"
        case "action.rolled_back":
            return f"{_text(p.get('step'))} was rolled back"
        case "action.undone":
            return f"{_text(p.get('step'))} was undone at your request"
        case "autonomy.demoted":
            return (
                f"{_text(p.get('action'))}({_text(p.get('target'))}) asks for approval from"
                f" now on: {_text(p.get('reason'))}"
            )
        case "autonomy.reinstated":
            return f"{_text(p.get('action'))}({_text(p.get('target'))}) is reinstated"
        case "console.channel.saved":
            verb = "added" if p.get("created") else "changed"
            carries = p.get("carries")
            what = ", ".join(map(str, carries)) if isinstance(carries, list) and carries else ""
            return (
                f"Channel {_text(p.get('name'))} ({_text(p.get('channel'))}) {verb} in the"
                f" console by {_text(p.get('by'))}: carries {what or 'nothing'}"
            )
        case "console.source.saved":
            verb = "added" if p.get("created") else "changed"
            return (
                f"Source {_text(p.get('name'))} ({_text(p.get('service'))}) {verb} in the"
                f" console by {_text(p.get('by'))}"
            )
        case "console.source.removed":
            return f"Source {_text(p.get('name'))} removed in the console by {_text(p.get('by'))}"
        case "console.channel.removed":
            return f"Channel {_text(p.get('name'))} removed in the console by {_text(p.get('by'))}"
        case "console.signin":
            return "Signed in to the console"
        case "console.signin_locked":
            return "Sign-in paused for a minute: too many wrong passwords"
        case "killswitch.engaged":
            return f"Automation stopped (from the {_text(p.get('via'))})"
        case "killswitch.released":
            return f"Automation resumed (from the {_text(p.get('via'))})"
    return event.kind


def _link(value: Any) -> str | None:
    """A URL only if it is one a page may link to: the forge's, over TLS."""
    return value if isinstance(value, str) and value.startswith("https://") else None


def _steps(payload: dict[str, Any]) -> str:
    steps = payload.get("steps")
    return ", ".join(map(str, steps)) if isinstance(steps, list) and steps else "no step"


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
        if self.kind == "feedback.recorded" or self.kind.startswith("console."):
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


_TONES: dict[str, str] = {
    **dict.fromkeys(("off", "none", "skipped", "recorded", "suggested", "not sent"), "mute"),
    **dict.fromkeys(("waiting", "queued", "running", "proposed"), "active"),
    **dict.fromkeys(("failed", "failed open", "refused", "expired", "declined"), "bad"),
    **dict.fromkeys(("verified", "draft written", "done"), "good"),
    **dict.fromkeys(("cancelled", "undone"), "warn"),
}


@dataclass(frozen=True)
class Cell:
    label: str
    value: str  # short: one line of a board strip
    pen: bool = False  # a model said it
    more: str = ""  # the same with its reason, where there is room

    @property
    def full(self) -> str:
        return self.more or self.value

    @property
    def tone(self) -> str:
        """How the value reads at a glance: the console colours a state, never a sentence."""
        return _TONES.get(self.value, "plain")


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


@dataclass(frozen=True)
class PlanView:
    """The last plan of an incident: what was proposed, at which level, and what became of it."""

    steps: tuple[str, ...]
    origin: str
    level: str
    held_back_by: tuple[str, ...]
    state: str  # one word, for a board strip
    story: str  # one sentence, for the incident page
    waiting: bool = False


_AFTER_PLAN = (
    "approval.requested",
    "approval.granted",
    "approval.declined",
    "approval.expired",
    "plan.cancelled",
    "plan.completed",
    "change.proposed",
    "change.failed",
    "action.started",
    "action.undone",
)


def plan_of(events: Sequence[Event]) -> PlanView | None:
    proposed = _last(events, "plan.proposed", "plan.refused", "plan.not_proposed")
    if proposed is None:
        return None
    p = proposed.payload
    steps = tuple(str(step) for step in p.get("steps") or [])
    origin = f"runbook {p['runbook']}" if p.get("runbook") else str(p.get("origin") or "")
    if proposed.kind != "plan.proposed":
        refused = proposed.kind == "plan.refused"
        return PlanView(
            steps,
            origin,
            "none",
            (),
            "refused" if refused else "not sent",
            f"The executor refused it: {_text(p.get('reason'))}."
            if refused
            else f"It did not reach the executor ({_text(p.get('reason'))}).",
        )
    level = str(p.get("level"))
    held = tuple(str(reason) for reason in p.get("held_back_by") or [])
    later = {
        e.kind: e
        for e in events
        if e.id > proposed.id
        and e.kind in _AFTER_PLAN
        and e.payload.get("proposal") == p.get("proposal")
    }
    state, story, waiting = "recorded", "Recorded only: nothing was sent and nothing ran.", False
    if level == "suggest":
        state, story = "suggested", "Suggested only: it is in the analysis, with no button."
    if "approval.requested" in later:
        state, waiting = "waiting", True
        story = "Waiting for your Go or No-go, on the notification sent to your phone."
    if "approval.declined" in later:
        state, story, waiting = "declined", "You said No-go. Nothing ran.", False
    if "approval.expired" in later:
        state, story, waiting = "expired", "Nobody answered in time. Nothing ran.", False
    if "plan.cancelled" in later:
        reason = _text(later["plan.cancelled"].payload.get("reason"))
        state, story, waiting = "cancelled", f"Cancelled: {reason}. Nothing ran.", False
    if "change.proposed" in later:
        url = later["change.proposed"].payload.get("url")
        where = "A pull request is open" if url else "A branch is pushed"
        state, waiting = "proposed", False
        story = f"{where} on your configuration repository. Nothing is applied until you merge."
    elif "change.failed" in later:
        reason = _text(later["change.failed"].payload.get("reason"))
        state, story, waiting = "failed", f"The change could not be proposed: {reason}.", False
    elif "approval.granted" in later or level == "auto":
        how = "You said Go." if "approval.granted" in later else "It ran without asking (auto)."
        state, story, waiting = "running", f"{how} The executor is running it.", False
        done = later.get("plan.completed")
        if done is not None and done.payload.get("outcome") == "verified":
            state, story = "verified", f"{how} It ran, and the result was verified."
        elif done is not None:
            state = "failed"
            story = f"{how} It ran and did not work: {_text(done.payload.get('reason'))}."
        if "action.undone" in later:
            state, story = "undone", f"{how} It ran, and you undid it."
    return PlanView(steps, origin, level, held, state, story, waiting)


def _plan_cells(plan: PlanView | None, acting: bool) -> tuple[Cell, Cell]:
    if plan is None:
        return Cell("Plan", "none" if acting else "off"), Cell("Action", "none")
    count = len(plan.steps)
    first = plan.steps[0] if plan.steps else "no step"
    short = first if count <= 1 else f"{first} +{count - 1}"
    return (
        Cell("Plan", short, more=", ".join(plan.steps)),
        Cell("Action", plan.state, more=f"{plan.state} (level {plan.level})"),
    )


def findings_of(event: Event | None) -> dict[str, Any] | None:
    if event is None:
        return None
    findings = event.payload.get("findings")
    if not isinstance(findings, dict) or not isinstance(findings.get("root_cause"), dict):
        return None
    return findings


def bay_of(incident: Incident, plan: PlanView | None = None) -> str:
    if incident.status is IncidentStatus.RESOLVED:
        return "resolved"
    if incident.triage is StageState.PENDING:
        return "triage"
    if incident.investigation is StageState.PENDING:
        return "investigation"
    if plan is not None and plan.waiting:
        return "approval"
    return "open"


def strip_of(
    incident: Incident, events: Sequence[Event], now: datetime, *, acting: bool = False
) -> Strip:
    firing = sum(1 for alert in incident.alerts if alert.status is Status.FIRING)
    cost = sum(float(e.payload.get("cost") or 0) for e in events if e.kind == "model.call")
    cells = [
        Cell("Host", incident.host or "none"),
        Cell("Alerts", f"{firing} of {len(incident.alerts)} firing"),
        _triage_cell(incident, events),
        _investigation_cell(incident, events),
    ]
    plan = plan_of(events)
    if acting or plan is not None:
        cells += _plan_cells(plan, acting)
    # Always the same cells in the same order: a box sits at the same place on every strip.
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
        bay=bay_of(incident, plan),
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


def board(store: Store, now: datetime, *, acting: bool = False) -> tuple[Bay, ...]:
    """Every open incident, and those resolved in the last days, each in its bay."""
    wanted = {incident.id: incident for incident in store.incidents(status=IncidentStatus.OPEN)}
    recent = now - timedelta(days=RESOLVED_DAYS)
    for incident in store.incidents(status=IncidentStatus.RESOLVED, active_since=recent):
        wanted[incident.id] = incident
    strips: dict[str, list[Strip]] = defaultdict(list)
    for incident in wanted.values():
        events = list(store.events(incident_id=incident.id))
        strip = strip_of(incident, events, now, acting=acting)
        strips[strip.bay].append(strip)
    for found in strips.values():
        found.sort(key=lambda strip: strip.incident.last_activity_at, reverse=True)
    return tuple(
        Bay(key, name, empty, tuple(strips[key]))
        for key, name, empty in BAYS
        if acting or key != "approval"
    )


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
    plan: PlanView | None = None
    changes: tuple[tuple[str, str | None], ...] = ()  # what was proposed, and the pull request


def incident_view(
    store: Store, incident: Incident, now: datetime, *, acting: bool = False
) -> IncidentView:
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
        strip=strip_of(incident, events, now, acting=acting),
        plan=plan_of([e for e in events if e.payload.get("kind") != "change"] or events),
        changes=tuple(
            (_steps(e.payload), _link(e.payload.get("url")))
            for e in events
            if e.kind == "change.proposed"
        ),
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
    series: tuple[tuple[str, float, float], ...] = ()  # every day, oldest first, for the chart


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
        series=tuple(
            (day, tally.cost, 0.0 if dearest <= 0 else tally.cost / dearest * 100)
            for day, tally in sorted(by_day.items())
        ),
    )


def stages(rules: Rules) -> tuple[tuple[str, str], ...]:
    """What is switched on, for the bar: the console must say what Paeon is not doing."""
    return (
        ("Triage", "shadow" if rules.triage.enabled else "off"),
        ("Investigation", "read-only" if rules.investigation.enabled else "off"),
        ("Postmortems", "drafts" if rules.postmortems.enabled else "off"),
        ("Actions", "by the executor" if rules.actions.enabled else "none"),
    )


# --- channels ----------------------------------------------------------------------------------

_STAGE_SENDS = (("digest", "digest"), ("investigation", "analysis"), ("actions", "approvals"))


@dataclass(frozen=True)
class ChannelLine:
    name: str
    kind: str
    carries: tuple[str, ...]  # what it is sent: digest, analysis, approvals
    sent: int
    failed: int
    last_at: datetime | None
    last_ok: bool
    last_error: str | None
    origin: str = "file"  # file: the rules file defines it; console: an admin added it here
    broken: str | None = None  # why a console channel is set aside, when it is
    can_test: bool = False

    @property
    def state(self) -> str:
        if self.last_at is None:
            return "unused"
        return "sent" if self.last_ok else "failed"


def channels_view(
    store: Store,
    rules: Rules,
    now: datetime,
    *,
    days: int = 30,
    console: Channels | None = None,
) -> tuple[ChannelLine, ...]:
    """Every channel a stage sends to, and how its deliveries went. Never where to: a webhook's
    address is its secret, and the rules file is where the rest is read. With `console`, the
    channels added there too, even one that carries nothing yet or is set aside."""
    carries: dict[str, list[str]] = {}
    kinds: dict[str, str] = {}
    added = {} if console is None else {one.name: one for one in console.shown()}
    for one in added.values():
        carries[one.name] = []
        kinds[one.name] = one.service.type
    for stage, what in _STAGE_SENDS:
        for name, config in rules.channels(stage):
            carries.setdefault(name, []).append(what)
            kinds[name] = config.type
    counts = {name: [0, 0] for name in carries}
    last: dict[str, Event] = {}
    for event in store.events(
        kinds=("notification.sent", "notification.failed"),
        recorded_since=now - timedelta(days=days),
    ):
        # Entries written before channels had names came from a stage's own ntfy block.
        name = str(event.payload.get("name") or Rules.LEGACY)
        if name not in counts:
            continue
        counts[name][event.kind == "notification.failed"] += 1
        last[name] = event
    return tuple(
        ChannelLine(
            name=name,
            kind=kinds[name],
            carries=tuple(carries[name]),
            sent=counts[name][0],
            failed=counts[name][1],
            last_at=last[name].recorded_at if name in last else None,
            last_ok=name in last and last[name].kind == "notification.sent",
            last_error=_error(last.get(name)),
            origin="console" if name in added else "file",
            broken=added[name].broken if name in added else None,
            can_test=console is not None and console.can_test(name),
        )
        for name in sorted(carries, key=lambda name: name in added)  # the file's first
    )


def _error(event: Event | None) -> str | None:
    error = None if event is None else event.payload.get("error")
    return None if error is None else str(error)


# --- sources ------------------------------------------------------------------------------------

# A source as the app built it: name, service, how its alerts arrive, the tools it offers.
SourceShown = tuple[str, str, str, tuple[str, ...]]


@dataclass(frozen=True)
class SourceLine:
    name: str
    kind: str
    alerts: str  # webhook, polled or none
    tools: tuple[str, ...]
    fired: int
    last_at: datetime | None
    stale: bool
    origin: str = "file"  # file: the rules file (or Paeon itself); console: added here
    broken: str | None = None
    probe: tuple[bool, str] | None = None  # what its last test answered
    can_test: bool = False

    @property
    def address(self) -> str | None:
        """Where the service posts, when it pushes."""
        return f"/ingest/{self.name}" if self.alerts == "webhook" else None


def sources_view(
    store: Store,
    shown: Sequence[SourceShown],
    now: datetime,
    *,
    days: int = 30,
    console: Sources | None = None,
) -> tuple[SourceLine, ...]:
    """Every source, what it gives, and what came from it. Never its address or its secret.
    With `console`, the sources added there too, even one that is set aside."""
    since = now - timedelta(days=days)
    lines = []
    added = {} if console is None else {one.name: one for one in console.shown()}
    listed = {name for name, *_ in shown}
    aside = [
        (one.name, one.service.type, "none", ()) for one in added.values() if one.name not in listed
    ]
    for name, kind, alerts, tools in (*shown, *aside):
        fired = 0
        last: datetime | None = None
        for event in store.events(
            kinds=("alert.firing", "alert.resolved"), source=name, recorded_since=since
        ):
            fired += event.kind == "alert.firing"
            last = event.recorded_at
        state = store.alert_state("paeon", f"source_stale:{name}")
        lines.append(
            SourceLine(
                name=name,
                kind=kind,
                alerts=alerts,
                tools=tools,
                fired=fired,
                last_at=last,
                stale=state is not None and state.status == Status.FIRING.value,
                origin="file" if console is None else console.origin(name),
                broken=added[name].broken if name in added else None,
                probe=None if console is None else console.probed(name),
                can_test=console is not None and console.can_test(name),
            )
        )
    return tuple(sorted(lines, key=lambda line: line.origin == "console"))  # the file's first


# --- actions and autonomy -------------------------------------------------------------------


@dataclass(frozen=True)
class PlanLine:
    incident_id: int
    title: str
    at: datetime
    plan: PlanView


@dataclass(frozen=True)
class ActionsView:
    enabled: bool
    killed: bool
    waiting: tuple[dict[str, Any], ...]
    plans: tuple[PlanLine, ...]
    days: int


def actions_view(store: Store, rules: Rules, now: datetime, *, days: int = 30) -> ActionsView:
    switch = store.events(kinds=("killswitch.engaged", "killswitch.released"))
    seen: dict[int, datetime] = {}
    kinds = ("plan.proposed", "plan.refused", "plan.not_proposed")
    for event in store.events(kinds=kinds, recorded_since=now - timedelta(days=days)):
        if event.incident_id is not None:
            seen[event.incident_id] = event.recorded_at
    lines = []
    for incident_id, at in sorted(seen.items(), key=lambda item: item[1], reverse=True)[:100]:
        incident = store.incident(incident_id)
        plan = plan_of(list(store.events(incident_id=incident_id)))
        if incident is not None and plan is not None:
            lines.append(PlanLine(incident_id, incident.title, at, plan))
    return ActionsView(
        enabled=rules.actions.enabled,
        killed=autonomy.killed(switch),
        waiting=tuple(remediation.pending_approvals(store, now)),
        plans=tuple(lines),
        days=days,
    )


@dataclass(frozen=True)
class AutonomyView:
    records: tuple[autonomy.Record, ...]
    earned: tuple[autonomy.Record, ...]
    promote_after: int
    window_days: int


def autonomy_view(store: Store, rules: Rules, now: datetime) -> AutonomyView:
    records = tuple(autonomy.matrix(store.events(kinds=autonomy.KINDS), rules.actions, now))
    return AutonomyView(
        records=records,
        earned=tuple(record for record in records if record.proposal),
        promote_after=rules.actions.autonomy.promote_after,
        window_days=rules.actions.autonomy.window_days,
    )


def postmortems_view(rules: Rules) -> tuple[postmortems.Draft, ...]:
    return tuple(postmortems.drafts(rules.postmortems.dir)) if rules.postmortems.enabled else ()
