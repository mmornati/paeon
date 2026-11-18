"""SQLite store: the append-only event log, plus the small mutable state the adapters need.

`events` is the record: rows are only ever inserted, and SQLite triggers refuse UPDATE and DELETE,
so a bug elsewhere cannot rewrite history. `alert_state` and `cursors` are working state that can be
rebuilt from the events and the sources.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
"""


@dataclass(frozen=True)
class Event:
    id: int
    recorded_at: datetime
    kind: str
    source: str | None
    fingerprint: str | None
    payload: dict[str, Any]


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
        now: datetime | None = None,
    ) -> int:
        recorded_at = (now or datetime.now(UTC)).isoformat()
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO events (recorded_at, kind, source, fingerprint, payload)"
                " VALUES (?, ?, ?, ?, ?)",
                (recorded_at, kind, source, fingerprint, body),
            )
        event_id = cursor.lastrowid
        if event_id is None:  # pragma: no cover - sqlite always sets it after an INSERT
            raise RuntimeError("event was not inserted")
        return event_id

    def events(self, *, after_id: int = 0, kind: str | None = None) -> Iterator[Event]:
        sql = "SELECT id, recorded_at, kind, source, fingerprint, payload FROM events WHERE id > ?"
        params: list[Any] = [after_id]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY id", params).fetchall()
        for row in rows:
            yield Event(
                id=row[0],
                recorded_at=datetime.fromisoformat(row[1]),
                kind=row[2],
                source=row[3],
                fingerprint=row[4],
                payload=json.loads(row[5]),
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
