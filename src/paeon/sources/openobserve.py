"""OpenObserve pull adapter: reads alert evaluations from the `triggers` stream.

OpenObserve appends one row to `triggers` per alert evaluation (every few minutes per alert), not
one per state change. Observed on a real instance:

- `status = 'firing'`: the condition held and a notification was sent. Repeats while it holds.
- `status = 'notify_failed'`: the condition held and the notification could not be delivered.
  Treated as firing: it is the case where nobody else was told.
- `status = 'normal'`: the condition did not hold.
- `status = 'skipped'`: says nothing about the condition (meaning unconfirmed), so it is ignored.
- rows can be duplicated, and `key` is `<alert_name>/<alert_id>`.

The adapter maps rows to observations; `paeon.recorder` reduces them to state changes. Each poll
re-reads an overlap before the cursor because rows can be ingested late; the recorder drops what
it has already seen.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx2 as httpx

from paeon.recorder import Recorder
from paeon.schema import Alert, Status, from_micros, to_micros
from paeon.store import Store

log = logging.getLogger(__name__)

_FIRING = frozenset({"firing", "notify_failed"})
_SQL = (
    'SELECT _timestamp, key, status, start_time, end_time FROM "triggers"'
    " WHERE module = 'alert' ORDER BY _timestamp ASC"
)
_PAGE = 5000


class SearchClient(Protocol):
    def search(self, sql: str, start: int, end: int, size: int) -> list[dict[str, Any]]: ...


class HttpSearchClient:
    """OpenObserve's search API (`POST /api/{org}/_search`), with a read-only account."""

    def __init__(
        self, base_url: str, org: str, user: str, password: str, *, timeout: float = 20.0
    ) -> None:
        self._org = org
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), auth=(user, password), timeout=timeout
        )

    def search(self, sql: str, start: int, end: int, size: int) -> list[dict[str, Any]]:
        response = self._http.post(
            f"/api/{self._org}/_search",
            params={"type": "logs"},
            json={"query": {"sql": sql, "start_time": start, "end_time": end, "size": size}},
        )
        response.raise_for_status()
        hits = response.json().get("hits", [])
        return [hit for hit in hits if isinstance(hit, dict)]

    def query_range(self, promql: str, start: int, end: int, step: int) -> Any:
        """A PromQL range query (`GET /api/{org}/prometheus/api/v1/query_range`), in seconds."""
        response = self._http.get(
            f"/api/{self._org}/prometheus/api/v1/query_range",
            params={"query": promql, "start": start, "end": end, "step": step},
        )
        response.raise_for_status()
        return response.json().get("data")

    def close(self) -> None:
        self._http.close()


def row_to_alert(row: dict[str, Any], *, source: str, base_url: str | None = None) -> Alert | None:
    """One `triggers` row as an observation, or None when the row carries no verdict."""
    status = row.get("status")
    if status in _FIRING:
        alert_status = Status.FIRING
    elif status == "normal":
        alert_status = Status.RESOLVED
    else:
        return None
    key = row.get("key")
    timestamp = row.get("_timestamp")
    if not isinstance(key, str) or not key or not isinstance(timestamp, int):
        return None
    name, _, alert_id = key.rpartition("/")
    if not name:  # a key without an id part
        name, alert_id = key, key
    observed_at = from_micros(timestamp)
    start = row.get("start_time")
    labels = {"alert_name": name, "alert_id": alert_id}
    if status == "notify_failed":
        labels["notify_failed"] = "true"
    return Alert(
        source=source,
        fingerprint=alert_id,
        status=alert_status,
        title=name,
        labels=labels,
        starts_at=from_micros(start) if isinstance(start, int) and start > 0 else observed_at,
        observed_at=observed_at,
        source_url=f"{base_url.rstrip('/')}/web/alerts" if base_url else None,
        raw=row,
    )


@dataclass
class PollResult:
    rows: int
    changes: int


class OpenObserveSource:
    def __init__(
        self,
        client: SearchClient,
        store: Store,
        recorder: Recorder,
        *,
        name: str = "openobserve",
        base_url: str | None = None,
        overlap: timedelta = timedelta(minutes=5),
        initial_lookback: timedelta = timedelta(hours=1),
    ) -> None:
        self.name = name
        self._client = client
        self._store = store
        self._recorder = recorder
        self._base_url = base_url
        self._overlap = overlap
        self._initial_lookback = initial_lookback

    def last_data_at(self) -> datetime | None:
        """Timestamp of the newest row ever read: what staleness is measured from."""
        cursor = self._store.cursor(self.name)
        return None if cursor is None else from_micros(int(cursor))

    def poll(self, now: datetime) -> PollResult:
        cursor = self._store.cursor(self.name)
        if cursor is None:
            start = to_micros(now - self._initial_lookback)
        else:
            start = int(cursor) - int(self._overlap.total_seconds() * 1_000_000)
        end = to_micros(now)
        rows = 0
        changes = 0
        newest = int(cursor) if cursor is not None else 0
        while True:
            page = self._client.search(_SQL, start, end, _PAGE)
            changes += self._record(page)
            rows += len(page)
            stamps = [r["_timestamp"] for r in page if isinstance(r.get("_timestamp"), int)]
            if stamps:
                newest = max(newest, *stamps)
            if len(page) < _PAGE or not stamps or max(stamps) <= start:
                break
            start = max(stamps)  # catch-up after downtime: continue from the last row read
        if newest and (cursor is None or newest > int(cursor)):
            self._store.set_cursor(self.name, str(newest))
        return PollResult(rows=rows, changes=changes)

    def _record(self, page: Sequence[dict[str, Any]]) -> int:
        changes = 0
        for row in page:
            alert = row_to_alert(row, source=self.name, base_url=self._base_url)
            if alert is not None and self._recorder.observe(alert) is not None:
                changes += 1
        return changes
