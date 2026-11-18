"""Push adapters: payloads posted to Paeon's webhook endpoints.

- uptime-kuma's "Webhook" notification (`application/json` body with `heartbeat`, `monitor`, `msg`).
- The Alertmanager webhook (version 4), which is also what Grafana and many other tools can send.

Everything in these payloads is written by someone else (a monitor name, a response body): it is
carried as data and never interpreted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from paeon.schema import Alert, Severity, Status

_KUMA_DOWN = 0
_KUMA_UP = 1  # 2 is "pending" and 3 is "maintenance": neither is a verdict


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _parse_time(value: Any, default: datetime) -> datetime:
    """An aware datetime from an ISO-like string; naive values are read as UTC (kuma's default)."""
    if not isinstance(value, str) or not value:
        return default
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return default
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def parse_uptime_kuma(
    payload: dict[str, Any], *, now: datetime, source: str = "uptime-kuma"
) -> Alert | None:
    """An observation from an uptime-kuma webhook, or None (test message, pending, maintenance)."""
    heartbeat = payload.get("heartbeat")
    monitor = payload.get("monitor")
    if not isinstance(heartbeat, dict) or not isinstance(monitor, dict):
        return None
    code = heartbeat.get("status")
    if code == _KUMA_DOWN:
        status = Status.FIRING
    elif code == _KUMA_UP:
        status = Status.RESOLVED
    else:
        return None
    monitor_id = monitor.get("id", heartbeat.get("monitorID"))
    if monitor_id is None:
        return None
    name = _text(monitor.get("name")) or f"monitor {monitor_id}"
    labels = {"monitor_id": str(monitor_id), "monitor": name}
    for key in ("type", "hostname", "url"):
        value = _text(monitor.get(key))
        if value:
            labels[key] = value
    observed_at = _parse_time(heartbeat.get("time"), now)
    return Alert(
        source=source,
        fingerprint=str(monitor_id),
        status=status,
        severity=Severity.CRITICAL if status is Status.FIRING else Severity.UNKNOWN,
        title=name,
        description=_text(heartbeat.get("msg")) or _text(payload.get("msg")),
        labels=labels,
        starts_at=observed_at,
        observed_at=observed_at,
        raw=payload,
    )


_SEVERITIES = {
    "critical": Severity.CRITICAL,
    "page": Severity.CRITICAL,
    "error": Severity.CRITICAL,
    "warning": Severity.WARNING,
    "warn": Severity.WARNING,
    "info": Severity.INFO,
}


def parse_alertmanager(
    payload: dict[str, Any], *, now: datetime, source: str = "alertmanager"
) -> list[Alert]:
    """The alerts of one Alertmanager webhook call. Entries without a fingerprint are skipped."""
    entries = payload.get("alerts")
    if not isinstance(entries, list):
        return []
    alerts: list[Alert] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fingerprint = _text(entry.get("fingerprint"))
        if not fingerprint:
            continue
        raw_labels = entry.get("labels")
        raw_annotations = entry.get("annotations")
        labels = (
            {str(k): str(v) for k, v in raw_labels.items()} if isinstance(raw_labels, dict) else {}
        )
        annotations = raw_annotations if isinstance(raw_annotations, dict) else {}
        resolved = entry.get("status") == "resolved"
        starts_at = _parse_time(entry.get("startsAt"), now)
        observed_at = _parse_time(entry.get("endsAt"), now) if resolved else now
        if observed_at < starts_at:  # Alertmanager sends year 1 as "no end yet"
            observed_at = now
        alerts.append(
            Alert(
                source=source,
                fingerprint=fingerprint,
                status=Status.RESOLVED if resolved else Status.FIRING,
                severity=_SEVERITIES.get(labels.get("severity", "").lower(), Severity.UNKNOWN),
                title=labels.get("alertname") or _text(annotations.get("summary")) or fingerprint,
                description=_text(annotations.get("description"))
                or _text(annotations.get("summary")),
                labels=labels,
                starts_at=starts_at,
                observed_at=observed_at,
                source_url=_text(entry.get("generatorURL")) or None,
                raw=entry,
            )
        )
    return alerts
