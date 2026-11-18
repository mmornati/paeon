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
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx2 as httpx

from paeon.recorder import Recorder
from paeon.schema import Alert, Severity, Status, from_micros, to_micros
from paeon.store import Store

log = logging.getLogger(__name__)

_FIRING = frozenset({"firing", "notify_failed"})
_SQL = (
    'SELECT _timestamp, key, status, start_time, end_time FROM "triggers"'
    " WHERE module = 'alert' ORDER BY _timestamp ASC"
)
_PAGE = 5000


_DEFINITIONS_TTL = 600.0


class SearchClient(Protocol):
    def search(self, sql: str, start: int, end: int, size: int) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class AlertDefinition:
    """What an OpenObserve alert watches, without where it notifies (that holds credentials)."""

    id: str
    name: str
    description: str = ""
    stream: str = ""
    stream_type: str = "logs"
    kind: str = "custom"  # sql, promql, slo, or custom (conditions built in the UI)
    sql: str = ""
    promql: str = ""
    conditions: Any = None
    period_minutes: int | None = None
    operator: str = ""
    threshold: float | None = None
    severity: Severity = Severity.UNKNOWN
    enabled: bool = True

    @property
    def query(self) -> str | None:
        return self.sql or self.promql or None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "stream": self.stream,
            "stream_type": self.stream_type,
            "type": self.kind,
            "sql": self.sql or None,
            "promql": self.promql or None,
            "conditions": self.conditions,
            "fires_when": f"{self.operator} {self.threshold:g}"
            if self.operator and self.threshold is not None
            else None,
            "period_minutes": self.period_minutes,
            "enabled": self.enabled,
        }


def _severity(raw: dict[str, Any]) -> Severity:
    """A severity the operator wrote in the alert's context attributes, when there is one."""
    context = raw.get("context_attributes")
    value = context.get("severity") if isinstance(context, dict) else None
    try:
        return Severity(str(value).lower())
    except ValueError:
        return Severity.UNKNOWN


def parse_definition(raw: Any) -> AlertDefinition | None:
    """One alert as the alerts API returns it (v2 by id, or an entry of the v1 list)."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    alert_id = raw.get("id") or raw.get("alert_id") or name
    if not isinstance(name, str) or not name or not isinstance(alert_id, str):
        return None
    # The alert itself calls it `query_condition`; an entry of the v2 list calls it `condition`.
    query = raw.get("query_condition") or raw.get("condition")
    query = query if isinstance(query, dict) else {}
    trigger = raw.get("trigger_condition")
    trigger = trigger if isinstance(trigger, dict) else {}
    sql = str(query.get("sql") or "")
    promql = str(query.get("promql") or "")
    kind = str(query.get("type") or ("sql" if sql else "promql" if promql else "custom")).lower()
    threshold = trigger.get("threshold")
    period = trigger.get("period")
    return AlertDefinition(
        id=alert_id,
        name=name,
        description=str(raw.get("description") or ""),
        stream=str(raw.get("stream_name") or ""),
        stream_type=str(raw.get("stream_type") or "logs"),
        kind=kind,
        sql=sql,
        promql=promql,
        # One of the three says what is watched when there is no query to show: conditions
        # built in the UI, a burn rate on an SLO, or the comparison applied to a PromQL value.
        conditions=query.get("conditions")
        or query.get("slo_condition")
        or query.get("promql_condition"),
        period_minutes=period if isinstance(period, int) and not isinstance(period, bool) else None,
        operator=str(trigger.get("operator") or ""),
        threshold=float(threshold)
        if isinstance(threshold, int | float) and not isinstance(threshold, bool)
        else None,
        severity=_severity(raw),
        enabled=raw.get("enabled") is not False,
    )


class HttpSearchClient:
    """OpenObserve's search API (`POST /api/{org}/_search`), with a read-only account."""

    def __init__(
        self,
        base_url: str,
        org: str,
        user: str,
        password: str,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._org = org
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            auth=(user, password),
            timeout=timeout,
            transport=transport,
        )
        self._definitions: dict[str, AlertDefinition] = {}
        self._listed_at = float("-inf")

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

    def _alert_list(self) -> list[dict[str, Any]]:
        """Every alert, by the v2 API and, where that does not exist, by the older one.

        Checked against OpenObserve 1.1: `GET /api/v2/{org}/alerts` answers `{"list": [...]}`,
        each entry with its `alert_id`, name, description, stream, `condition` (the query) and
        `trigger_condition`, so one request is enough. An entry's `level` is the alert's
        state at that moment (`ok`, `critical`), not a severity the owner chose: it is not used.
        """
        for path in (f"/api/v2/{self._org}/alerts", f"/api/{self._org}/alerts"):
            response = self._http.get(path, params={"page_size": 1000})
            if response.status_code == 404:
                continue
            response.raise_for_status()
            found = response.json().get("list", [])
            return [entry for entry in found if isinstance(entry, dict)]
        return []

    def alert_definition(self, key: str) -> AlertDefinition | None:
        """The definition of the alert with this id or this name. Cached for ten minutes."""
        now = time.monotonic()
        if now - self._listed_at > _DEFINITIONS_TTL:
            self._definitions = {}
            for entry in self._alert_list():
                alert_id = entry.get("alert_id") or entry.get("id")
                summary = "query_condition" not in entry and "condition" not in entry
                if isinstance(alert_id, str) and summary:
                    # A list that carries no query: it is in the alert itself.
                    response = self._http.get(f"/api/v2/{self._org}/alerts/{alert_id}")
                    if response.status_code < 400:
                        entry = {**response.json(), "id": alert_id}
                definition = parse_definition(entry)
                if definition is not None:
                    self._definitions[definition.id] = definition
            self._listed_at = now
        if key in self._definitions:
            return self._definitions[key]
        return next((d for d in self._definitions.values() if d.name == key), None)

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
        definitions: Callable[[str], AlertDefinition | None] | None = None,
    ) -> None:
        self.name = name
        self._client = client
        self._store = store
        self._recorder = recorder
        self._base_url = base_url
        self._overlap = overlap
        self._initial_lookback = initial_lookback
        self._definitions = definitions

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
            if alert is None:
                continue
            if alert.status is Status.FIRING and self._starts_firing(alert):
                alert = self._enriched(alert)
            if self._recorder.observe(alert) is not None:
                changes += 1
        return changes

    def _starts_firing(self, alert: Alert) -> bool:
        state = self._store.alert_state(alert.source, alert.fingerprint)
        return state is None or state.status != Status.FIRING.value

    def _enriched(self, alert: Alert) -> Alert:
        """The alert with what its definition says: a `triggers` row has the name and id only.

        Asked once per firing, not per evaluation row. A definition that cannot be fetched
        changes nothing: the alert is recorded as it came.
        """
        if self._definitions is None:
            return alert
        try:
            definition = self._definitions(alert.fingerprint)
        except Exception as exc:
            log.warning("openobserve: alert definition not read (%s)", type(exc).__name__)
            return alert
        if definition is None:
            return alert
        labels = dict(alert.labels)
        if definition.stream:
            labels["stream"] = definition.stream
        return alert.model_copy(
            update={
                "description": definition.description,
                "severity": definition.severity,
                "query": definition.query,
                "labels": labels,
            }
        )
