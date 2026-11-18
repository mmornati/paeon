"""Traces: the shape of each step leaves, its content does not."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from paeon import telemetry
from paeon.app import create_app
from paeon.config import load
from paeon.rules import Telemetry
from paeon.store import Store
from support import T0

CONFIG = Telemetry(
    otlp_endpoint="http://openobserve:5080/api/default/",
    headers={"stream-name": "paeon"},
    service_name="paeon-test",
)


def record(store: Store) -> None:
    def add(kind: str, payload: dict[str, Any], minute: float, incident: int | None = 1) -> None:
        store.append(kind, payload, incident_id=incident, now=T0 + timedelta(minutes=minute))

    add("webhook.received", {"monitor": {"url": "https://x/?token=hunter2hunter2"}}, 0, None)
    add(
        "alert.firing",
        {"title": "API down password=hunter2pass", "severity": "critical", "raw": {"a": 1}},
        0,
    )
    add(
        "model.call",
        {
            "stage": "investigate",
            "model": "claude-opus-5-5",
            "input_tokens": 1200,
            "output_tokens": 300,
            "cost": 0.0108,
            "duration_ms": 4000,
            "input": {"alerts": ["the whole context pack"]},
            "output": "the model's words",
        },
        1,
    )
    add(
        "tool.call",
        {
            "call": "c1",
            "name": "container_logs",
            "input": {"name": "api"},
            "result": {"lines": ["a secret log line"]},
            "is_error": True,
            "duration_ms": 250,
        },
        2,
    )
    add(
        "investigation.completed",
        {
            "findings": {"summary": "private", "root_cause": {"statement": "x", "confidence": 0.8}},
            "tool_calls": 1,
            "duration_seconds": 90.0,
        },
        3,
    )
    add("action.failed", {"step": "restart_container(api)", "reason": "it is not running"}, 4)
    add("incident.resolved", {"duration_minutes": 12.0}, 12)
    add("change.recorded", {"kind": "deploy", "title": "api"}, 13, None)


class Collector:
    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.status = 200
        self.error: Exception | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.error is not None:
            raise self.error
        assert request.url.path == "/api/default/v1/traces"
        self.headers.append(dict(request.headers))
        self.bodies.append(json.loads(request.content))
        return httpx.Response(self.status, json={"partialSuccess": {}})

    def spans(self, body: int = 0) -> list[dict[str, Any]]:
        spans: list[dict[str, Any]] = self.bodies[body]["resourceSpans"][0]["scopeSpans"][0][
            "spans"
        ]
        return spans


def attributes(span: dict[str, Any]) -> dict[str, Any]:
    return {a["key"]: next(iter(a["value"].values())) for a in span["attributes"]}


def test_an_incident_is_one_trace_of_spans_without_their_content(store: Store) -> None:
    record(store)
    collector = Collector()
    exporter = telemetry.Exporter(
        store, CONFIG, "Basic dXNlcjpwYXNz", transport=httpx.MockTransport(collector.handle)
    )
    try:
        assert exporter.export() == 6  # no webhook, no event outside an incident
        assert exporter.export() == 0  # the cursor moved
    finally:
        exporter.close()
    assert collector.headers[0]["authorization"] == "Basic dXNlcjpwYXNz"
    assert collector.headers[0]["stream-name"] == "paeon"
    resource = collector.bodies[0]["resourceSpans"][0]["resource"]["attributes"]
    assert {"key": "service.name", "value": {"stringValue": "paeon-test"}} in resource

    spans = {span["name"]: span for span in collector.spans()}
    assert set(spans) == {
        "alert.firing",
        "model investigate",
        "tool container_logs",
        "investigation.completed",
        "action.failed",
        "incident",
    }
    assert len({span["traceId"] for span in spans.values()}) == 1
    root = spans["incident"]
    assert "parentSpanId" not in root
    assert all(s["parentSpanId"] == root["spanId"] for n, s in spans.items() if n != "incident")
    minutes = (int(root["endTimeUnixNano"]) - int(root["startTimeUnixNano"])) / 60e9
    assert minutes == 12

    model = attributes(spans["model investigate"])
    assert model["paeon.model"] == "claude-opus-5-5"
    assert (model["paeon.input_tokens"], model["paeon.cost"]) == ("1200", 0.0108)
    call = spans["model investigate"]
    assert int(call["endTimeUnixNano"]) - int(call["startTimeUnixNano"]) == 4_000_000_000
    tool = spans["tool container_logs"]
    assert attributes(tool)["paeon.is_error"] is True
    assert tool["status"]["code"] == 2
    assert spans["action.failed"]["status"]["code"] == 2
    assert attributes(spans["action.failed"])["paeon.reason"] == "it is not running"
    assert attributes(spans["investigation.completed"])["paeon.confidence"] == 0.8
    assert spans["alert.firing"]["status"]["code"] == 1

    # Facts leave; what was read, asked or answered does not, and a title is scrubbed.
    sent = json.dumps(collector.bodies)
    for private in ("context pack", "model's words", "secret log line", "hunter2", "private"):
        assert private not in sent
    assert "API down password=[REDACTED]" in sent


def test_a_failed_export_is_tried_again_and_says_nothing_of_the_request(store: Store) -> None:
    record(store)
    collector = Collector()
    exporter = telemetry.Exporter(
        store, CONFIG, "Basic c2VjcmV0", transport=httpx.MockTransport(collector.handle)
    )
    try:
        collector.status = 401
        with pytest.raises(RuntimeError, match=r"^trace export answered HTTP 401$"):
            exporter.export()
        collector.error = httpx.ConnectError("http://openobserve:5080 Basic c2VjcmV0")
        with pytest.raises(RuntimeError, match=r"^trace export failed: ConnectError$"):
            exporter.export()
        collector.error, collector.status = None, 200
        assert exporter.export() == 6  # nothing was skipped by the failures
    finally:
        exporter.close()

    quiet = Store(":memory:")
    try:
        quiet.append("change.recorded", {"kind": "deploy"})
        idle = telemetry.Exporter(quiet, CONFIG, transport=httpx.MockTransport(collector.handle))
        before = len(collector.bodies)
        assert idle.export() == 0  # nothing of an incident: no request, and the cursor moves
        assert len(collector.bodies) == before
        assert quiet.cursor(telemetry.CURSOR) == "1"
        idle.close()
    finally:
        quiet.close()


def test_the_exporter_is_built_from_the_rules(store: Store, tmp_path: Any) -> None:
    rules = tmp_path / "paeon.yaml"
    rules.write_text(
        "telemetry:\n  otlp_endpoint: http://openobserve:5080/api/default\n"
        "  authorization_env: OTLP_AUTH\n"
    )
    env = {"PAEON_INGEST_TOKEN": "t" * 32, "PAEON_CONFIG": str(rules), "OTLP_AUTH": "Basic abc"}
    settings = load(env)
    assert settings.telemetry_authorization == "Basic abc"
    app = create_app(settings, store)
    with TestClient(app):
        assert app.state.exporter is not None
    assert create_app(load({"PAEON_INGEST_TOKEN": "t" * 32}), store).state.exporter is None
    assert telemetry.span_of(next(iter(store.events()), None) or _outside()) is None


def _outside() -> Any:
    from paeon.store import Event

    return Event(1, T0, "digest.sent", None, None, {})
