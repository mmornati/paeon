"""SQLite store: the append-only event log, plus the small mutable state built from it.

`events` is the record: rows are only ever inserted, and SQLite triggers refuse UPDATE and DELETE,
so a bug elsewhere cannot rewrite history. `alert_state`, `incidents`, `incident_alerts` and
`cursors` are working state: projections kept for fast lookups, never the source of truth.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
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
        kind: str | None = None,
        source: str | None = None,
        fingerprint: str | None = None,
        incident_id: int | None = None,
        recorded_since: datetime | None = None,
        newest_first: bool = False,
        limit: int | None = None,
    ) -> Iterator[Event]:
        sql = (
            "SELECT id, recorded_at, kind, source, fingerprint, payload, incident_id"
            " FROM events WHERE id > ?"
        )
        params: list[Any] = [after_id]
        for clause, value in (
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

    def _incidents(self, sql: str, params: list[Any]) -> list[Incident]:
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY id", params).fetchall()
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
