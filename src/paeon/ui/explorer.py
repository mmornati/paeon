"""The incident explorer: every incident, filtered and paged by the server.

A query is read from the page's URL and written back into every link the page draws, so a view
can be bookmarked and sent. Rows are paged by a cursor (the key of the last row shown), never by
page number: an incident that opens while the operator reads does not shift the next page.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from paeon.schema import Incident, IncidentStatus, Severity, StageState
from paeon.store import Event, IncidentCursor, IncidentFilter, Store
from paeon.ui.views import (
    Cell,
    PlanView,
    _investigation_cell,
    _triage_cell,
    money,
    plan_of,
    span,
)

PAGE = 50
STATUSES = ("all", "needs", "open", "resolved")
RANGES: dict[str, timedelta | None] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}
DEFAULT_RANGE = "7d"
SORTS = ("activity",)  # one order for now: open first, then the most recent activity
STAGES = ("triage", "investigation", "plan", "action", "postmortem")
SEVERITY_ORDER = tuple(Severity)
STATE_LABELS = {
    StageState.DONE.value: "done",
    StageState.FAILED.value: "failed",
    StageState.SKIPPED.value: "skipped",
    StageState.PENDING.value: "waiting",
    StageState.OFF.value: "off",
}


@dataclass(frozen=True)
class Query:
    """What the operator asked the explorer for, as it stands in the URL."""

    status: str = "all"
    range: str = DEFAULT_RANGE
    severity: tuple[str, ...] = ()
    host: tuple[str, ...] = ()
    triage: tuple[str, ...] = ()
    investigation: tuple[str, ...] = ()
    q: str = ""
    after: str = ""  # the cursor of the page before this one, as text

    @classmethod
    def read(cls, params: Mapping[str, Sequence[str]]) -> Query:
        """Read a query from URL parameters. A value the explorer does not know is dropped,
        never an error: an old bookmark still opens."""

        def one(name: str, allowed: Sequence[str], default: str) -> str:
            value = (params.get(name) or [default])[-1]
            return value if value in allowed else default

        def many(name: str, allowed: Sequence[str] | None = None) -> tuple[str, ...]:
            values: list[str] = []
            for value in params.get(name) or []:
                if (allowed is None or value in allowed) and value not in values:
                    values.append(value[:200])
            return tuple(values)

        states = tuple(s.value for s in StageState)
        return cls(
            status=one("status", STATUSES, "all"),
            range=one("range", tuple(RANGES), DEFAULT_RANGE),
            severity=many("severity", tuple(s.value for s in Severity)),
            host=many("host"),
            triage=many("triage", states),
            investigation=many("investigation", states),
            q=(params.get("q") or [""])[-1].strip()[:200],
            after=(params.get("after") or [""])[-1][:100],
        )

    def params(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        if self.status != "all":
            pairs.append(("status", self.status))
        if self.range != DEFAULT_RANGE:
            pairs.append(("range", self.range))
        for name in ("severity", "host", "triage", "investigation"):
            pairs += [(name, value) for value in getattr(self, name)]
        if self.q:
            pairs.append(("q", self.q))
        if self.after:
            pairs.append(("after", self.after))
        return pairs

    def url(self, path: str = "/ui") -> str:
        pairs = self.params()
        return path + ("?" + urlencode(pairs) if pairs else "")

    def with_(self, **change: object) -> Query:
        """The same query with some fields changed; a new filter starts from the first page."""
        if "after" not in change:
            change["after"] = ""
        return replace(self, **change)  # type: ignore[arg-type]

    def toggle(self, name: str, value: str) -> Query:
        chosen: tuple[str, ...] = getattr(self, name)
        values = tuple(v for v in chosen if v != value) if value in chosen else (*chosen, value)
        return self.with_(**{name: values})

    @property
    def filtered(self) -> bool:
        """Whether anything narrows the list beyond the status and the range."""
        return bool(self.severity or self.host or self.triage or self.investigation or self.q)

    def store_filter(self, now: datetime, *, status: bool = True) -> IncidentFilter:
        since = RANGES[self.range]
        statuses: tuple[IncidentStatus, ...] = ()
        if status and self.status in ("open", "needs"):
            statuses = (IncidentStatus.OPEN,)
        elif status and self.status == "resolved":
            statuses = (IncidentStatus.RESOLVED,)
        return IncidentFilter(
            statuses=statuses,
            severities=tuple(Severity(v) for v in self.severity),
            hosts=self.host,
            triage=tuple(StageState(v) for v in self.triage),
            investigation=tuple(StageState(v) for v in self.investigation),
            text=self.q,
            active_since=None if since is None else now - since,
        )

    def cursor(self) -> IncidentCursor | None:
        try:
            rank, stamp, number = self.after.split("~")
            return IncidentCursor(int(rank), datetime.fromisoformat(stamp), int(number))
        except ValueError:
            return None


def cursor_text(cursor: IncidentCursor) -> str:
    return f"{cursor.rank}~{cursor.last_activity_at.isoformat()}~{cursor.id}"


@dataclass(frozen=True)
class Step:
    """One square of an incident's stage track."""

    stage: str
    state: str  # model, code, failed, waiting, skipped, off
    text: str  # what the square stands for, said in words

    @property
    def label(self) -> str:
        return f"{self.stage.capitalize()}: {self.text}"


