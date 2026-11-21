"""SQLite store: the append-only event log, plus the small mutable state built from it.

`events` is the record: rows are only ever inserted, and SQLite triggers refuse UPDATE and DELETE,
so a bug elsewhere cannot rewrite history. `alert_state`, `incidents`, `incident_alerts` and
`cursors` are working state: projections kept for fast lookups, never the source of truth.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from paeon.schema import (
    Incident,
    IncidentAlert,
    IncidentStatus,
    Severity,
    StageState,
    Status,
)

Stage = Literal["triage", "investigation", "postmortem"]
# The two tables of what the console adds. A table name cannot be a parameter of a statement:
# it is one of these two literals, never anything read from a request.
_ConsoleTable = Literal["console_channels", "console_sources"]
_ADD_STAGE = {
    "investigation": "ALTER TABLE incidents ADD COLUMN investigation TEXT NOT NULL DEFAULT 'off'",
    "postmortem": "ALTER TABLE incidents ADD COLUMN postmortem TEXT NOT NULL DEFAULT 'off'",
}
_SET_STAGE = {
    "triage": "UPDATE incidents SET triage = ? WHERE id = ?",
    "investigation": "UPDATE incidents SET investigation = ? WHERE id = ?",
    "postmortem": "UPDATE incidents SET postmortem = ? WHERE id = ?",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    kind        TEXT NOT NULL,
    source      TEXT,
    fingerprint TEXT,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_alert ON events (source, fingerprint, id);
CREATE INDEX IF NOT EXISTS events_kind ON events (kind, id);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;

CREATE TABLE IF NOT EXISTS alert_state (
    source      TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    status      TEXT NOT NULL,
    since       TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    PRIMARY KEY (source, fingerprint)
);

CREATE TABLE IF NOT EXISTS cursors (
    source TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS console_channels (
    name       TEXT PRIMARY KEY,
    type       TEXT NOT NULL,
    config     TEXT NOT NULL,
    stages     TEXT NOT NULL,
    secrets    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS console_sources (
    name       TEXT PRIMARY KEY,
    type       TEXT NOT NULL,
    config     TEXT NOT NULL,
    stages     TEXT NOT NULL,
    secrets    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS console_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    status           TEXT NOT NULL,
    title            TEXT NOT NULL,
    severity         TEXT NOT NULL,
    host             TEXT,
    opened_at        TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    resolved_at      TEXT,
    flapping         INTEGER NOT NULL DEFAULT 0,
    maintenance      TEXT,
    triage           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS incidents_status ON incidents (status, id);
CREATE INDEX IF NOT EXISTS incidents_activity ON incidents (last_activity_at, id);

CREATE TABLE IF NOT EXISTS incident_alerts (
    incident_id INTEGER NOT NULL REFERENCES incidents (id),
    source      TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL,
    PRIMARY KEY (incident_id, source, fingerprint)
);
CREATE INDEX IF NOT EXISTS incident_alerts_alert ON incident_alerts (source, fingerprint);
"""

_SELECT_INCIDENTS = (
    "SELECT id, status, title, severity, host, opened_at, last_activity_at, resolved_at,"
    " flapping, maintenance, triage, investigation, postmortem FROM incidents"
)


# Open before resolved, in the explorer's order.
_RANK = "(CASE status WHEN 'open' THEN 0 ELSE 1 END)"
_FACETS = ("status", "severity", "host", "triage", "investigation")


@dataclass(frozen=True)
class IncidentFilter:
    """What the incident explorer asks for. An empty choice means any value."""

    statuses: tuple[IncidentStatus, ...] = ()
    severities: tuple[Severity, ...] = ()
    hosts: tuple[str, ...] = ()  # "" stands for an incident with no host
    triage: tuple[StageState, ...] = ()
    investigation: tuple[StageState, ...] = ()
    text: str = ""  # in the title or the host, or "#12" for an incident's number
    active_since: datetime | None = None  # open incidents match whatever this says


