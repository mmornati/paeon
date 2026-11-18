"""A small server's bad evening, written straight into a store: what the console is shown with.

Synthetic. Used by the console's tests, and by `python tests/ui_data.py <db>` to look at the
console with something in it.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from paeon.schema import Incident, IncidentStatus, Severity, StageState, Status
from paeon.store import Store

FINDINGS: dict[str, Any] = {
    "summary": "Traefik answers 502 for media.example because media-server cannot write: /srv"
    " has been full since the 20:30 backup.",
    "impact": "The media service is down for everyone since 21:04. Nothing else is affected.",
    "root_cause": {
        "statement": "The backup wrote its archive to /srv instead of the backup volume and"
        " filled the disk.",
        "confidence": 0.78,
    },
    "evidence": [
        {
            "claim": "Traefik logs 502 for media.example from 21:03:58.",
            "call": "c1",
            "verified": True,
        },
        {"claim": "media-server restarts in a loop, exit code 1.", "call": "c2", "verified": True},
        {"claim": "/srv has 0 bytes available.", "call": "c3", "verified": True},
        {"claim": "The backup job was changed last week.", "call": "c9", "verified": False},
    ],
    "hypotheses_rejected": [
        {
            "hypothesis": "A bad deploy of media-server.",
            "reason": "The image has not changed in 12 days.",
        }
    ],
    "plan": [
        {
            "action": "Move /srv/backup-2026-11-18.tar to the backup volume.",
            "why": "Frees 41 GB.",
            "risk": "low",
        },
        {"action": "Restart media-server.", "why": "It does not recover by itself.", "risk": "low"},
        {
            "action": "Fix the target path of the backup job.",
            "why": "Or it happens again tomorrow.",
            "risk": "medium",
        },
    ],
}


def _model_call(stage: str, model: str, cost: float, **more: Any) -> dict[str, Any]:
    return {
        "stage": stage,
        "alias": stage,
        "backend": "anthropic",
        "model_requested": model,
        "model": model,
        "input_tokens": 1840,
        "output_tokens": 212,
        "cached_tokens": 0,
        "cost": cost,
        "duration_ms": 1310,
        "prompt_version": f"{stage}-1",
        **more,
    }


def populate(store: Store, now: datetime | None = None) -> dict[str, int]:
    """Five incidents, one per situation the console has to show. Returns their ids by name."""
    end = now or datetime.now(UTC)
    ids: dict[str, int] = {}

    def at(minutes: float) -> datetime:
        return end - timedelta(minutes=minutes)

    def add(kind: str, payload: dict[str, Any], minutes: float, **where: Any) -> int:
        return store.append(kind, payload, now=at(minutes), **where)

    def open_(
        name: str,
        title: str,
        severity: Severity,
        minutes: float,
        *,
        source: str = "openobserve",
        host: str | None = "server-1",
        maintenance: str | None = None,
        triage: StageState = StageState.PENDING,
        investigation: StageState = StageState.PENDING,
    ) -> int:
        incident = store.open_incident(
            title=title,
            severity=severity,
            host=host,
            opened_at=at(minutes),
            maintenance=maintenance,
            triage=triage,
            investigation=investigation,
        )
        ids[name] = incident
        alert: dict[str, Any] = {
            "title": title,
            "severity": severity.value,
            "source": source,
            "labels": {},
        }
        store.attach_alert(
            incident, source=source, fingerprint=name, title=title, status=Status.FIRING
        )
        add("alert.firing", alert, minutes, source=source, fingerprint=name, incident_id=incident)
        add(
            "incident.opened",
            {"title": title, "severity": severity.value, "host": host, "maintenance": maintenance},
            minutes,
            incident_id=incident,
        )
        return incident

    def resolve(incident_id: int, name: str, title: str, source: str, minutes: float) -> None:
        found = store.incident(incident_id)
        assert isinstance(found, Incident)
        store.attach_alert(
            incident_id, source=source, fingerprint=name, title=title, status=Status.RESOLVED
        )
        add("alert.resolved", {"title": title}, minutes, source=source, incident_id=incident_id)
        store.save_incident(
            found.model_copy(
                update={
                    "status": IncidentStatus.RESOLVED,
                    "resolved_at": at(minutes),
                    "last_activity_at": at(minutes),
                }
            )
        )
        took = round((at(minutes) - found.opened_at).total_seconds() / 60, 1)
        add("incident.resolved", {"duration_minutes": took}, minutes, incident_id=incident_id)

    add(
        "change.recorded",
        {"kind": "backup", "title": "nightly backup", "host": "server-1"},
        200,
        source="change",
    )

    # Resolved yesterday evening: triage called it noise, it was not investigated, you agreed.
    late = open_("late", "backup_late", Severity.INFO, 190, maintenance="nightly backup")
    add("model.call", _model_call("triage", "claude-haiku-5-5", 0.0021), 189, incident_id=late)
    add(
        "triage.verdict",
        {
            "shadow": True,
            "verdict": "noise",
            "effective_verdict": "noise",
            "fail_open": None,
            "confidence": 0.91,
            "reason": "Fires during the backup window on most nights and recovers by itself.",
            "model": "claude-haiku-5-5",
            "attempts": 1,
            "prompt_version": "triage-1",
        },
        189,
        incident_id=late,
    )
    store.set_stage(late, "triage", StageState.DONE)
    add("investigation.skipped", {"skip": "triage: noise"}, 188, incident_id=late)
    store.set_stage(late, "investigation", StageState.SKIPPED)
    resolve(late, "late", "backup_late", "openobserve", 170)
    add("feedback.recorded", {"about": "triage", "vote": "up", "note": ""}, 100, incident_id=late)

    # Resolved: triage failed open, the investigation ran out of budget.
    stale = open_(
        "stale", "Source openobserve is stale", Severity.CRITICAL, 150, source="paeon", host=None
    )
    add(
        "model.call",
        _model_call("triage", "claude-haiku-5-5", 0.0019),
        149,
        incident_id=stale,
    )
    add(
        "triage.verdict",
        {
            "shadow": True,
            "verdict": "transient",
            "effective_verdict": "real",
            "fail_open": "low_confidence",
            "confidence": 0.55,
            "reason": "It may recover at the next poll.",
            "model": "claude-haiku-5-5",
            "attempts": 1,
            "prompt_version": "triage-1",
        },
        149,
        incident_id=stale,
    )
    store.set_stage(stale, "triage", StageState.DONE)
    add(
        "investigation.started",
        {"alias": "investigate", "tools": ["search_logs"]},
        148,
        incident_id=stale,
    )
    add(
        "model.call",
        _model_call("investigate", "claude-opus-5-5", 0.0, error="timeout after 300 s"),
        147,
        incident_id=stale,
    )
    add(
        "investigation.failed",
        {"reason": "model_error: timeout after 300 s", "tool_calls": 0, "duration_seconds": 300.0},
        143,
        incident_id=stale,
    )
    store.set_stage(stale, "investigation", StageState.FAILED)
    resolve(stale, "stale", "Source openobserve is stale", "paeon", 120)

    # Open and analysed: the whole chain, with a notification at the end.
    media = open_("media", "slo_traefik_5xx", Severity.WARNING, 64)
    add(
        "webhook.received",
        {
            "heartbeat": {"status": 0},
            "monitor": {"name": "media", "url": "https://media.example/?token=hunter2hunter2"},
        },
        63.5,
        source="uptime-kuma",
    )
    store.attach_alert(
        media,
        source="uptime-kuma",
        fingerprint="kuma-media",
        title="media is DOWN",
        status=Status.FIRING,
    )
    add(
        "alert.firing",
        {"title": "media is DOWN", "severity": "critical", "source": "uptime-kuma"},
        63.5,
        source="uptime-kuma",
        fingerprint="kuma-media",
        incident_id=media,
    )
    add(
        "incident.correlated",
        {
            "reason": "host",
            "alert": {
                "source": "uptime-kuma",
                "fingerprint": "kuma-media",
                "title": "media is DOWN",
            },
        },
        63.5,
        incident_id=media,
    )
    add("model.call", _model_call("triage", "claude-haiku-5-5", 0.0024), 63, incident_id=media)
    add(
        "triage.verdict",
        {
            "shadow": True,
            "verdict": "real",
            "effective_verdict": "real",
            "fail_open": None,
            "confidence": 0.86,
            "reason": "Two sources agree that media is failing; no recovery after a minute.",
            "model": "claude-haiku-5-5",
            "attempts": 1,
            "prompt_version": "triage-1",
        },
        63,
        incident_id=media,
    )
    store.set_stage(media, "triage", StageState.DONE)
    tools = ["search_logs", "inspect_container", "query_metrics", "list_changes", "submit_findings"]
    add("investigation.started", {"alias": "investigate", "tools": tools}, 62.6, incident_id=media)
    add(
        "model.call",
        _model_call("investigate", "claude-opus-5-5", 0.061, turn=1),
        62.4,
        incident_id=media,
    )
    for number, (minutes, name, args, result) in enumerate(
        (
            (
                62.3,
                "search_logs",
                {"stream": "traefik", "filter": "status >= 500"},
                {"rows": [{"status": 502, "host": "media.example"}]},
            ),
            (
                62.2,
                "inspect_container",
                {"name": "media-server"},
                {"state": "restarting", "exit_code": 1},
            ),
            (
                61.9,
                "query_metrics",
                {"query": "node_filesystem_avail_bytes{mountpoint='/srv'}"},
                {"value": 0},
            ),
        ),
        1,
    ):
        add(
            "tool.call",
            {
                "call": f"c{number}",
                "name": name,
                "input": args,
                "result": result,
                "is_error": False,
                "duration_ms": 240,
            },
            minutes,
            incident_id=media,
        )
    add(
        "model.call",
        _model_call("investigate", "claude-opus-5-5", 0.148, turn=2),
        60.4,
        incident_id=media,
    )
    add(
        "investigation.completed",
        {
            "findings": FINDINGS,
            "model": "claude-opus-5-5",
            "tool_calls": 3,
            "duration_seconds": 131.0,
        },
        60.3,
        incident_id=media,
    )
    store.set_stage(media, "investigation", StageState.DONE)
    add(
        "notification.sent",
        {"channel": "ntfy", "about": "investigation", "topic": "incidents"},
        60.2,
        incident_id=media,
    )

    # Open: never sent to a model, the investigation is running.
    vpn = open_(
        "vpn",
        "vpn_unhealthy",
        Severity.CRITICAL,
        9,
        source="uptime-kuma",
        host="vps-1",
        triage=StageState.SKIPPED,
    )
    add("triage.skipped", {"skip": "page_level", "effective_verdict": "real"}, 8.9, incident_id=vpn)
    add("investigation.started", {"alias": "investigate", "tools": tools}, 7.9, incident_id=vpn)
    add(
        "model.call",
        _model_call("investigate", "claude-opus-5-5", 0.058, turn=1),
        7.6,
        incident_id=vpn,
    )
    add(
        "tool.call",
        {
            "call": "c1",
            "name": "search_logs",
            "input": {"stream": "docker"},
            "result": "stream not allowed",
            "is_error": True,
            "duration_ms": 3,
        },
        7.5,
        incident_id=vpn,
    )

    # Just opened: waiting for triage, and flapping.
    load = open_("load", "node_load_high", Severity.WARNING, 0.6, investigation=StageState.OFF)
    found = store.incident(load)
    assert isinstance(found, Incident)
    store.save_incident(found.model_copy(update={"flapping": True}))
    add("incident.flapping", {"firings": 3, "window_minutes": 60}, 0.5, incident_id=load)

    add("digest.sent", {"date": at(0).date().isoformat(), "incidents": 2}, 0.2)
    return ids


POSTMORTEM = """\
---
id: 2026-11-17-worker-stalled
incident: {incident}
status: draft
severity: warning
detected_at: '2026-11-17T22:41:00+00:00'
autonomy: auto
---