@dataclass(frozen=True)
class Row:
    incident: Incident
    track: tuple[Step, ...]
    verdict: Cell
    standing: str  # "open 3 h 10", "resolved in 60 s"
    cost: str
    flags: tuple[str, ...]
    waiting: bool  # a plan waits for the operator's answer
    rev: int

    @property
    def open(self) -> bool:
        return self.incident.status is IncidentStatus.OPEN


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    count: int
    chosen: bool
    url: str


@dataclass(frozen=True)
class Facet:
    name: str
    title: str
    options: tuple[Option, ...]


@dataclass(frozen=True)
class Bucket:
    start: datetime
    label: str
    counts: dict[str, int]  # per severity

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass(frozen=True)
class Chart:
    buckets: tuple[Bucket, ...]
    top: int  # the scale's maximum: a round number at or above the tallest bar
    unit: str  # "hour", "day" or "week"
    severities: tuple[str, ...]  # those present, in order

    @property
    def total(self) -> int:
        return sum(b.total for b in self.buckets)


@dataclass(frozen=True)
class Explorer:
    query: Query
    rows: tuple[Row, ...]
    counts: dict[str, int]  # per status tab
    facets: tuple[Facet, ...]
    chart: Chart | None
    newer: str | None  # url of the first page, when this is not it
    older: str | None  # url of the next page
    shown_from: int = 1
    tabs: tuple[tuple[str, str, int, str, bool], ...] = field(default=())


def track_of(
    incident: Incident, events: Sequence[Event], plan: PlanView | None, *, acting: bool
) -> tuple[Step, ...]:
    triage = _triage_cell(incident, events)
    investigation = _investigation_cell(incident, events)
    steps = [
        Step(
            "triage",
            _stage_state(incident.triage, failed=triage.value == "failed open"),
            triage.full,
        ),
        Step("investigation", _stage_state(incident.investigation), investigation.full),
    ]
    if plan is None:
        none = "none" if acting else "off"
        steps += [Step("plan", _quiet(acting), none), Step("action", _quiet(acting), "none")]
    else:
        by = "code" if plan.origin.startswith("runbook") else "model"
        planned = "failed" if plan.state in ("refused", "not sent") else by
        steps.append(Step("plan", planned, ", ".join(plan.steps) or "no step"))
        steps.append(Step("action", _ACTION.get(plan.state, "skipped"), plan.story))
    postmortem = incident.postmortem
    words = {StageState.DONE: "draft written", StageState.PENDING: "waiting"}
    steps.append(
        Step("postmortem", _stage_state(postmortem), words.get(postmortem, postmortem.value))
    )
    return tuple(steps)


_ACTION = {
    **dict.fromkeys(("waiting", "running", "queued", "proposed"), "waiting"),
    **dict.fromkeys(("verified", "undone"), "code"),
    **dict.fromkeys(("failed", "refused"), "failed"),
}


def _quiet(acting: bool) -> str:
    return "skipped" if acting else "off"


def _stage_state(state: StageState, *, failed: bool = False) -> str:
    if failed:
        return "failed"
    return {
        StageState.DONE: "model",
        StageState.FAILED: "failed",
        StageState.SKIPPED: "skipped",
        StageState.PENDING: "waiting",
        StageState.OFF: "off",
    }[state]


def row_of(incident: Incident, events: Sequence[Event], now: datetime, *, acting: bool) -> Row:
    plan = plan_of(events)
    cost = sum(float(e.payload.get("cost") or 0) for e in events if e.kind == "model.call")
    if incident.resolved_at is not None:
        standing = span(incident.resolved_at - incident.opened_at)
    else:
        standing = f"open {span(now - incident.opened_at)}"
    flags = []
    if incident.flapping:
        flags.append("flapping")
    if incident.maintenance:
        flags.append(f"maintenance: {incident.maintenance}")
    return Row(
        incident=incident,
        track=track_of(incident, events, plan, acting=acting),
        verdict=_triage_cell(incident, events),
        standing=standing,
        cost=money(cost),
        flags=tuple(flags),
        waiting=plan is not None and plan.waiting,
        rev=events[-1].id if events else 0,
    )


def _rows(store: Store, incidents: Sequence[Incident], now: datetime, *, acting: bool) -> list[Row]:
    """Rows for a page of incidents, reading their events in one query."""
    events: dict[int, list[Event]] = defaultdict(list)
    for event in store.events(incident_ids=[i.id for i in incidents]):
        if event.incident_id is not None:
            events[event.incident_id].append(event)
    return [row_of(i, events[i.id], now, acting=acting) for i in incidents]


