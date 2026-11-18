from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from paeon.schema import Severity, Status
from paeon.sources.webhook import parse_alertmanager, parse_uptime_kuma

NOW = datetime(2026, 11, 18, 8, 0, tzinfo=UTC)


def kuma(status: int, **heartbeat: Any) -> dict[str, Any]:
    return {
        "heartbeat": {
            "monitorID": 12,
            "status": status,
            "time": "2026-11-18 07:58:30.123",
            "msg": "connect ECONNREFUSED 10.0.0.5:8096",
            **heartbeat,
        },
        "monitor": {
            "id": 12,
            "name": "Jellyfin",
            "type": "http",
            "url": "https://jellyfin.example.org",
            "hostname": None,
        },
        "msg": "[Jellyfin] [🔴 Down] connect ECONNREFUSED",
    }


def test_kuma_down_and_up() -> None:
    down = parse_uptime_kuma(kuma(0), now=NOW)
    assert down is not None
    assert down.status is Status.FIRING
    assert down.severity is Severity.CRITICAL
    assert down.fingerprint == "12"
    assert down.title == "Jellyfin"
    assert down.description.startswith("connect ECONNREFUSED")
    assert down.labels == {
        "monitor_id": "12",
        "monitor": "Jellyfin",
        "type": "http",
        "url": "https://jellyfin.example.org",
    }
    assert down.observed_at == datetime(2026, 11, 18, 7, 58, 30, 123000, tzinfo=UTC)

    up = parse_uptime_kuma(kuma(1), now=NOW)
    assert up is not None
    assert up.status is Status.RESOLVED


@pytest.mark.parametrize(
    "payload",
    [
        {"heartbeat": None, "monitor": None, "msg": "Paeon Testing"},  # the "Test" button
        kuma(2),  # pending
        kuma(3),  # maintenance
        {"heartbeat": {"status": 0}, "monitor": {}},  # no monitor id
        {},
    ],
)
def test_kuma_payloads_that_are_not_verdicts(payload: dict[str, Any]) -> None:
    assert parse_uptime_kuma(payload, now=NOW) is None


def test_kuma_bad_time_falls_back_to_now() -> None:
    alert = parse_uptime_kuma(kuma(0, time="not a date"), now=NOW)
    assert alert is not None
    assert alert.observed_at == NOW


def test_kuma_monitor_without_name() -> None:
    payload = kuma(0)
    payload["monitor"] = {"id": 7}
    alert = parse_uptime_kuma(payload, now=NOW)
    assert alert is not None
    assert alert.title == "monitor 7"


def alertmanager() -> dict[str, Any]:
    return {
        "version": "4",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "DiskFull", "severity": "warning", "instance": "server-1"},
                "annotations": {"summary": "disk", "description": "/ is at 93 %"},
                "startsAt": "2026-11-18T07:50:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph?g0.expr=x",
                "fingerprint": "abc123",
            },
            {
                "status": "resolved",
                "labels": {"alertname": "HostDown", "severity": "page"},
                "annotations": {},
                "startsAt": "2026-11-18T07:00:00Z",
                "endsAt": "2026-11-18T07:30:00Z",
                "fingerprint": "def456",
            },
            {"status": "firing", "labels": {}},  # no fingerprint
            "junk",
        ],
    }


def test_alertmanager() -> None:
    firing, resolved = parse_alertmanager(alertmanager(), now=NOW)
    assert firing.status is Status.FIRING
    assert firing.severity is Severity.WARNING
    assert firing.title == "DiskFull"
    assert firing.description == "/ is at 93 %"
    assert firing.observed_at == NOW
    assert firing.source_url == "http://prometheus:9090/graph?g0.expr=x"
    assert firing.labels["instance"] == "server-1"

    assert resolved.status is Status.RESOLVED
    assert resolved.severity is Severity.CRITICAL
    assert resolved.observed_at == datetime(2026, 11, 18, 7, 30, tzinfo=UTC)


def test_alertmanager_without_alerts() -> None:
    assert parse_alertmanager({"alerts": "none"}, now=NOW) == []
    assert parse_alertmanager({}, now=NOW) == []
