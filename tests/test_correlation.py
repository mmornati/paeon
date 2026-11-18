from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from paeon.correlation import Correlator
from paeon.recorder import Recorder
from paeon.rules import Rules, parse_rules
from paeon.schema import IncidentStatus, Severity, Status, TriageState
from paeon.store import Store
from support import alert

FIRING, RESOLVED = Status.FIRING, Status.RESOLVED


def recorder(store: Store, rules: Rules | None = None) -> Recorder:
    return Recorder(store, Correlator(store, rules or Rules()))


def kinds(store: Store, incident_id: int | None = None) -> list[str]:
    return [e.kind for e in store.events(incident_id=incident_id)]


def test_one_alert_is_one_incident_from_open_to_resolved(store: Store) -> None:
    rec = recorder(store)
    rec.observe(alert(FIRING, 0))
    [incident] = store.incidents()
    assert (incident.status, incident.title, incident.triage) == (
        IncidentStatus.OPEN,
        "alert a",
        TriageState.OFF,
    )
    rec.observe(alert(FIRING, 5))  # a repeat changes nothing
    rec.observe(alert(RESOLVED, 12))
    [incident] = store.incidents()
    assert incident.status is IncidentStatus.RESOLVED
    assert incident.resolved_at == alert(RESOLVED, 12).observed_at
    assert [member.status for member in incident.alerts] == [RESOLVED]
    assert kinds(store, incident.id) == [
        "incident.opened",
        "alert.firing",
        "alert.resolved",
        "incident.resolved",
    ]


def test_same_alert_within_the_window_reopens_the_incident(store: Store) -> None:
    rec = recorder(store)
    rec.observe(alert(FIRING, 0))
    rec.observe(alert(RESOLVED, 5))
    rec.observe(alert(FIRING, 30))  # 25 minutes after it closed
    [incident] = store.incidents()
    assert incident.status is IncidentStatus.OPEN
    assert incident.resolved_at is None
    events = list(store.events(incident_id=incident.id))
    assert [e.kind for e in events][-3:] == [
        "incident.reopened",
        "incident.correlated",
        "alert.firing",
    ]
    assert events[-2].payload["reason"] == "fingerprint"


def test_same_alert_after_the_window_is_a_new_incident(store: Store) -> None:
    rec = recorder(store)
    rec.observe(alert(FIRING, 0))
    rec.observe(alert(RESOLVED, 5))
    rec.observe(alert(FIRING, 36))  # 31 minutes after it closed
    assert [i.status for i in store.incidents()] == [IncidentStatus.RESOLVED, IncidentStatus.OPEN]
    rec.observe(alert(RESOLVED, 40))  # closes the second one, not the first
    assert kinds(store, 2)[-1] == "incident.resolved"
    assert kinds(store, 1).count("incident.resolved") == 1


def test_alerts_on_the_same_host_join_the_open_incident(store: Store) -> None:
    rec = recorder(store)
    host = {"host": "server-1"}
    rec.observe(alert(FIRING, 0, "proxy", source="uptime-kuma", labels=host))
    rec.observe(
        alert(FIRING, 2, "api", source="uptime-kuma", labels=host, severity=Severity.CRITICAL)
    )
    rec.observe(alert(FIRING, 3, "slo", source="openobserve", labels={"instance": "server-1"}))
    rec.observe(alert(FIRING, 3, "elsewhere", labels={"host": "nas"}))
    rec.observe(alert(FIRING, 9, "late", labels=host))  # after the 5 minute window
    first, nas, late = store.incidents()
    assert [member.fingerprint for member in first.alerts] == ["proxy", "api", "slo"]
    assert first.severity is Severity.CRITICAL  # the worst of its alerts
    assert first.host == "server-1"
    assert (nas.host, late.host) == ("nas", "server-1")
    correlated = [e.payload for e in store.events(kind="incident.correlated")]
    assert [c["reason"] for c in correlated] == ["host", "host"]

    # The incident stays open until its last alert resolves.
    rec.observe(alert(RESOLVED, 10, "proxy", source="uptime-kuma", labels=host))
    rec.observe(alert(RESOLVED, 11, "api", source="uptime-kuma", labels=host))
    assert store.incident(first.id).status is IncidentStatus.OPEN  # type: ignore[union-attr]
    rec.observe(alert(RESOLVED, 14, "slo", source="openobserve"))
    resolved = store.incident(first.id)
    assert resolved is not None
    assert resolved.status is IncidentStatus.RESOLVED