def explore(
    store: Store,
    query: Query,
    now: datetime,
    zone: ZoneInfo,
    *,
    acting: bool = False,
    limit: int = PAGE,
) -> Explorer:
    # What waits for the operator is read from the open incidents' plans: there are few.
    everywhere = query.store_filter(now, status=False)
    open_now = store.search_incidents(
        replace(everywhere, statuses=(IncidentStatus.OPEN,)), limit=10_000
    )
    waiting = [r for r in _rows(store, open_now, now, acting=acting) if r.waiting]

    if query.status == "needs":
        rows = waiting
        older = None
    else:
        found = store.search_incidents(
            query.store_filter(now), after=query.cursor(), limit=limit + 1
        )
        page = found[:limit]
        rows = _rows(store, page, now, acting=acting)
        older = (
            query.with_(after=cursor_text(IncidentCursor.of(page[-1]))).url()
            if len(found) > limit
            else None
        )

    by_status = store.count_incidents_by(everywhere, "status")
    counts = {
        "all": sum(by_status.values()),
        "needs": len(waiting),
        "open": by_status.get(IncidentStatus.OPEN.value, 0),
        "resolved": by_status.get(IncidentStatus.RESOLVED.value, 0),
    }
    names = {"all": "All", "needs": "Needs you", "open": "Open", "resolved": "Resolved"}
    tabs = tuple(
        (key, names[key], counts[key], query.with_(status=key).url(), query.status == key)
        for key in STATUSES
        if acting or key != "needs"
    )
    return Explorer(
        query=query,
        rows=tuple(rows),
        counts=counts,
        facets=_facets(store, query, now),
        chart=_chart(store, query, now, zone),
        newer=query.with_().url() if query.after else None,
        older=older,
        tabs=tabs,
    )


def _facets(store: Store, query: Query, now: datetime) -> tuple[Facet, ...]:
    where = query.store_filter(now)
    facets = []
    orders: dict[str, Sequence[str]] = {
        "severity": [s.value for s in SEVERITY_ORDER],
        "triage": [s.value for s in StageState],
        "investigation": [s.value for s in StageState],
    }
    titles = {
        "severity": "Severity",
        "triage": "Triage",
        "investigation": "Investigation",
        "host": "Host",
    }
    for name, title in titles.items():
        counts = store.count_incidents_by(where, name)
        chosen: tuple[str, ...] = getattr(query, name)
        values = [v for v in orders.get(name, sorted(counts, key=lambda v: (-counts[v], v)))]
        values += [v for v in chosen if v not in values]
        options = tuple(
            Option(
                value=value,
                label=_label(name, value),
                count=counts.get(value, 0),
                chosen=value in chosen,
                url=query.toggle(name, value).url(),
            )
            for value in values
            if counts.get(value, 0) or value in chosen
        )
        if options:
            facets.append(Facet(name, title, options[:12] if name == "host" else options))
    return tuple(facets)


def _label(name: str, value: str) -> str:
    if name == "host":
        return value or "no host"
    if name in ("triage", "investigation"):
        return STATE_LABELS.get(value, value)
    return value


def _chart(store: Store, query: Query, now: datetime, zone: ZoneInfo) -> Chart | None:
    """Incidents opened per hour, day or week across the range, in the operator's time zone."""
    since = RANGES[query.range]
    local_now = now.astimezone(zone)
    if since is not None and since <= timedelta(days=1):
        unit, step = "hour", timedelta(hours=1)
        first = local_now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
        count = 24
    else:
        today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        if since is None:
            openings = store.incident_openings(
                query.store_filter(now), datetime.min.replace(tzinfo=now.tzinfo)
            )
            if not openings:
                return None
            oldest = min(o for o, _ in openings).astimezone(zone)
            days = (today - oldest.replace(hour=0, minute=0, second=0, microsecond=0)).days + 1
        else:
            days = since.days
        if days > 45:
            unit, step = "week", timedelta(days=7)
            count = min((days + 6) // 7, 52)
            first = today - timedelta(days=today.weekday()) - step * (count - 1)
        else:
            unit, step = "day", timedelta(days=1)
            count = max(days, 1)
            first = today - step * (count - 1)
    tallies: list[dict[str, int]] = [defaultdict(int) for _ in range(count)]
    for opened, severity in store.incident_openings(query.store_filter(now), first):
        slot = int((opened.astimezone(zone) - first) / step)
        if 0 <= slot < count:
            tallies[slot][severity] += 1
    buckets = []
    for slot in range(count):
        start = first + step * slot
        label = start.strftime("%H:00") if unit == "hour" else start.strftime("%d %b").lstrip("0")
        buckets.append(Bucket(start, label, dict(tallies[slot])))
    tallest = max((b.total for b in buckets), default=0)
    present = {s for b in buckets for s in b.counts}
    return Chart(
        buckets=tuple(buckets),
        top=_nice(tallest),
        unit=unit,
        severities=tuple(s.value for s in SEVERITY_ORDER if s.value in present),
    )


def _nice(value: int) -> int:
    """A round top for a scale: 1, 2, 5, 10, 20, 50, ..."""
    top = 1
    while top < value:
        for factor in (2, 5 / 2, 2):
            top = int(top * factor)
            if top >= value:
                break
    return max(top, 1)