# The job worker stopped taking jobs for nine minutes

> Draft written by Paeon from its event log. Review before relying on it.

## Summary

The worker stalled on a **dead VPN route**. The runbook restarted it and it recovered. See the
[runbook](https://git.example/ops/runbooks/worker.md), not [this](javascript:alert(1)).

## Timeline

| Time (UTC) | What happened |
|---|---|
| 22:41 | `worker_stalled` fired |
| 22:42 | Runbook `worker-stalled` ran `restart_container(worker)` |
| 22:50 | The alert resolved |

```
restart_container(worker): up and healthy after 8 s
```

<script>alert(1)</script>

```mermaid
sequenceDiagram
    participant A as Alertmanager
    participant P as Paeon
    participant E as Executor
    A->>P: worker_stalled firing
    P->>E: plan restart_container(worker)
    E-->>P: verified
    A->>P: resolved
```

## Action items

- [ ] (prevent) Add a liveness probe that checks the route.
- [x] (detect) Alert on the queue length.
"""


def populate_actions(store: Store, ids: dict[str, int], directory: Path | None = None) -> None:
    """The same evening once Paeon may act: a plan waiting for Go, a runbook that fixed a
    stall by itself, one that failed and was demoted, and a record long enough to earn `auto`.
    Written as the core mirrors them from the executor."""
    end = datetime.now(UTC)

    def add(kind: str, payload: dict[str, Any], minutes: float, incident_id: int | None) -> None:
        store.append(
            kind,
            payload,
            source="executor",
            incident_id=incident_id,
            now=end - timedelta(minutes=minutes),
        )

    restart_media = "restart_container(media-server)"
    media_tuple = ["slo_traefik_5xx", "restart_container", "media-server"]
    for days in (40, 31, 22, 15, 6):  # five approved, verified restarts in the last weeks
        add(
            "plan.completed",
            {"outcome": "verified", "level": "approve", "tuples": [media_tuple]},
            days * 24 * 60,
            None,
        )
    media = ids["media"]
    add(
        "plan.proposed",
        {
            "proposal": 11,
            "origin": "investigation",
            "runbook": None,
            "level": "approve",
            "held_back_by": [],
            "steps": [restart_media],
            "tuples": [media_tuple],
            "confidence": 0.78,
        },
        60.1,
        media,
    )
    expires = (end + timedelta(minutes=14)).isoformat()
    add("approval.requested", {"proposal": 11, "expires_at": expires}, 60.0, media)
    add(
        "notification.sent",
        {"channel": "ntfy", "about": "approval", "topic": "incidents"},
        60,
        media,
    )

    def incident(name: str, title: str, minutes: float) -> int:
        number = store.open_incident(
            title=title,
            severity=Severity.WARNING,
            host="server-1",
            opened_at=end - timedelta(minutes=minutes),
            maintenance=None,
            triage=StageState.SKIPPED,
            investigation=StageState.SKIPPED,
        )
        ids[name] = number
        store.attach_alert(
            number, source="uptime-kuma", fingerprint=name, title=title, status=Status.RESOLVED
        )
        store.append(
            "incident.opened",
            {"title": title, "severity": "warning", "host": "server-1"},
            incident_id=number,
            now=end - timedelta(minutes=minutes),
        )
        for stage in ("triage", "investigation"):
            store.append(
                f"{stage}.skipped",
                {"skip": "runbook"},
                incident_id=number,
                now=end - timedelta(minutes=minutes),
            )
        return number

    def close(number: int, minutes: float) -> None:
        found = store.incident(number)
        assert isinstance(found, Incident)
        when = end - timedelta(minutes=minutes)
        store.save_incident(
            found.model_copy(
                update={
                    "status": IncidentStatus.RESOLVED,
                    "resolved_at": when,
                    "last_activity_at": when,
                }
            )
        )

    # A runbook that worked without asking.
    worker = incident("worker", "worker_stalled", 1400)
    worker_tuple = ["worker_stalled", "restart_container", "worker"]
    steps = ["restart_container(worker)", "restart_container(vpn)"]
    add("runbook.matched", {"runbook": "worker-stalled", "steps": steps}, 1400, worker)
    add(
        "plan.proposed",
        {
            "proposal": 9,
            "origin": "runbook",
            "runbook": "worker-stalled",
            "level": "auto",
            "held_back_by": [],
            "steps": steps,
            "tuples": [worker_tuple],
        },
        1399,
        worker,
    )
    add("action.started", {"proposal": 9, "step": steps[0]}, 1399, worker)
    add("action.verified", {"proposal": 9, "step": steps[0]}, 1398, worker)
    add(
        "plan.completed",
        {
            "proposal": 9,
            "outcome": "verified",
            "level": "auto",
            "origin": "runbook",
            "steps": steps[:1],
            "tuples": [worker_tuple],
        },
        1398,
        worker,
    )
    add(
        "notification.sent",
        {"channel": "ntfy", "about": "result", "topic": "incidents"},
        1398,
        worker,
    )
    close(worker, 1391)
    store.set_stage(worker, "postmortem", StageState.DONE)
    store.append(
        "postmortem.drafted",
        {"path": "2026-11-17-worker-stalled.md", "narrative": True},
        incident_id=worker,
        now=end - timedelta(minutes=1300),
    )

    # One that did not work, and lost its `auto`.
    queue = incident("queue", "queue_backlog", 2900)
    queue_tuple = ["queue_backlog", "restart_container", "queue"]
    add(
        "plan.proposed",
        {
            "proposal": 6,
            "origin": "runbook",
            "runbook": "queue-backlog",
            "level": "auto",
            "held_back_by": [],
            "steps": ["restart_container(queue)"],
            "tuples": [queue_tuple],
        },
        2899,
        queue,
    )
    add(
        "action.failed",
        {
            "proposal": 6,
            "step": "restart_container(queue)",
            "reason": "its health check says unhealthy",
        },
        2897,
        queue,
    )
    add(
        "plan.completed",
        {
            "proposal": 6,
            "outcome": "failed",
            "level": "auto",
            "origin": "runbook",
            "steps": ["restart_container(queue)"],
            "tuples": [queue_tuple],
            "reason": "its health check says unhealthy",
        },
        2897,
        queue,
    )
    demoted = {"alert": "queue_backlog", "action": "restart_container", "target": "queue"}
    add("autonomy.demoted", {**demoted, "reason": "a verification failed"}, 2897, queue)
    close(queue, 2700)

    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "2026-11-17-worker-stalled.md").write_text(POSTMORTEM.format(incident=worker))


ACTING_RULES: dict[str, Any] = {
    "actions": {
        "enabled": True,
        "catalog": {
            "restart_container": {
                "targets": ["worker", "vpn", "queue", "media-*"],
                "deny": ["media-db"],
            }
        },
        "policies": [
            {"alert": "worker_stalled", "action": "restart_container", "level": "auto"},
            {"alert": "queue_backlog", "action": "restart_container", "level": "auto"},
            {"alert": "slo_.*", "level": "approve"},
        ],
        "runbooks": [
            {
                "name": "worker-stalled",
                "match": {"title": "worker_stalled"},
                "steps": [
                    {"action": "restart_container", "target": "worker"},
                    {"action": "restart_container", "target": "vpn"},
                ],
            }
        ],
    }
}


if __name__ == "__main__":  # pragma: no cover
    demo = Store(sys.argv[1])
    populate_actions(demo, populate(demo), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
