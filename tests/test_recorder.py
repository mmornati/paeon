from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from paeon.recorder import Recorder
from paeon.schema import Alert, Status
from paeon.store import Store

T0 = datetime(2026, 11, 18, 8, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "paeon.sqlite")
    yield s
    s.close()


def obs(status: Status, minutes: int, fingerprint: str = "a") -> Alert:
    at = T0 + timedelta(minutes=minutes)
    return Alert(
        source="test",
        fingerprint=fingerprint,
        status=status,
        title="t",
        starts_at=at,
        observed_at=at,
    )


def kinds(store: Store) -> list[str]:
    return [e.kind for e in store.events()]


def test_repeated_firing_is_one_event(store: Store) -> None:
    recorder = Recorder(store)
    assert recorder.observe(obs(Status.FIRING, 0)) is not None
    assert recorder.observe(obs(Status.FIRING, 5)) is None
    assert recorder.observe(obs(Status.FIRING, 10)) is None
    assert kinds(store) == ["alert.firing"]
    state = store.alert_state("test", "a")
    assert state is not None
    assert state.since == T0
    assert state.last_seen == T0 + timedelta(minutes=10)


def test_resolve_carries_when_it_fired(store: Store) -> None:
    recorder = Recorder(store)
    recorder.observe(obs(Status.FIRING, 0))
    recorder.observe(obs(Status.RESOLVED, 15))
    recorder.observe(obs(Status.RESOLVED, 20))
    events = list(store.events())
    assert [e.kind for e in events] == ["alert.firing", "alert.resolved"]
    assert events[1].payload["fired_at"] == T0.isoformat()


def test_first_sight_of_a_healthy_alert_is_not_a_recovery(store: Store) -> None:
    recorder = Recorder(store)
    assert recorder.observe(obs(Status.RESOLVED, 0)) is None
    assert kinds(store) == []
    assert recorder.observe(obs(Status.FIRING, 5)) is not None


def test_old_or_duplicate_observations_are_ignored(store: Store) -> None:
    recorder = Recorder(store)
    recorder.observe(obs(Status.FIRING, 10))
    assert recorder.observe(obs(Status.RESOLVED, 10)) is None  # same instant: duplicate row
    assert recorder.observe(obs(Status.RESOLVED, 5)) is None  # older than what we know
    assert kinds(store) == ["alert.firing"]


def test_alerts_are_independent(store: Store) -> None:
    recorder = Recorder(store)
    recorder.observe(obs(Status.FIRING, 0, "a"))
    recorder.observe(obs(Status.FIRING, 0, "b"))
    assert len(kinds(store)) == 2


def test_events_cannot_be_rewritten(store: Store, tmp_path: Path) -> None:
    store.append("x", {"a": 1})
    db = sqlite3.connect(tmp_path / "paeon.sqlite")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("UPDATE events SET kind = 'y'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("DELETE FROM events")
    db.close()
    assert [e.kind for e in store.events()] == ["x"]


def test_events_filter(store: Store) -> None:
    first = store.append("a", {})
    store.append("b", {})
    assert [e.kind for e in store.events(kind="b")] == ["b"]
    assert [e.kind for e in store.events(after_id=first)] == ["b"]


def test_source_stale_fires_and_clears(store: Store) -> None:
    recorder = Recorder(store)
    limit = timedelta(minutes=15)
    assert recorder.source_health("oo", last_data_at=T0, now=T0 + limit, stale_after=limit) is None
    late = T0 + timedelta(minutes=20)
    assert recorder.source_health("oo", last_data_at=T0, now=late, stale_after=limit) is not None
    back = T0 + timedelta(minutes=30)
    assert recorder.source_health("oo", last_data_at=back, now=back, stale_after=limit) is not None
    events = list(store.events())
    assert [e.kind for e in events] == ["alert.firing", "alert.resolved"]
    assert events[0].fingerprint == "source_stale:oo"


def test_source_never_seen_is_stale(store: Store) -> None:
    recorder = Recorder(store)
    recorder.source_health("oo", last_data_at=None, now=T0, stale_after=timedelta(minutes=15))
    assert kinds(store) == ["alert.firing"]