@dataclass(frozen=True)
class IncidentCursor:
    """Where a page of the explorer ended: the key of its last row."""

    rank: int  # 0 open, 1 resolved
    last_activity_at: datetime
    id: int

    @classmethod
    def of(cls, incident: Incident) -> IncidentCursor:
        rank = 0 if incident.status is IncidentStatus.OPEN else 1
        return cls(rank, incident.last_activity_at, incident.id)


def _filter_sql(where: IncidentFilter, *, without: str = "") -> tuple[str, list[Any]]:
    clause = "1 = 1"
    params: list[Any] = []
    choices: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("status", tuple(s.value for s in where.statuses)),
        ("severity", tuple(s.value for s in where.severities)),
        ("host", where.hosts),
        ("triage", tuple(s.value for s in where.triage)),
        ("investigation", tuple(s.value for s in where.investigation)),
    )
    for column, values in choices:
        if values and column != without:
            target = "COALESCE(host, '')" if column == "host" else column
            clause += f" AND {target} IN ({', '.join('?' * len(values))})"
            params += values
    if where.active_since is not None:
        clause += " AND (status = 'open' OR last_activity_at >= ?)"
        params.append(_iso(where.active_since))
    text = where.text.strip()
    if text:
        number = text.removeprefix("#")
        if number.isdigit():
            clause += " AND id = ?"
            params.append(int(number))
        else:
            like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            clause += " AND (title LIKE ? ESCAPE '\\' OR host LIKE ? ESCAPE '\\')"
            params += [like, like]
    return clause, params


def _iso(moment: datetime) -> str:
    """UTC ISO 8601, so that stored timestamps compare as text."""
    return moment.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class Event:
    id: int
    recorded_at: datetime
    kind: str
    source: str | None
    fingerprint: str | None
    payload: dict[str, Any]
    incident_id: int | None = None


@dataclass(frozen=True)
class ConsoleChannel:
    """A channel added in the console. `secrets` are sealed (see `paeon.vault`), by key."""

    name: str
    type: str
    config: dict[str, Any]
    stages: tuple[str, ...]
    secrets: dict[str, str]
    updated_at: datetime


@dataclass(frozen=True)
class AlertState:
    status: str
    since: datetime
    last_seen: datetime


