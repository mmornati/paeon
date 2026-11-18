from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from paeon.recorder import Recorder
from paeon.schema import Status, from_micros
from paeon.sources.openobserve import HttpSearchClient, OpenObserveSource, row_to_alert
from paeon.store import Store

KEY = "slo_fast_burn_photo_indexer_health/2aBcD3eFgH4iJkL5mNoP6qRsT7u"
BASE = 1_794_688_800_000_000  # micros
MINUTE = 60_000_000


def row(minute: int, status: str, key: str = KEY) -> dict[str, Any]:
    """A `triggers` row with the fields seen on a real instance."""
    start = BASE + minute * MINUTE
    return {
        "_timestamp": start + 4_684_527,
        "key": key,
        "status": status,
        "start_time": start,
        "end_time": start,
        "module": "alert",
        "is_silenced": False,
        "org": "default",
    }


class FakeClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[int, int]] = []

    def search(self, sql: str, start: int, end: int, size: int) -> list[dict[str, Any]]:
        assert "triggers" in sql
        self.calls.append((start, end))
        hits = [r for r in self.rows if start <= r["_timestamp"] <= end]
        return sorted(hits, key=lambda r: r["_timestamp"])[:size]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "paeon.sqlite")
    yield s
    s.close()


def test_row_mapping() -> None:
    alert = row_to_alert(row(0, "firing"), source="openobserve", base_url="http://oo:5080/")
    assert alert is not None
    assert alert.status is Status.FIRING
    assert alert.fingerprint == "2aBcD3eFgH4iJkL5mNoP6qRsT7u"
    assert alert.title == "slo_fast_burn_photo_indexer_health"
    assert alert.starts_at == from_micros(BASE)
    assert alert.source_url == "http://oo:5080/web/alerts"

    failed = row_to_alert(row(0, "notify_failed"), source="openobserve")
    assert failed is not None
    assert failed.status is Status.FIRING
    assert failed.labels["notify_failed"] == "true"

    normal = row_to_alert(row(0, "normal"), source="openobserve")
    assert normal is not None
    assert normal.status is Status.RESOLVED


@pytest.mark.parametrize(
    "bad",
    [
        row(0, "skipped"),
        row(0, "succeeded"),
        {**row(0, "firing"), "key": ""},
        {**row(0, "firing"), "key": None},
        {**row(0, "firing"), "_timestamp": "yesterday"},
    ],
)
def test_rows_without_a_verdict_are_dropped(bad: dict[str, Any]) -> None:
    assert row_to_alert(bad, source="openobserve") is None


def test_key_without_id() -> None:
    alert = row_to_alert(row(0, "firing", key="plain"), source="openobserve")
    assert alert is not None
    assert (alert.title, alert.fingerprint) == ("plain", "plain")


def test_evaluation_rows_become_two_state_changes(store: Store) -> None:
    rows = [
        row(0, "normal"),
        row(5, "firing"),
        row(5, "firing"),  # duplicated row, as seen in the stream
        row(10, "skipped"),
        row(15, "firing"),
        row(20, "skipped"),
        row(25, "normal"),
        row(30, "normal"),
    ]
    client = FakeClient(rows)
    source = OpenObserveSource(client, store, Recorder(store))
    now = from_micros(BASE + 31 * MINUTE)

    result = source.poll(now)

    assert (result.rows, result.changes) == (8, 2)
    events = list(store.events())
    assert [e.kind for e in events] == ["alert.firing", "alert.resolved"]
    observed = datetime.fromisoformat(events[0].payload["observed_at"])
    assert observed == from_micros(rows[1]["_timestamp"])
    assert source.last_data_at() == from_micros(rows[-1]["_timestamp"])


def test_next_poll_overlaps_without_duplicating(store: Store) -> None:
    client = FakeClient([row(0, "firing")])
    source = OpenObserveSource(client, store, Recorder(store), overlap=timedelta(minutes=5))
    source.poll(from_micros(BASE + 1 * MINUTE))
    cursor = store.cursor("openobserve")
    assert cursor is not None

    # A row that was ingested late: older than the cursor, for another alert.
    client.rows.append(row(-2, "firing", key="late/xyz"))
    result = source.poll(from_micros(BASE + 2 * MINUTE))

    assert client.calls[1][0] == int(cursor) - 5 * MINUTE
    assert result.changes == 1  # the late alert only; the first row is not recorded twice
    assert [e.fingerprint for e in store.events()] == ["2aBcD3eFgH4iJkL5mNoP6qRsT7u", "xyz"]
    assert store.cursor("openobserve") == cursor  # never moves backwards


def test_empty_poll_keeps_cursor_and_first_poll_looks_back(store: Store) -> None:
    client = FakeClient([])
    source = OpenObserveSource(client, store, Recorder(store), initial_lookback=timedelta(hours=1))
    now = from_micros(BASE)
    assert source.poll(now).rows == 0
    assert client.calls == [(BASE - 60 * MINUTE, BASE)]
    assert source.last_data_at() is None


def test_catch_up_pages_through_a_backlog(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("paeon.sources.openobserve._PAGE", 3)
    rows = [row(i, "firing" if i == 4 else "normal") for i in range(7)]
    client = FakeClient(rows)
    source = OpenObserveSource(client, store, Recorder(store))

    result = source.poll(from_micros(BASE + 10 * MINUTE))

    assert len(client.calls) > 1
    assert result.changes == 2  # fired at minute 4, resolved at minute 5
    assert source.last_data_at() == from_micros(rows[-1]["_timestamp"])


def test_http_client_request_and_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read()
        return httpx.Response(200, json={"hits": [row(0, "firing"), "junk"]})

    client = HttpSearchClient("http://oo:5080/", "default", "reader", "pw")
    client._http = httpx.Client(
        base_url="http://oo:5080", auth=("reader", "pw"), transport=httpx.MockTransport(handler)
    )
    hits = client.search("SELECT 1", 1, 2, 10)
    client.close()

    assert seen["url"] == "http://oo:5080/api/default/_search?type=logs"
    assert seen["auth"].startswith("Basic ")
    assert b'"start_time":1' in seen["body"].replace(b" ", b"")
    assert len(hits) == 1
