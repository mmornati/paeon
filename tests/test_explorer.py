"""The incident explorer: filters in the URL, paging by cursor, counts that add up."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from paeon.schema import IncidentStatus, Severity, StageState
from paeon.store import IncidentFilter, Store
from paeon.ui.explorer import Query, explore
from ui_data import populate, populate_actions, populate_history

ZONE = ZoneInfo("Europe/Paris")


def read(url: str) -> Query:
    return Query.read(parse_qs(urlsplit(url).query))


def test_pages_cover_every_incident_once(store: Store) -> None:
    now = datetime.now(UTC)
    populate(store, now)
    populate_history(store, 80, now)
    query = Query(range="all")
    seen: list[int] = []
    pages = 0
    while True:
        page = explore(store, query, now, ZONE, limit=25)
        seen += [row.incident.id for row in page.rows]
        pages += 1
        if page.older is None:
            break
        query = read(page.older)
    assert pages == 4
    assert len(seen) == len(set(seen)) == 85
    # Open first, then the most recent activity first.
    rows = [store.incident(i) for i in seen]
    ranks = [0 if r is not None and r.status is IncidentStatus.OPEN else 1 for r in rows]
    assert ranks == sorted(ranks)


def test_a_new_incident_does_not_shift_the_next_page(store: Store) -> None:
    now = datetime.now(UTC)
    populate_history(store, 30, now)
    first = explore(store, Query(range="all"), now, ZONE, limit=10)
    assert first.older is not None
    store.open_incident(
        title="new",
        severity=Severity.CRITICAL,
        host=None,
        opened_at=now,
        maintenance=None,
        triage=StageState.PENDING,
    )
    second = explore(store, read(first.older), now, ZONE, limit=10)
    assert not {r.incident.id for r in first.rows} & {r.incident.id for r in second.rows}
    assert second.newer == "/ui?range=all"


def test_range_keeps_open_incidents_and_drops_old_resolved_ones(store: Store) -> None:
    now = datetime.now(UTC)
    ids = populate(store, now)
    populate_history(store, 40, now)
    day = explore(store, Query(range="24h"), now, ZONE)
    shown = {r.incident.id for r in day.rows}
    assert ids["media"] in shown  # open
    assert all(r.open or r.incident.last_activity_at >= now - timedelta(hours=24) for r in day.rows)
    month = explore(store, Query(range="30d"), now, ZONE)
    assert month.counts["all"] > day.counts["all"]


def test_facet_counts_ignore_their_own_choice(store: Store) -> None:
    now = datetime.now(UTC)
    populate_history(store, 60, now)
    query = Query(range="all", severity=("critical",))
    page = explore(store, query, now, ZONE)
    severity = next(f for f in page.facets if f.name == "severity")
    counts = {o.value: o.count for o in severity.options}
    assert sum(counts.values()) == 60
    assert next(o for o in severity.options if o.value == "critical").chosen
    assert {r.incident.severity for r in page.rows} == {Severity.CRITICAL}
    # Choosing again removes it, and a new filter starts from the first page.
    critical = next(o for o in severity.options if o.value == "critical")
    assert read(critical.url) == Query(range="all")


def test_text_finds_titles_hosts_and_numbers(store: Store) -> None:
    now = datetime.now(UTC)
    ids = populate(store, now)
    populate_history(store, 20, now)
    by_title = explore(store, Query(range="all", q="queue_back"), now, ZONE)
    assert by_title.rows
    assert all("queue_backlog" in r.incident.title for r in by_title.rows)
    by_host = explore(store, Query(range="all", q="vps-1"), now, ZONE)
    assert by_host.rows
    assert all(r.incident.host == "vps-1" for r in by_host.rows)
    by_number = explore(store, Query(range="all", q=f"#{ids['media']}"), now, ZONE)
    assert [r.incident.id for r in by_number.rows] == [ids["media"]]
    # LIKE wildcards are text, not patterns.
    assert explore(store, Query(range="all", q="%"), now, ZONE).rows == ()


def test_needs_you_lists_plans_waiting_for_an_answer(store: Store) -> None:
    now = datetime.now(UTC)
    ids = populate(store, now)
    populate_actions(store, ids)
    page = explore(store, Query(status="needs"), now, ZONE, acting=True)
    assert [r.incident.id for r in page.rows] == [ids["media"]]
    assert page.counts["needs"] == 1
    assert [t[0] for t in page.tabs] == ["all", "needs", "open", "resolved"]
    # Without the executor, nothing can wait for an answer: no tab.
    assert "needs" not in [t[0] for t in explore(store, Query(), now, ZONE).tabs]


def test_stage_track_says_what_happened(store: Store) -> None:
    now = datetime.now(UTC)
    ids = populate(store, now)
    page = explore(store, Query(range="all"), now, ZONE)
    rows = {r.incident.id: r for r in page.rows}
    media = {s.stage: s for s in rows[ids["media"]].track}
    assert [s.stage for s in rows[ids["media"]].track] == [
        "triage",
        "investigation",
        "plan",
        "action",
        "postmortem",
    ]
    assert media["triage"].state == "model"
    assert media["investigation"].state == "model"
    assert media["plan"].state == "off"  # no executor
    stale = {s.stage: s for s in rows[ids["stale"]].track}
    assert stale["triage"].state == "failed"
    assert stale["triage"].label.startswith("Triage: real, failed open")
    assert rows[ids["load"]].track[0].state == "waiting"


def test_chart_counts_openings_per_day_in_the_zone(store: Store) -> None:
    now = datetime(2026, 11, 21, 12, tzinfo=UTC)
    populate_history(store, 50, now)
    page = explore(store, Query(range="30d"), now, ZONE)
    assert page.chart is not None
    assert page.chart.unit == "day"
    assert len(page.chart.buckets) == 30
    since = now - timedelta(days=30)
    opened = [
        i
        for i in store.incidents()
        if i.opened_at.astimezone(ZONE).date() >= page.chart.buckets[0].start.date()
        and i.opened_at >= since - timedelta(days=1)
    ]
    assert page.chart.total == len(opened)
    assert page.chart.top >= max(b.total for b in page.chart.buckets)
    hours = explore(store, Query(range="24h"), now, ZONE).chart
    assert hours is not None
    assert hours.unit == "hour"
    assert len(hours.buckets) == 24


def test_unknown_values_in_the_url_are_dropped() -> None:
    query = read("/ui?status=nope&range=1y&severity=critical&severity=bogus&triage=done&after=x")
    assert query == Query(severity=("critical",), triage=("done",), after="x")
    assert query.cursor() is None
    assert read(query.url()) == query


def test_store_filter_by_stage_and_missing_host(store: Store) -> None:
    now = datetime.now(UTC)
    ids = populate(store, now)
    nohost = store.search_incidents(IncidentFilter(hosts=("",)))
    assert [i.id for i in nohost] == [ids["stale"]]
    pending = store.search_incidents(IncidentFilter(triage=(StageState.PENDING,)))
    assert [i.id for i in pending] == [ids["load"]]