class Store:
    """One SQLite file. Safe to share between the poll loop and the HTTP handlers."""

    def __init__(self, path: Path | str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(_SCHEMA)
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(events)")}
        if "incident_id" not in columns:  # a database created by phase 0
            self._db.execute("ALTER TABLE events ADD COLUMN incident_id INTEGER")
        self._db.execute("CREATE INDEX IF NOT EXISTS events_incident ON events (incident_id, id)")
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(incidents)")}
        for stage in ("investigation", "postmortem"):  # absent from a phase 1 database
            if stage not in columns:
                self._db.execute(_ADD_STAGE[stage])

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- events -------------------------------------------------------------------------------

    def append(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        source: str | None = None,
        fingerprint: str | None = None,
        incident_id: int | None = None,
        now: datetime | None = None,
    ) -> int:
        recorded_at = _iso(now or datetime.now(UTC))
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO events (recorded_at, kind, source, fingerprint, incident_id, payload)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (recorded_at, kind, source, fingerprint, incident_id, body),
            )
        event_id = cursor.lastrowid
        if event_id is None:  # pragma: no cover - sqlite always sets it after an INSERT
            raise RuntimeError("event was not inserted")
        return event_id

    def events(
        self,
        *,
        after_id: int = 0,
        before_id: int | None = None,
        kind: str | None = None,
        kinds: Sequence[str] | None = None,
        source: str | None = None,
        fingerprint: str | None = None,
        incident_id: int | None = None,
        incident_ids: Sequence[int] | None = None,
        recorded_since: datetime | None = None,
        newest_first: bool = False,
        limit: int | None = None,
    ) -> Iterator[Event]:
        sql = (
            "SELECT id, recorded_at, kind, source, fingerprint, payload, incident_id"
            " FROM events WHERE id > ?"
        )
        params: list[Any] = [after_id]
        if kinds is not None:
            sql += f" AND kind IN ({', '.join('?' * len(kinds))})" if kinds else " AND 0"
            params += kinds
        if incident_ids is not None:
            marks = ", ".join("?" * len(incident_ids))
            sql += f" AND incident_id IN ({marks})" if incident_ids else " AND 0"
            params += incident_ids
        for clause, value in (
            (" AND id < ?", before_id),
            (" AND kind = ?", kind),
            (" AND source = ?", source),
            (" AND fingerprint = ?", fingerprint),
            (" AND incident_id = ?", incident_id),
            (" AND recorded_at >= ?", None if recorded_since is None else _iso(recorded_since)),
        ):
            if value is not None:
                sql += clause
                params.append(value)
        sql += " ORDER BY id DESC" if newest_first else " ORDER BY id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        for row in rows:
            yield Event(
                id=row[0],
                recorded_at=datetime.fromisoformat(row[1]),
                kind=row[2],
                source=row[3],
                fingerprint=row[4],
                payload=json.loads(row[5]),
                incident_id=row[6],
            )

    def check(self) -> None:
        """Raise if the database cannot be read (the health check)."""
        with self._lock:
            self._db.execute("SELECT 1 FROM events LIMIT 1").fetchall()

    # --- alert state --------------------------------------------------------------------------

    def alert_state(self, source: str, fingerprint: str) -> AlertState | None:
        with self._lock:
            row = self._db.execute(
                "SELECT status, since, last_seen FROM alert_state"
                " WHERE source = ? AND fingerprint = ?",
                (source, fingerprint),
            ).fetchone()
        if row is None:
            return None
        return AlertState(
            status=row[0],
            since=datetime.fromisoformat(row[1]),
            last_seen=datetime.fromisoformat(row[2]),
        )

    def set_alert_state(
        self, source: str, fingerprint: str, *, status: str, since: datetime, last_seen: datetime
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO alert_state (source, fingerprint, status, since, last_seen)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (source, fingerprint) DO UPDATE SET"
                " status = excluded.status, since = excluded.since,"
                " last_seen = excluded.last_seen",
                (source, fingerprint, status, since.isoformat(), last_seen.isoformat()),
            )

    # --- incidents ----------------------------------------------------------------------------

    def open_incident(
        self,
        *,
        title: str,
        severity: Severity,
        host: str | None,
        opened_at: datetime,
        maintenance: str | None,
        triage: StageState,
        investigation: StageState = StageState.OFF,
    ) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO incidents (status, title, severity, host, opened_at,"
                " last_activity_at, maintenance, triage, investigation)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    IncidentStatus.OPEN.value,
                    title,
                    severity.value,
                    host,
                    _iso(opened_at),
                    _iso(opened_at),
                    maintenance,
                    triage.value,
                    investigation.value,
                ),
            )
        incident_id = cursor.lastrowid
        if incident_id is None:  # pragma: no cover - sqlite always sets it after an INSERT
            raise RuntimeError("incident was not inserted")
        return incident_id

    def save_incident(self, incident: Incident) -> None:
        """Write back what correlation changes. Triage state has its own setter: it is written
        from another thread, and a whole-row update would overwrite one with the other."""
        with self._lock:
            self._db.execute(
                "UPDATE incidents SET status = ?, severity = ?, last_activity_at = ?,"
                " resolved_at = ?, flapping = ? WHERE id = ?",
                (
                    incident.status.value,
                    incident.severity.value,
                    _iso(incident.last_activity_at),
                    None if incident.resolved_at is None else _iso(incident.resolved_at),
                    int(incident.flapping),
                    incident.id,
                ),
            )

    def set_stage(self, incident_id: int, stage: Stage, state: StageState) -> None:
        with self._lock:
            self._db.execute(_SET_STAGE[stage], (state.value, incident_id))

    def attach_alert(
        self, incident_id: int, *, source: str, fingerprint: str, title: str, status: Status
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO incident_alerts (incident_id, source, fingerprint, title, status)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (incident_id, source, fingerprint) DO UPDATE SET"
                " title = excluded.title, status = excluded.status",
                (incident_id, source, fingerprint, title, status.value),
            )

    def incident_of(self, source: str, fingerprint: str) -> int | None:
        """The most recent incident this alert is attached to."""
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(incident_id) FROM incident_alerts WHERE source = ? AND fingerprint = ?",
                (source, fingerprint),
            ).fetchone()
        return None if row is None or row[0] is None else int(row[0])

    def incident(self, incident_id: int) -> Incident | None:
        found = self._incidents(_SELECT_INCIDENTS + " WHERE id = ?", [incident_id])
        return found[0] if found else None

    def incidents(
        self,
        *,
        status: IncidentStatus | None = None,
        triage: StageState | None = None,
        investigation: StageState | None = None,
        postmortem: StageState | None = None,
        active_since: datetime | None = None,
    ) -> list[Incident]:
        sql = _SELECT_INCIDENTS + " WHERE 1 = 1"
        params: list[Any] = []
        for clause, value in (
            (" AND status = ?", None if status is None else status.value),
            (" AND triage = ?", None if triage is None else triage.value),
            (" AND investigation = ?", None if investigation is None else investigation.value),
            (" AND postmortem = ?", None if postmortem is None else postmortem.value),
            (" AND last_activity_at >= ?", None if active_since is None else _iso(active_since)),
        ):
            if value is not None:
                sql += clause
                params.append(value)
        return self._incidents(sql, params)

    def count_incidents(self, *, status: IncidentStatus | None = None) -> int:
        sql = "SELECT COUNT(*) FROM incidents"
        params: list[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status.value)
        with self._lock:
            return int(self._db.execute(sql, params).fetchone()[0])

    # --- the incident explorer -------------------------------------------------------------

    def search_incidents(
        self,
        where: IncidentFilter,
        *,
        after: IncidentCursor | None = None,
        limit: int = 50,
    ) -> list[Incident]:
        """Open incidents first, then the rest, each newest activity first; a page at a time.

        `after` is the key of the last row of the previous page: rows are paged by position
        in that order, not by number, so an incident that opens meanwhile shifts nothing.
        """
        clause, params = _filter_sql(where)
        if after is not None:
            clause += (
                " AND (" + _RANK + " > ? OR (" + _RANK + " = ? AND (last_activity_at < ?"
                " OR (last_activity_at = ? AND id < ?))))"
            )
            stamp = _iso(after.last_activity_at)
            params += [after.rank, after.rank, stamp, stamp, after.id]
        return self._incidents(
            _SELECT_INCIDENTS + " WHERE " + clause,
            params,
            order=f" ORDER BY {_RANK}, last_activity_at DESC, id DESC LIMIT {int(limit)}",
        )

    def count_incidents_by(self, where: IncidentFilter, column: str) -> dict[str, int]:
        """How many incidents match, per value of one column. The column's own choice is left
        out, so the counts say what picking another value would show."""
        if column not in _FACETS:
            raise ValueError(f"not a facet: {column}")
        clause, params = _filter_sql(where, without=column)
        # The column is one of _FACETS and the clause is built from fixed text: values are bound.
        sql = f"SELECT COALESCE({column}, ''), COUNT(*) FROM incidents WHERE {clause}"  # noqa: S608
        with self._lock:
            rows = self._db.execute(sql + f" GROUP BY {column}", params).fetchall()
        return {str(value): int(count) for value, count in rows}

    def incident_openings(
        self, where: IncidentFilter, since: datetime
    ) -> list[tuple[datetime, str]]:
        """When each matching incident opened since a moment, and its severity: for a chart."""
        clause, params = _filter_sql(where)
        sql = "SELECT opened_at, severity FROM incidents WHERE " + clause  # noqa: S608
        sql += " AND opened_at >= ?"
        with self._lock:
            rows = self._db.execute(sql, [*params, _iso(since)]).fetchall()
        return [(datetime.fromisoformat(row[0]), str(row[1])) for row in rows]

    def _incidents(
        self, sql: str, params: list[Any], *, order: str = " ORDER BY id"
    ) -> list[Incident]:
        with self._lock:
            rows = self._db.execute(sql + order, params).fetchall()
            members: dict[int, list[IncidentAlert]] = {row[0]: [] for row in rows}
            for row in rows:
                for member in self._db.execute(
                    "SELECT source, fingerprint, title, status FROM incident_alerts"
                    " WHERE incident_id = ? ORDER BY rowid",
                    (row[0],),
                ):
                    members[row[0]].append(
                        IncidentAlert(
                            source=member[0],
                            fingerprint=member[1],
                            title=member[2],
                            status=Status(member[3]),
                        )
                    )
        return [
            Incident(
                id=row[0],
                status=IncidentStatus(row[1]),
                title=row[2],
                severity=Severity(row[3]),
                host=row[4],
                opened_at=datetime.fromisoformat(row[5]),
                last_activity_at=datetime.fromisoformat(row[6]),
                resolved_at=None if row[7] is None else datetime.fromisoformat(row[7]),
                flapping=bool(row[8]),
                maintenance=row[9],
                triage=StageState(row[10]),
                investigation=StageState(row[11]),
                postmortem=StageState(row[12]),
                alerts=tuple(members[row[0]]),
            )
            for row in rows
        ]

    # --- cursors ------------------------------------------------------------------------------

    def cursor(self, source: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM cursors WHERE source = ?", (source,)
            ).fetchone()
        return None if row is None else str(row[0])

    def set_cursor(self, source: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO cursors (source, value) VALUES (?, ?)"
                " ON CONFLICT (source) DO UPDATE SET value = excluded.value",
                (source, value),
            )

    # --- channels added in the console --------------------------------------------------------
    # Working state, like `incidents`: the log says who changed what and when, and holds
    # neither an address nor a secret, since it can never forget one.

    def console_channels(self) -> list[ConsoleChannel]:
        return self._console_rows("console_channels")

    def save_console_channel(self, channel: ConsoleChannel) -> None:
        self._save_console_row("console_channels", channel)

    def delete_console_channel(self, name: str) -> bool:
        return self._delete_console_row("console_channels", name)

    # Sources added in the console are kept the same way; `stages` is empty for them.

    def console_sources(self) -> list[ConsoleChannel]:
        return self._console_rows("console_sources")

    def save_console_source(self, source: ConsoleChannel) -> None:
        self._save_console_row("console_sources", source)

    def delete_console_source(self, name: str) -> bool:
        return self._delete_console_row("console_sources", name)

    def _console_rows(self, table: _ConsoleTable) -> list[ConsoleChannel]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT name, type, config, stages, secrets, updated_at FROM {table}"  # noqa: S608
                " ORDER BY name"
            ).fetchall()
        return [
            ConsoleChannel(
                name=row[0],
                type=row[1],
                config=json.loads(row[2]),
                stages=tuple(json.loads(row[3])),
                secrets=json.loads(row[4]),
                updated_at=datetime.fromisoformat(row[5]),
            )
            for row in rows
        ]

    def _save_console_row(self, table: _ConsoleTable, row: ConsoleChannel) -> None:
        with self._lock:
            self._db.execute(
                f"INSERT INTO {table} (name, type, config, stages, secrets, updated_at)"  # noqa: S608
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (name) DO UPDATE SET type = excluded.type, config = excluded.config,"
                " stages = excluded.stages, secrets = excluded.secrets,"
                " updated_at = excluded.updated_at",
                (
                    row.name,
                    row.type,
                    json.dumps(row.config, sort_keys=True),
                    json.dumps(list(row.stages)),
                    json.dumps(row.secrets, sort_keys=True),
                    _iso(row.updated_at),
                ),
            )

    def _delete_console_row(self, table: _ConsoleTable, name: str) -> bool:
        with self._lock:
            cursor = self._db.execute(f"DELETE FROM {table} WHERE name = ?", (name,))  # noqa: S608
        return cursor.rowcount > 0

    # What the console keeps for itself: the hash of the password chosen at setup.

    def console_state(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM console_state WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else str(row[0])

    def set_console_state(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO console_state (key, value) VALUES (?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