def test_host_rules_give_a_host_to_alerts_without_one(store: Store) -> None:
    rules = parse_rules(
        {"correlation": {"hosts": [{"match": {"title": "slo_.*"}, "host": "server-1"}]}}
    )
    rec = recorder(store, rules)
    rec.observe(alert(FIRING, 0, "kuma", labels={"hostname": "server-1"}))
    rec.observe(alert(FIRING, 1, "slo", title="slo_fast_burn_api"))
    assert len(store.incidents()) == 1


def test_alerts_without_a_host_do_not_group(store: Store) -> None:
    rec = recorder(store)
    rec.observe(alert(FIRING, 0, "a"))
    rec.observe(alert(FIRING, 1, "b"))
    assert len(store.incidents()) == 2


def test_flapping_is_flagged_once(store: Store) -> None:
    rec = recorder(store)
    for start in (0, 10, 20, 30):
        rec.observe(alert(FIRING, start))
        rec.observe(alert(RESOLVED, start + 2))
    [incident] = store.incidents()
    assert incident.flapping
    [flap] = list(store.events(kind="incident.flapping"))
    assert flap.payload["firings"] == 3
    # The flag was raised by the third firing, before its alert event.
    assert (
        kinds(store).index("incident.flapping")
        < [i for i, k in enumerate(kinds(store)) if k == "alert.firing"][2]
    )


def test_slow_repeats_are_not_flapping(store: Store) -> None:
    rec = recorder(store, parse_rules({"correlation": {"window_minutes": 600}}))
    for start in (0, 45, 90):  # three firings, but 90 minutes apart
        rec.observe(alert(FIRING, start))
        rec.observe(alert(RESOLVED, start + 2))
    assert not store.incidents()[0].flapping


def test_maintenance_window_flags_the_incident(store: Store) -> None:
    rules = parse_rules(
        {
            "maintenance": [
                {
                    "name": "updates",
                    "days": ["wed"],
                    "start": "07:30",
                    "duration_minutes": 60,
                    "match": {"labels": {"host": "server-1"}},
                }
            ]
        }
    )
    rec = recorder(store, rules)
    rec.observe(alert(FIRING, 0, "in", labels={"host": "server-1"}))  # Wednesday 08:00 UTC
    rec.observe(alert(FIRING, 0, "other-host", labels={"host": "nas"}))
    rec.observe(alert(FIRING, 45, "after", labels={"host": "server-1"}))
    assert [i.maintenance for i in store.incidents()] == ["updates", None, None]
    opened = next(iter(store.events(kind="incident.opened")))
    assert opened.payload["maintenance"] == "updates"


def test_incidents_wait_for_triage_when_it_is_enabled(store: Store) -> None:
    rules = parse_rules(
        {
            "models": {"triage": {"backend": "openai", "model": "m"}},
            "triage": {"enabled": True},
        }
    )
    recorder(store, rules).observe(alert(FIRING, 0))
    assert store.incidents(triage=TriageState.PENDING)[0].id == 1


def test_first_sight_of_a_healthy_alert_opens_nothing(store: Store) -> None:
    recorder(store).observe(alert(RESOLVED, 0))
    assert store.incidents() == []
    assert kinds(store) == []


def test_a_phase_0_database_is_upgraded_in_place(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL,"
        " kind TEXT NOT NULL, source TEXT, fingerprint TEXT, payload TEXT NOT NULL);"
        "INSERT INTO events (recorded_at, kind, payload)"
        " VALUES ('2026-11-18T08:00:00+00:00', 'alert.firing', '{}');"
    )
    db.close()
    store = Store(path)
    try:
        payload: dict[str, Any] = {"x": 1}
        store.append("incident.opened", payload, incident_id=7)
        assert [e.incident_id for e in store.events()] == [None, 7]
    finally:
        store.close()
