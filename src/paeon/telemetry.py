"""Traces of Paeon's own work, exported over OTLP/HTTP: one trace per incident.

The responder gets a dashboard of its own in whatever receives OpenTelemetry (OpenObserve does):
how long triage and each investigation took, which tools were called, what each model call
cost, what the executor did. Spans are built from the event log, after the fact, by a cursor:
an export that fails is tried again, and nothing in the pipeline waits for it.

A span carries facts about a step (its kind, duration, model, token counts, cost, outcome),
never its content: no prompt, no model answer, no tool input or result, no alert payload. The
event log holds excerpts of the server's logs and is never exported whole.

The format is OTLP's JSON encoding, written by hand: a few dozen lines, against one more
dependency and its exporter threads.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

import httpx2 as httpx

from paeon import __version__
from paeon.redact import redact_text
from paeon.rules import Telemetry
from paeon.store import Event, Store

CURSOR = "telemetry"
BATCH = 500
_OK, _ERROR = 1, 2
# Scalar facts that may leave with a span, by event payload key. Anything else stays behind.
_FACTS = (
    "stage", "alias", "backend", "model", "model_requested", "input_tokens", "output_tokens",
    "cached_tokens", "cost", "prompt_version", "verdict", "effective_verdict", "fail_open",
    "confidence", "skip", "tool_calls", "level", "outcome", "origin", "runbook", "step", "name",
    "call", "is_error", "about", "vote", "channel", "severity", "source", "host", "kind",
    "duration_minutes", "narrative", "pushed", "proposal",
)  # fmt: skip
# Free text a person or a program wrote: kept short and scrubbed of secret-shaped strings.
_TEXTS = ("title", "reason", "error")
_SKIPPED = frozenset({"webhook.received"})
_FAILED = frozenset(
    {"action.failed", "investigation.failed", "notification.failed", "postmortem.failed",
     "plan.refused", "change.failed", "runbook.failed", "runbook.not_cleared"}
)  # fmt: skip


def trace_id(incident_id: int) -> str:
    return hashlib.sha256(f"paeon-incident-{incident_id}".encode()).hexdigest()[:32]


def span_id(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()[:16]


def _nanos(moment: datetime) -> str:
    return str(int(moment.timestamp() * 1_000_000_000))


def _attribute(key: str, value: Any) -> dict[str, Any] | None:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    if isinstance(value, str) and value:
        return {"key": key, "value": {"stringValue": value}}
    return None


def _duration(event: Event) -> timedelta:
    payload = event.payload
    if isinstance(payload.get("duration_ms"), int | float):
        return timedelta(milliseconds=float(payload["duration_ms"]))
    if isinstance(payload.get("duration_seconds"), int | float):
        return timedelta(seconds=float(payload["duration_seconds"]))
    return timedelta(0)


def _name(event: Event) -> str:
    payload = event.payload
    if event.kind == "model.call":
        return f"model {payload.get('stage') or 'call'}"
    if event.kind == "tool.call":
        return f"tool {payload.get('name') or '?'}"
    return event.kind


def span_of(event: Event) -> dict[str, Any] | None:
    """One event as a span of its incident's trace, or None for what is not exported."""
    if event.incident_id is None or event.kind in _SKIPPED:
        return None
    payload = event.payload
    attributes = [{"key": "paeon.event.kind", "value": {"stringValue": event.kind}}]
    attributes.append({"key": "paeon.incident.id", "value": {"intValue": str(event.incident_id)}})
    for key in _FACTS:
        found = _attribute(f"paeon.{key}", payload.get(key))
        if found is not None:
            attributes.append(found)
    for key in _TEXTS:
        text = payload.get(key)
        if isinstance(text, str) and text:
            attributes.append(
                {"key": f"paeon.{key}", "value": {"stringValue": redact_text(text, max_length=200)}}
            )
    if event.kind == "investigation.completed":
        cause = (payload.get("findings") or {}).get("root_cause") or {}
        found = _attribute("paeon.confidence", cause.get("confidence"))
        if found is not None:
            attributes.append(found)
    failed = bool(payload.get("error")) or payload.get("is_error") is True
    failed = failed or event.kind in _FAILED or payload.get("outcome") == "failed"
    end = event.recorded_at
    root = event.kind == "incident.resolved"
    start = end - _duration(event)
    if root and isinstance(payload.get("duration_minutes"), int | float):
        # The incident itself: the span every other span of the trace hangs from.
        start = end - timedelta(minutes=float(payload["duration_minutes"]))
    span: dict[str, Any] = {
        "traceId": trace_id(event.incident_id),
        "spanId": span_id(f"incident-{event.incident_id}" if root else f"event-{event.id}"),
        "name": "incident" if root else _name(event),
        "kind": 1,
        "startTimeUnixNano": _nanos(start),
        "endTimeUnixNano": _nanos(end),
        "attributes": attributes,
        "status": {"code": _ERROR if failed else _OK},
    }
    if not root:
        span["parentSpanId"] = span_id(f"incident-{event.incident_id}")
    return span


def envelope(spans: list[dict[str, Any]], service: str) -> dict[str, Any]:
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": service}},
                        {"key": "service.version", "value": {"stringValue": __version__}},
                    ]
                },
                "scopeSpans": [
                    {"scope": {"name": "paeon", "version": __version__}, "spans": spans}
                ],
            }
        ]
    }


class Exporter:
    def __init__(
        self,
        store: Store,
        config: Telemetry,
        authorization: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._store = store
        self._config = config
        headers = dict(config.headers)
        if authorization:
            headers["Authorization"] = authorization
        self._http = httpx.Client(
            base_url=config.otlp_endpoint.rstrip("/"),
            headers=headers,
            timeout=20,
            transport=transport,
        )

    def export(self) -> int:
        """Send the spans of the events recorded since the last export. Returns how many."""
        cursor = int(self._store.cursor(CURSOR) or 0)
        events = list(self._store.events(after_id=cursor, limit=BATCH))
        if not events:
            return 0
        spans = [span for span in map(span_of, events) if span is not None]
        if spans:
            # Errors are reduced to their kind or status: the request carries a credential.
            try:
                response = self._http.post(
                    "/v1/traces", json=envelope(spans, self._config.service_name)
                )
            except httpx.HTTPError as exc:
                raise RuntimeError(f"trace export failed: {type(exc).__name__}") from None
            if response.status_code >= 400:
                raise RuntimeError(f"trace export answered HTTP {response.status_code}")
        self._store.set_cursor(CURSOR, str(events[-1].id))
        return len(spans)

    def close(self) -> None:
        self._http.close()
