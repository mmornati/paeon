"""Sources: each adapter against the payloads and answers its service documents.

None of these services runs here. A `Service` answers by path with what the documentation says
the real one answers, and keeps the requests, so a test can say what was asked and how.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from paeon import sources
from paeon.app import create_app
from paeon.cli import main
from paeon.config import ConfigError, Settings, load
from paeon.correlation import Correlator
from paeon.recorder import Recorder
from paeon.rules import Rules, RulesError, parse_rules
from paeon.schema import Severity, Status
from paeon.sources import Connected, ReadError
from paeon.sources.http import Reader
from paeon.store import Store
from paeon.tools import Toolbox
from paeon.ui import views
from support import T0

TOKEN = "t" * 32


class Service:
    """A double of a service: `answers` by path, a value or a function of the request."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.get(request.url.path)
        if callable(answer):
            answer = answer(request)
        if isinstance(answer, httpx.Response):
            return answer
        if answer is None:
            return httpx.Response(404, json={"message": "not found"})
        if isinstance(answer, str):
            return httpx.Response(200, text=answer)
        return httpx.Response(200, json=answer)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def connect(
    document: dict[str, Any],
    service: Service | None = None,
    secrets: dict[str, str] | None = None,
    name: str = "s",
) -> Connected:
    rules = parse_rules({"sources": {name: document}})
    transport = None if service is None else service.transport
    return sources.build(name, rules.sources[name], secrets or {}, transport=transport)


def call(source: Connected, tool: str, **args: Any) -> Any:
    """Run a tool the way an investigation does. An error comes back as `{"error": ...}`."""
    result, _ = Toolbox(source.tools).call(tool, args)
    return result


def names(source: Connected) -> list[str]:
    return [tool.spec.name for tool in source.tools]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "paeon.sqlite")
    yield s
    s.close()


# --- the rules file -------------------------------------------------------------------------

PROM = {"type": "prometheus", "url": "http://prometheus:9090", "metrics": ["node_*"]}


@pytest.mark.parametrize(
    "document",
    [
        {"Prom": PROM},  # not a name
        {"change": {"type": "gatus"}},  # the address of the change calendar
        {"paeon": {"type": "gatus"}},  # Paeon's own source
        {"alertmanager": {"type": "gatus"}},  # a name that keeps its type
        {"a-b": {"type": "gatus"}, "a_b": {"type": "gatus"}},  # the same tool names
        {"p": {"type": "pigeon"}},  # not a service
        {"p": {**PROM, "password": "x"}},  # not a key
        {"p": {"type": "prometheus"}},  # nothing to read
        {"p": {**PROM, "url": "http://paeon:hunter2@prometheus:9090"}},  # a secret in the file
        {"p": {**PROM, "user": "u"}},  # a user without a password
        {"p": {**PROM, "user": "u", "password_env": "P", "token_env": "T"}},  # two ways in
        {"l": {"type": "loki", "url": "http://loki:3100", "streams": {"api": "job=api"}}},
        {"l": {"type": "loki", "url": "http://loki:3100", "streams": {"a b": "{job=1}"}}},
        {"v": {"type": "victorialogs", "url": "http://v:9428", "streams": {"a": "x | stats"}}},
        {"o": {"type": "openobserve", "url": "http://oo:5080"}},  # no account
        {"b": {"type": "beszel", "url": "http://beszel:8090"}},  # no account
        {"b": {"type": "beszel", "url": "http://b", "token_env": "T"}},  # it has no tokens
    ],
)
def test_sources_that_cannot_be_are_refused(document: dict[str, Any]) -> None:
    with pytest.raises(RulesError):
        parse_rules({"sources": document})


def test_a_source_reads_its_secrets_and_a_missing_one_stops_the_start() -> None:
    env = {"PAEON_INGEST_TOKEN": TOKEN, "PAEON_CONFIG": ""}
    rules = parse_rules({"sources": {"prom": {**PROM, "token_env": "T_PROM"}}})
    from paeon.config import source_secrets

    assert source_secrets({**env, "T_PROM": "tk"}, rules, "prom") == {"token": "tk"}
    with pytest.raises(ConfigError, match=r"sources\.prom: T_PROM"):
        source_secrets(env, rules, "prom")


def test_openobserve_is_configured_in_one_place(tmp_path: Path) -> None:
    path = tmp_path / "paeon.yaml"
    path.write_text(
        "sources:\n  openobserve:\n    type: openobserve\n    url: http://oo:5080\n"
        "    user: u\n    password_env: OO_PASSWORD\n"
    )
    env = {"PAEON_INGEST_TOKEN": TOKEN, "PAEON_CONFIG": str(path), "OO_PASSWORD": "pw"}
    assert load(env).source_secrets == {"openobserve": {"password": "pw"}}
    twice = {
        **env,
        "PAEON_OPENOBSERVE_URL": "http://oo:5080",
        "PAEON_OPENOBSERVE_USER": "u",
        "PAEON_OPENOBSERVE_PASSWORD": "pw",
    }
    with pytest.raises(ConfigError, match="configured twice"):
        load(twice)


# --- the reader -----------------------------------------------------------------------------


def test_the_reader_asks_only_its_paths_and_never_says_a_url() -> None:
    service = Service(
        {"/prefix/api/v1/query": {"ok": True}, "/prefix/api/v1/rules": httpx.Response(503)}
    )
    http = Reader(
        "prometheus",
        "http://prometheus:9090/prefix/",
        paths=("/api/v1/query", "/api/v1/rules"),
        token="tk",
        transport=service.transport,
    )
    assert http.json("/api/v1/query", query="up", unset=None) == {"ok": True}
    assert service.last.url.path == "/prefix/api/v1/query"
    assert dict(service.last.url.params) == {"query": "up"}
    assert service.last.headers["authorization"] == "Bearer tk"
    for path in ("/api/v1/admin/tsdb/delete_series", "/api/v1/query_range", "/-/reload"):
        with pytest.raises(ReadError, match="not a path this source reads"):
            http.get(path)
    assert len(service.requests) == 1  # a path that is not listed is not asked at all
    with pytest.raises(ReadError) as refused:
        http.get("/api/v1/rules")
    assert str(refused.value) == "prometheus answered HTTP 503"

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to http://user:hunter2@prometheus:9090")

    broken = Reader("prometheus", "http://p", paths=("/api",), transport=httpx.MockTransport(down))
    with pytest.raises(ReadError) as unreachable:
        broken.get("/api")
    assert str(unreachable.value) == "prometheus is unreachable: ConnectError"


# --- Prometheus -----------------------------------------------------------------------------

RULES = {
    "status": "success",
    "data": {
        "groups": [
            {
                "name": "node",
                "file": "/etc/prometheus/alerts.yml",
                "rules": [
                    {
                        "state": "firing",
                        "name": "DiskFull",
                        "query": "node_filesystem_avail_bytes / node_filesystem_size_bytes < 0.1",
                        "duration": 300,
                        "labels": {"severity": "critical"},
                        "annotations": {"summary": "Disk almost full"},
                        "alerts": [
                            {
                                "labels": {"alertname": "DiskFull", "instance": "server-1"},
                                "state": "firing",
                                "activeAt": "2026-11-18T07:55:00Z",
                                "value": "5e-02",
                            }
                        ],
                        "health": "ok",
                        "lastEvaluation": "2026-11-18T08:00:00Z",
                        "type": "alerting",
                    },
                    {"name": "HighLoad", "state": "inactive", "health": "ok", "type": "alerting"},
                    {"name": "job:up:sum", "query": "sum(up)", "type": "recording"},
                ],
            }
        ]
    },
}
TARGETS = {
    "status": "success",
    "data": {
        "activeTargets": [
            {
                "labels": {"instance": "server-1:9100", "job": "node"},
                "scrapePool": "node",
                "scrapeUrl": "http://admin:hunter2@server-1:9100/metrics",
                "lastError": "",
                "lastScrape": "2026-11-18T08:00:00Z",
                "lastScrapeDuration": 0.02,
                "health": "up",
            },
            {
                "labels": {"instance": "server-2:9100", "job": "node"},
                "scrapeUrl": "http://server-2:9100/metrics",
                "lastError": "connection refused",
                "health": "down",
            },
        ],
        "droppedTargets": [],
    },
}
MATRIX = {"status": "success", "data": {"resultType": "matrix", "result": []}}


def test_prometheus_reads_metrics_on_the_allow_list_only() -> None:
    service = Service({"/api/v1/query_range": MATRIX, "/api/v1/query": MATRIX})
    prom = connect({**PROM, "headers": {"X-Scope-OrgID": "home"}}, service, name="prom")
    assert names(prom) == ["query_metrics_prom", "alert_rules_prom", "scrape_targets_prom"]
    assert (prom.alerts, prom.parse, prom.polling) == ("none", None, None)

    answer = call(prom, "query_metrics_prom", promql="rate(node_cpu_seconds_total[5m])")
    assert answer == MATRIX["data"]
    asked = service.last
    assert (asked.method, asked.url.path) == ("GET", "/api/v1/query_range")
    assert asked.url.params["query"] == "rate(node_cpu_seconds_total[5m])"
    assert int(asked.url.params["end"]) - int(asked.url.params["start"]) == 3600
    assert asked.headers["x-scope-orgid"] == "home"

    refused = call(prom, "query_metrics_prom", promql="container_memory_usage_bytes")
    assert "not readable: ['container_memory_usage_bytes']" in refused["error"]
    assert len(service.requests) == 1

    # A week at the smallest step would be more points than Prometheus accepts.
    call(prom, "query_metrics_prom", promql="node_load1", since_minutes=10080, step_seconds=15)
    params = service.last.url.params
    assert (int(params["end"]) - int(params["start"])) / int(params["step"]) <= 11000

    assert prom.probe is not None
    assert prom.probe() == "answered a query"


def test_prometheus_rules_and_targets_without_what_must_not_leave() -> None:
    service = Service({"/api/v1/rules": RULES, "/api/v1/targets": TARGETS})
    prom = connect(PROM, service, name="prom")
    listed = call(prom, "alert_rules_prom")
    assert [rule["name"] for rule in listed["rules"]] == ["DiskFull", "HighLoad"]  # no recording
    disk = listed["rules"][0]
    assert (disk["state"], disk["for_seconds"], disk["group"]) == ("firing", 300, "node")
    assert disk["alerts"][0]["labels"]["instance"] == "server-1"
    assert service.last.url.params["type"] == "alert"
    assert call(prom, "alert_rules_prom", firing_only=True)["count"] == 1
    assert call(prom, "alert_rules_prom", text="load")["rules"][0]["name"] == "HighLoad"

    targets = call(prom, "scrape_targets_prom")
    assert "hunter2" not in json.dumps(targets)
    assert "scrapeUrl" not in json.dumps(targets)
    down = call(prom, "scrape_targets_prom", down_only=True)["targets"]
    assert [(t["instance"], t["health"], t["last_error"]) for t in down] == [
        ("server-2:9100", "down", "connection refused")
    ]


def test_prometheus_offers_only_what_the_rules_allow_and_says_a_refusal() -> None:
    service = Service({"/api/v1/rules": {"status": "error", "error": "query timed out"}})
    prom = connect(
        {"type": "prometheus", "url": "http://vm:8428", "targets": False}, service, name="vm"
    )
    assert names(prom) == ["alert_rules_vm"]  # no metric pattern, no query tool
    assert call(prom, "alert_rules_vm") == {
        "error": "prometheus refused the request: query timed out"
    }


# --- Alertmanager ---------------------------------------------------------------------------

AM_WEBHOOK = {
    "version": "4",
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "DiskFull", "severity": "critical", "instance": "server-1"},
            "annotations": {"summary": "/ is 95 % full"},
            "startsAt": "2026-11-18T07:55:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://prometheus:9090/graph?g0.expr=x",
            "fingerprint": "c1a5f0d2b7e94a33",
        }
    ],
}


def test_alertmanager_says_what_is_silenced() -> None:
    held = [
        {
            "fingerprint": "c1a5f0d2b7e94a33",
            "labels": {"alertname": "DiskFull", "instance": "server-1"},
            "annotations": {"summary": "/ is 95 % full"},
            "startsAt": "2026-11-18T07:55:00Z",
            "endsAt": "2026-11-18T08:05:00Z",
            "updatedAt": "2026-11-18T08:00:00Z",
            "generatorURL": "http://prometheus:9090/graph",
            "receivers": [{"name": "paeon"}],
            "status": {"state": "suppressed", "silencedBy": ["5d3f"], "inhibitedBy": []},
        }
    ]
    silences = [
        {
            "id": "5d3f",
            "status": {"state": "active"},
            "matchers": [
                {"name": "instance", "value": "server-1", "isRegex": False, "isEqual": True}
            ],
            "startsAt": "2026-11-18T07:00:00Z",
            "endsAt": "2026-11-18T09:00:00Z",
            "createdBy": "marco",
            "comment": "replacing the disk",
        },
        {"id": "old", "status": {"state": "expired"}, "matchers": []},
    ]
    service = Service(
        {
            "/api/v2/alerts": held,
            "/api/v2/silences": silences,
            "/api/v2/status": {"versionInfo": {"version": "0.28.1"}},
        }
    )
    am = connect(
        {"type": "alertmanager", "url": "http://am:9093", "user": "paeon", "password_env": "P"},
        service,
        {"password": "pw"},
        name="am",
    )
    assert names(am) == ["active_alerts_am", "silences_am"]
    (alert,) = call(am, "active_alerts_am")["alerts"]
    assert (alert["state"], alert["silenced_by"], alert["receivers"]) == (
        "suppressed",
        ["5d3f"],
        ["paeon"],
    )
    assert service.last.headers["authorization"].startswith("Basic ")
    assert call(am, "active_alerts_am", text="server-9")["count"] == 0
    (silence,) = call(am, "silences_am")["silences"]
    assert (silence["created_by"], silence["comment"]) == ("marco", "replacing the disk")
    assert silence["matchers"] == [
        {"name": "instance", "value": "server-1", "regex": False, "equal": True}
    ]
    assert call(am, "silences_am", expired=True)["count"] == 2
    assert am.probe is not None
    assert am.probe() == "Alertmanager 0.28.1"

    # Its webhook is the one Paeon has always read, under the source's own name.
    assert am.parse is not None
    (received,) = am.parse(AM_WEBHOOK, T0)
    assert (received.source, received.fingerprint, received.severity) == (
        "am",
        "c1a5f0d2b7e94a33",
        Severity.CRITICAL,
    )


# --- Grafana --------------------------------------------------------------------------------

GRAFANA_WEBHOOK = {
    "receiver": "paeon",
    "status": "firing",
    "orgId": 1,
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "High memory usage", "team": "blue", "zone": "us-1"},
            "annotations": {"description": "The system has high memory usage"},
            "startsAt": "2026-11-18T07:55:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "",
            "fingerprint": "ada6f8f3b3c5e1a0",
            "silenceURL": "https://grafana.example.org/alerting/silence/new",
            "dashboardURL": "https://grafana.example.org/d/abc",
            "panelURL": "https://grafana.example.org/d/abc?viewPanel=2",
            "values": {"B": 44.23943737541908, "C": 1},
        },
        {
            "status": "resolved",
            "labels": {"alertname": "High CPU usage"},
            "annotations": {},
            "startsAt": "2026-11-18T07:00:00Z",
            "endsAt": "2026-11-18T07:30:00Z",
            "generatorURL": "https://grafana.example.org/alerting/1afz29v7z/edit",
            "fingerprint": "b6a1a1d7e2f04c55",
            "values": None,
        },
        {"status": "firing", "labels": {"alertname": "no fingerprint"}},
    ],
    "version": "1",
    "title": "[FIRING:1] High memory usage",
    "state": "alerting",
    "message": "...",
}


def test_grafana_webhook_keeps_the_values_that_fired() -> None:
    grafana = connect({"type": "grafana"}, name="grafana")
    assert (grafana.alerts, grafana.tools, grafana.probe) == ("webhook", [], None)
    assert grafana.parse is not None
    firing, resolved = grafana.parse(GRAFANA_WEBHOOK, T0)
    assert (firing.status, firing.title, firing.fingerprint) == (
        Status.FIRING,
        "High memory usage",
        "ada6f8f3b3c5e1a0",
    )
    assert firing.description == (
        "The system has high memory usage\nValues: B=44.23943737541908, C=1"
    )
    assert firing.source_url == "https://grafana.example.org/d/abc?viewPanel=2"
    assert (resolved.status, resolved.source_url) == (
        Status.RESOLVED,
        "https://grafana.example.org/alerting/1afz29v7z/edit",
    )
    assert resolved.observed_at == datetime(2026, 11, 18, 7, 30, tzinfo=UTC)
    assert grafana.parse({"alerts": "none"}, T0) == []


def test_grafana_reads_annotations_and_rules_with_a_service_account() -> None:
    annotation = {
        "id": 1124,
        "alertId": 0,
        "alertName": "High memory usage",
        "dashboardUID": "abc",
        "newState": "Alerting",
        "prevState": "Normal",
        "time": 1794988500000,
        "timeEnd": 1794988500000,
        "text": "High memory usage {zone=us-1} - B=44.2",
        "tags": [],
        "data": {},
    }
    rule = {
        "id": 1,
        "uid": "1afz29v7z",
        "title": "High memory usage",
        "folderUID": "nErXDvCkzz",
        "ruleGroup": "node",
        "condition": "C",
        "for": "5m",
        "noDataState": "NoData",
        "execErrState": "Alerting",
        "isPaused": False,
        "labels": {"team": "blue"},
        "annotations": {"description": "The system has high memory usage"},
        "data": [
            {"refId": "A", "datasourceUid": "prom", "model": {"expr": "node_memory_used / 1e9"}},
            {"refId": "C", "datasourceUid": "__expr__", "model": {"expression": "$A > 40"}},
            {"refId": "D", "model": {"hide": False}},
        ],
    }
    service = Service(
        {
            "/api/annotations": [annotation],
            "/api/v1/provisioning/alert-rules": [rule],
            "/api/health": {"database": "ok", "version": "12.3.0"},
        }
    )
    grafana = connect(
        {"type": "grafana", "url": "https://grafana.example.org", "token_env": "T"},
        service,
        {"token": "glsa_x"},
        name="grafana",
    )
    assert names(grafana) == ["annotations_grafana", "alert_rules_grafana"]
    (found,) = call(grafana, "annotations_grafana", alerts_only=True)["annotations"]
    assert (found["alert"], found["new_state"], found["previous_state"]) == (
        "High memory usage",
        "Alerting",
        "Normal",
    )
    assert found["time"] == datetime.fromtimestamp(1794988500, UTC).isoformat()
    asked = service.last
    assert asked.headers["authorization"] == "Bearer glsa_x"
    assert asked.url.params["type"] == "alert"
    assert int(asked.url.params["to"]) - int(asked.url.params["from"]) == 24 * 3600 * 1000
    call(grafana, "annotations_grafana")
    assert "type" not in service.last.url.params

    (read,) = call(grafana, "alert_rules_grafana")["rules"]
    assert read["queries"] == [
        {"ref": "A", "query": "node_memory_used / 1e9"},
        {"ref": "C", "query": "$A > 40"},
    ]
    assert (read["for"], read["paused"], read["group"]) == ("5m", False, "node")
    assert grafana.probe is not None
    assert grafana.probe() == "Grafana 12.3.0"

    service.answers["/api/v1/provisioning/alert-rules"] = httpx.Response(403)
    assert call(grafana, "alert_rules_grafana") == {
        "error": "grafana refused: the service account may not read alert rules"
    }
    service.answers["/api/v1/provisioning/alert-rules"] = httpx.Response(500)
    assert call(grafana, "alert_rules_grafana") == {"error": "grafana answered HTTP 500"}
    only = connect(
        {"type": "grafana", "url": "https://g.example.org", "rules": False}, service, name="g"
    )
    assert names(only) == ["annotations_g"]


# --- Loki and VictoriaLogs ------------------------------------------------------------------

LOKI = {
    "type": "loki",
    "url": "http://loki:3100",
    "headers": {"X-Scope-OrgID": "home"},
    "streams": {"api": '{container="api"}', "proxy": '{job="caddy"}'},
    "max_lines": 20,
}


def test_loki_builds_the_query_from_a_name_and_words() -> None:
    answer = {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {"container": "api", "level": "error"},
                    "values": [
                        ["1794988800000000000", "timeout talking to db"],
                        ["1794988860000000000", "x" * 5000],
                    ],
                },
                {"stream": {"container": "api"}, "values": [["1794988830000000000", "retry 2"]]},
            ],
        },
    }
    service = Service({"/loki/api/v1/query_range": answer, "/loki/api/v1/labels": {"data": ["a"]}})
    loki = connect(LOKI, service, name="logs")
    assert names(loki) == ["search_logs_logs"]
    spec = loki.tools[0].spec
    assert spec.input_schema["properties"]["stream"]["enum"] == ["api", "proxy"]

    found = call(loki, "search_logs_logs", stream="api", contains=["timeout"], limit=500)
    assert [line["line"][:7] for line in found["lines"]] == ["xxxxxxx", "retry 2", "timeout"]
    assert len(found["lines"][0]["line"]) == 2000
    assert found["lines"][2]["labels"] == {"container": "api", "level": "error"}
    asked = service.last.url.params
    assert asked["query"] == '{container="api"} |= "timeout"'
    assert (asked["limit"], asked["direction"]) == ("20", "backward")  # the rules' own limit
    assert int(asked["end"]) - int(asked["start"]) == 3600 * 10**9
    assert service.last.headers["x-scope-orgid"] == "home"

    # A word is a quoted string, whatever is in it: it cannot close the selector or open another.
    hostile = '"} | line_format "{{.secret}}" or {job=~".+'
    call(loki, "search_logs_logs", stream="api", contains=[hostile], excludes=["debug"])
    query = service.last.url.params["query"]
    assert query == '{container="api"} |= ' + json.dumps(hostile) + ' != "debug"'

    asked_so_far = len(service.requests)
    assert "not readable" in call(loki, "search_logs_logs", stream="vault")["error"]
    assert "not readable" in call(loki, "search_logs_logs", stream='{job="vault"}')["error"]
    assert "list of at most" in call(loki, "search_logs_logs", stream="api", contains="x")["error"]
    many = call(loki, "search_logs_logs", stream="api", contains=["a"] * 7)
    assert "list of at most" in many["error"]
    assert len(service.requests) == asked_so_far
    assert loki.probe is not None
    assert loki.probe() == "1 labels"
    service.answers["/loki/api/v1/query_range"] = {"status": "error"}
    assert call(loki, "search_logs_logs", stream="api") == {"error": "loki refused the query"}
    assert connect({"type": "loki", "url": "http://loki:3100"}, service).tools == []


def test_victorialogs_reads_json_lines_with_a_start_and_a_limit() -> None:
    body = "\n".join(
        [
            json.dumps(
                {
                    "_time": "2026-11-18T08:00:00Z",
                    "_msg": "timeout talking to db",
                    "_stream": '{app="api"}',
                    "_stream_id": "0000",
                    "level": "error",
                }
            ),
            "not json",
            json.dumps(["not an object"]),
            "",
            json.dumps({"_time": "2026-11-18T08:01:00Z", "_msg": "retry 2", "_stream": "{}"}),
        ]
    )
    service = Service({"/select/logsql/query": body, "/health": "OK"})
    logs = connect(
        {
            "type": "victorialogs",
            "url": "http://vlogs:9428",
            "headers": {"AccountID": "7"},
            "streams": {"api": '{app="api"} OR {app="worker"}'},
        },
        service,
        name="vl",
    )
    found = call(logs, "search_logs_vl", stream="api", contains=["db"], excludes=['a "b"'])
    assert [line["line"] for line in found["lines"]] == ["retry 2", "timeout talking to db"]
    assert found["lines"][1]["fields"] == {"level": "error"}
    asked = service.last.url.params
    assert asked["query"] == '({app="api"} OR {app="worker"}) "db" !"a \\"b\\""'
    assert asked["limit"] == "50"
    assert datetime.fromisoformat(asked["end"]) - datetime.fromisoformat(
        asked["start"]
    ) == timedelta(hours=1)
    assert service.last.headers["accountid"] == "7"
    assert "not readable" in call(logs, "search_logs_vl", stream="other")["error"]
    assert logs.probe is not None
    assert logs.probe() == "healthy"


# --- OpenObserve and uptime-kuma, as named sources ----------------------------------------------


def test_openobserve_as_a_source_polls_and_reads_under_its_name(store: Store) -> None:
    row = {
        "_timestamp": int(T0.timestamp() * 1e6),
        "key": "disk_full/2aBc",
        "status": "firing",
        "start_time": int(T0.timestamp() * 1e6),
        "module": "alert",
    }
    service = Service(
        {
            "/api/home/_search": {"hits": [row]},
            "/api/v2/home/alerts": {"list": []},
        }
    )
    rules = parse_rules(
        {
            "sources": {
                "oo-home": {
                    "type": "openobserve",
                    "url": "http://oo:5080",
                    "org": "home",
                    "user": "paeon@example.org",
                    "password_env": "P",
                    "streams": ["docker"],
                    "metrics": ["container_*"],
                }
            }
        }
    )
    recorder = Recorder(store, Correlator(store, rules))
    source = sources.build(
        "oo-home",
        rules.sources["oo-home"],
        {"password": "pw"},
        store=store,
        recorder=recorder,
        transport=service.transport,
    )
    assert names(source) == ["search_logs_oo_home", "query_metrics_oo_home", "alert_query_oo_home"]
    assert (source.kind, source.alerts) == ("openobserve", "polled")
    assert source.polling is not None
    assert (source.polling.every_seconds, source.polling.stale_seconds) == (60, 900)
    source.polling.poller.poll(T0 + timedelta(minutes=1))
    (event,) = store.events(kind="alert.firing")
    assert (event.source, event.fingerprint) == ("oo-home", "2aBc")
    assert source.probe is not None
    assert source.probe() == "read the triggers stream"
    service.answers["/api/home/_search"] = httpx.Response(401)
    with pytest.raises(ReadError, match="openobserve answered HTTP 401"):
        source.probe()
    # Asked for one question only, nothing is polled.
    assert sources.build("oo-home", rules.sources["oo-home"], {"password": "pw"}).polling is None


def test_uptime_kuma_as_a_source() -> None:
    metrics = (
        'monitor_status{monitor_id="1",monitor_name="api",monitor_type="http"} 0\n'
        'monitor_response_time{monitor_id="1",monitor_name="api",monitor_type="http"} 31\n'
    )
    service = Service({"/metrics": metrics})
    kuma = connect(
        {"type": "uptime_kuma", "url": "http://kuma:3001", "api_key_env": "K"},
        service,
        {"api_key": "uk1_x"},
        name="kuma",
    )
    assert names(kuma) == ["monitor_status_kuma"]
    assert call(kuma, "monitor_status_kuma")["monitors"] == [
        {"name": "api", "type": "http", "status": "down", "response_ms": 31.0}
    ]
    assert service.last.headers["authorization"].startswith("Basic ")
    assert kuma.probe is not None
    assert kuma.probe() == "1 monitors"
    assert kuma.parse is not None
    payload = {
        "heartbeat": {"status": 0, "time": "2026-11-18 08:00:00", "msg": "timeout"},
        "monitor": {"id": 7, "name": "api"},
    }
    (down,) = kuma.parse(payload, T0)
    assert (down.source, down.fingerprint, down.status) == ("kuma", "7", Status.FIRING)
    assert kuma.parse({"msg": "test"}, T0) == []
    service.answers["/metrics"] = httpx.Response(401)
    with pytest.raises(ReadError, match="uptime-kuma answered HTTP 401"):
        kuma.probe()


# --- Gatus ----------------------------------------------------------------------------------


def test_gatus_custom_alert_and_endpoint_statuses() -> None:
    gatus = connect({"type": "gatus"}, name="gatus")
    assert gatus.parse is not None
    body = {
        "status": "TRIGGERED",
        "name": "front end",
        "group": "core",
        "description": "healthcheck failed 3 time(s) in a row",
        "errors": "",
    }
    (down,) = gatus.parse(body, T0)
    assert (down.status, down.severity, down.title, down.fingerprint) == (
        Status.FIRING,
        Severity.CRITICAL,
        "core/front end",
        "core_front-end",
    )
    assert down.labels == {"endpoint": "front end", "group": "core"}
    (up,) = gatus.parse({**body, "status": "RESOLVED"}, T0)
    assert (up.status, up.fingerprint) == (Status.RESOLVED, down.fingerprint)
    (alone,) = gatus.parse({"status": "triggered", "name": "blog"}, T0)
    assert (alone.title, alone.fingerprint) == ("blog", "_blog")
    # A placeholder Gatus did not replace, or a body without a name, says nothing.
    assert gatus.parse({**body, "status": "[ALERT_TRIGGERED_OR_RESOLVED]"}, T0) == []
    assert gatus.parse({"status": "TRIGGERED"}, T0) == []

    statuses = [
        {
            "name": "front end",
            "group": "core",
            "key": "core_front-end",
            "results": [
                {"status": 200, "duration": 31_000_000, "success": True, "timestamp": "t1"},
                {
                    "status": 502,
                    "hostname": "example.org",
                    "duration": 120_500_000,
                    "errors": ["upstream is down"],
                    "conditionResults": [
                        {"condition": "[STATUS] == 200", "success": False},
                        {"condition": "[RESPONSE_TIME] < 500", "success": True},
                    ],
                    "success": False,
                    "timestamp": "2026-11-18T08:00:00Z",
                },
            ],
            "events": [{"type": "UNHEALTHY", "timestamp": "2026-11-18T08:00:00Z"}],
        },
        {"name": "blog", "key": "_blog", "results": [{"status": 200, "success": True}]},
    ]
    service = Service({"/api/v1/endpoints/statuses": statuses, "/health": {"status": "UP"}})
    read = connect({"type": "gatus", "url": "http://gatus:8080"}, service, name="gatus")
    assert names(read) == ["endpoint_status_gatus"]
    (failing,) = call(read, "endpoint_status_gatus", failing_only=True)["endpoints"]
    assert (failing["healthy"], failing["http_status"], failing["response_ms"]) == (
        False,
        502,
        120.5,
    )
    assert failing["conditions"][0] == {"condition": "[STATUS] == 200", "met": False}
    assert (failing["failed_of_last"], failing["errors"]) == ("1 of 2", ["upstream is down"])
    assert failing["events"] == [{"type": "UNHEALTHY", "at": "2026-11-18T08:00:00Z"}]
    assert call(read, "endpoint_status_gatus", text="blog")["endpoints"][0]["group"] is None
    assert service.last.url.params["pageSize"] == "100"
    assert read.probe is not None
    assert read.probe() == "2 endpoints"


# --- Healthchecks ---------------------------------------------------------------------------


def test_healthchecks_webhook_and_read_only_key() -> None:
    hc = connect({"type": "healthchecks"}, name="hc")
    assert hc.parse is not None
    body = {"status": "down", "name": "Nightly backup", "slug": "nightly-backup", "tags": "prod db"}
    (down,) = hc.parse(body, T0)
    assert (down.status, down.severity, down.fingerprint, down.title) == (
        Status.FIRING,
        Severity.WARNING,
        "nightly-backup",
        "Nightly backup",
    )
    assert down.labels == {"check": "Nightly backup", "tags": "prod db"}
    (up,) = hc.parse({**body, "status": "up"}, T0)
    assert (up.status, up.fingerprint) == (Status.RESOLVED, "nightly-backup")
    (unnamed,) = hc.parse({"status": "down", "name": "", "slug": "x"}, T0)
    assert unnamed.title == "x"
    assert hc.parse({"status": "down", "name": "", "slug": ""}, T0) == []
    assert hc.parse({"status": "$STATUS", "name": "a"}, T0) == []

    checks = {
        "checks": [
            {
                "name": "Nightly backup",
                "slug": "nightly-backup",
                "tags": "prod db",
                "desc": "",
                "grace": 3600,
                "n_pings": 412,
                "status": "down",
                "started": False,
                "last_ping": "2026-11-17T02:00:04+00:00",
                "next_ping": "2026-11-18T02:00:04+00:00",
                "unique_key": "a6c7b0a8a66bed0df66abfdab3c77736861703ee",
                "schedule": "0 2 * * *",
                "tz": "UTC",
            },
            {
                "name": "Renew certificates",
                "slug": "",
                "status": "up",
                "uuid": "803f680d-e89b-492b-82ef-2be7b774a92d",
                "ping_url": "https://hc-ping.com/803f680d-e89b-492b-82ef-2be7b774a92d",
                "update_url": "https://healthchecks.io/api/v3/checks/803f680d",
                "pause_url": "https://healthchecks.io/api/v3/checks/803f680d/pause",
            },
        ]
    }
    key = "a6c7b0a8a66bed0df66abfdab3c77736861703ee"
    flips = [{"timestamp": "2026-11-18T03:00:04+00:00", "up": 0}]
    service = Service({"/api/v3/checks/": checks, f"/api/v3/checks/{key}/flips/": flips})
    read = connect(
        {"type": "healthchecks", "url": "https://healthchecks.io", "api_key_env": "K"},
        service,
        {"api_key": "ro-key"},
        name="hc",
    )
    assert names(read) == ["check_status_hc", "check_flips_hc"]
    listed = call(read, "check_status_hc")
    assert listed["count"] == 2
    assert service.last.headers["x-api-key"] == "ro-key"
    shown = json.dumps(listed)
    assert "ping_url" not in shown
    assert "803f680d" not in shown  # a UUID is the secret of a ping URL
    assert listed["checks"][0]["schedule"] == "0 2 * * *"
    assert call(read, "check_status_hc", text="backup")["count"] == 1

    assert call(read, "check_flips_hc", check="nightly-backup", since_hours=48) == {
        "flips": [{"at": "2026-11-18T03:00:04+00:00", "to": "down"}],
        "count": 1,
    }
    assert service.last.url.params["seconds"] == str(48 * 3600)
    assert call(read, "check_flips_hc", check="Nightly Backup")["count"] == 1  # by name too
    assert call(read, "check_flips_hc", check="nothing") == {
        "error": "no check with this name or slug"
    }
    assert read.probe is not None
    assert read.probe() == "2 checks"


# --- Netdata --------------------------------------------------------------------------------


def test_netdata_sender_and_agent_reads() -> None:
    netdata = connect({"type": "netdata"}, name="netdata")
    assert netdata.parse is not None
    form = {
        "status": "CRITICAL",
        "old_status": "WARNING",
        "name": "disk_space_usage",
        "chart": "disk_space._",
        "host": "server-1",
        "value": "96.2%",
        "info": "disk / space utilization",
        "url": "https://app.netdata.cloud/alerts/1",
    }
    (critical,) = netdata.parse(form, T0)
    assert (critical.status, critical.severity, critical.fingerprint) == (
        Status.FIRING,
        Severity.CRITICAL,
        "server-1/disk_space._/disk_space_usage",
    )
    assert critical.description == "disk / space utilization\nValue: 96.2%"
    assert critical.labels == {
        "alarm": "disk_space_usage",
        "host": "server-1",
        "chart": "disk_space._",
    }
    (warning,) = netdata.parse({**form, "status": "WARNING"}, T0)
    assert (warning.status, warning.severity) == (Status.FIRING, Severity.WARNING)
    (clear,) = netdata.parse({**form, "status": "CLEAR"}, T0)
    assert (clear.status, clear.fingerprint) == (Status.RESOLVED, critical.fingerprint)
    for silent in ("UNDEFINED", "UNINITIALIZED", "REMOVED", ""):
        assert netdata.parse({**form, "status": silent}, T0) == []
    assert netdata.parse({"status": "CRITICAL"}, T0) == []

    alarms = {
        "hostname": "server-1",
        "status": True,
        "alarms": {
            "disk_space._.disk_space_usage": {
                "id": 1,
                "name": "disk_space_usage",
                "chart": "disk_space._",
                "status": "CRITICAL",
                "value_string": "96.2%",
                "units": "%",
                "info": "disk / space utilization",
                "last_status_change": 1794988800,
            }
        },
    }
    table = {"labels": ["time", "user", "system"], "data": [[1794988800, 12.5, 3.1]]}
    service = Service(
        {
            "/api/v1/alarms": alarms,
            "/api/v1/data": lambda request: (
                table if request.url.params["chart"] == "system.cpu" else httpx.Response(404)
            ),
            "/api/v1/info": {"version": "v2.5.0"},
        }
    )
    agent = connect(
        {"type": "netdata", "url": "http://netdata:19999", "charts": ["system.*", "disk_space.*"]},
        service,
        name="nd",
    )
    assert names(agent) == ["active_alerts_nd", "chart_data_nd"]
    (alarm,) = call(agent, "active_alerts_nd")["alerts"]
    assert (alarm["status"], alarm["value"], alarm["chart"]) == (
        "CRITICAL",
        "96.2%",
        "disk_space._",
    )
    assert alarm["since"] == datetime.fromtimestamp(1794988800, UTC).isoformat()
    assert "all" not in service.last.url.params
    call(agent, "active_alerts_nd", all=True)
    assert "all" in service.last.url.params

    assert call(agent, "chart_data_nd", chart="system.cpu", since_minutes=30) == {
        "columns": ["time", "user", "system"],
        "rows": [[1794988800, 12.5, 3.1]],
    }
    asked = service.last.url.params
    assert (asked["after"], asked["points"], asked["group"]) == ("-1800", "60", "average")
    assert call(agent, "chart_data_nd", chart="system.ram") == {
        "error": "no chart with this id on this agent"
    }
    asked_so_far = len(service.requests)
    assert "not readable" in call(agent, "chart_data_nd", chart="apps.cpu")["error"]
    assert len(service.requests) == asked_so_far
    assert agent.probe is not None
    assert agent.probe() == "Netdata v2.5.0"
    assert names(connect({"type": "netdata", "url": "http://n:19999"}, service, name="nd")) == [
        "active_alerts_nd"
    ]


# --- Zabbix ---------------------------------------------------------------------------------


class Zabbix:
    """A double of `api_jsonrpc.php`: answers by method, and keeps the calls."""

    def __init__(self, results: dict[str, Any]) -> None:
        self.results = results
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append((body["method"], body["params"], request.headers.get("authorization")))
        result = self.results.get(body["method"])
        if callable(result):
            result = result(body["params"])
        if result is None:
            error = {"code": -32602, "message": "Invalid params.", "data": "No permissions"}
            return httpx.Response(200, json={"jsonrpc": "2.0", "error": error, "id": 1})
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": result, "id": 1})


def test_zabbix_webhook_tells_problem_recovery_and_update() -> None:
    zabbix = connect({"type": "zabbix"}, name="zabbix")
    assert zabbix.parse is not None
    body = {
        "event_id": "4711",
        "event_value": "1",
        "update_status": "0",
        "trigger_id": "20381",
        "name": "High CPU utilization",
        "severity": "4",
        "host": "server-1",
        "url": "https://zabbix.example.org/tr_events.php?triggerid=20381&eventid=4711",
    }
    (problem,) = zabbix.parse(body, T0)
    assert (problem.status, problem.severity, problem.fingerprint, problem.title) == (
        Status.FIRING,
        Severity.CRITICAL,
        "20381",
        "High CPU utilization",
    )
    assert problem.labels == {"trigger_id": "20381", "host": "server-1", "event_id": "4711"}
    (recovery,) = zabbix.parse({**body, "event_value": "0"}, T0)
    assert (recovery.status, recovery.fingerprint) == (Status.RESOLVED, "20381")
    assert zabbix.parse({**body, "update_status": "1"}, T0) == []  # an acknowledgement
    assert zabbix.parse({**body, "event_value": "{EVENT.VALUE}"}, T0) == []  # not replaced
    assert zabbix.parse({**body, "name": ""}, T0) == []
    levels = [zabbix.parse({**body, "severity": str(n)}, T0)[0].severity for n in range(6)]
    assert levels == [
        Severity.UNKNOWN,
        Severity.INFO,
        Severity.WARNING,
        Severity.WARNING,
        Severity.CRITICAL,
        Severity.CRITICAL,
    ]
    assert zabbix.parse({**body, "severity": "High"}, T0)[0].severity is Severity.UNKNOWN


def test_zabbix_reads_with_get_methods_and_the_host_allow_list() -> None:
    history = [{"itemid": "44", "clock": "1794988800", "value": "93.5", "ns": "0"}]
    api = Zabbix(
        {
            "apiinfo.version": "7.0.4",
            "problem.get": [
                {
                    "eventid": "4711",
                    "objectid": "20381",
                    "clock": "1794988800",
                    "name": "High CPU utilization",
                    "severity": "4",
                    "acknowledged": "0",
                }
            ],
            "trigger.get": [{"triggerid": "20381", "hosts": [{"hostid": "9", "host": "server-1"}]}],
            "host.get": lambda params: (
                [{"hostid": "9"}]
                if params.get("filter", {"host": ["server-1"]}) == {"host": ["server-1"]}
                else []
            ),
            "item.get": lambda params: (
                [
                    {
                        "itemid": "44",
                        "name": "CPU utilization",
                        "key_": "system.cpu.util",
                        "lastvalue": "93.5",
                        "units": "%",
                        "lastclock": "1794988800",
                        "value_type": "0",
                        "hosts": [
                            {"host": "server-1" if params.get("itemids") != ["99"] else "vault"}
                        ],
                    }
                ]
            ),
            "history.get": history,
        }
    )
    zabbix = connect(
        {
            "type": "zabbix",
            "url": "https://zabbix.example.org",
            "token_env": "T",
            "hosts": ["server-*"],
            "max_values": 5,
        },
        Service({"/api_jsonrpc.php": api}),
        {"token": "zbx"},
        name="zbx",
    )
    assert names(zabbix) == ["problems_zbx", "host_items_zbx", "item_history_zbx"]
    (problem,) = call(zabbix, "problems_zbx")["problems"]
    assert (problem["severity"], problem["hosts"], problem["acknowledged"]) == (
        "high",
        ["server-1"],
        False,
    )
    assert [c[0] for c in api.calls] == ["problem.get", "trigger.get"]
    assert api.calls[0][2] == "Bearer zbx"
    assert call(zabbix, "problems_zbx", text="server-9")["count"] == 0

    (item,) = call(zabbix, "host_items_zbx", host="server-1", text="cpu")["items"]
    assert (item["item_id"], item["key"], item["last_value"]) == ("44", "system.cpu.util", "93.5")
    assert api.calls[-1][1]["search"] == {"name": "cpu", "key_": "cpu"}
    assert "search" not in (call(zabbix, "host_items_zbx", host="server-1"), api.calls[-1][1])[1]
    assert call(zabbix, "host_items_zbx", host="server-9") == {"error": "no host with this name"}

    values = call(zabbix, "item_history_zbx", item_id="44", since_minutes=30)
    assert values["values"] == [
        {"at": datetime.fromtimestamp(1794988800, UTC).isoformat(), "value": "93.5"}
    ]
    asked = api.calls[-1][1]
    assert (asked["history"], asked["limit"], asked["itemids"]) == (0, 5, ["44"])  # a float item

    before = len(api.calls)
    assert "not readable" in call(zabbix, "host_items_zbx", host="vault")["error"]
    assert len(api.calls) == before  # a host that is not listed is not even looked up
    assert "host is not readable" in call(zabbix, "item_history_zbx", item_id="99")["error"]
    assert api.calls[-1][0] == "item.get"  # asked whose item it is, and stopped there
    assert "is a number" in call(zabbix, "item_history_zbx", item_id="44; drop")["error"]

    assert zabbix.probe is not None
    assert zabbix.probe() == "Zabbix 7.0.4"
    version = next(c for c in api.calls if c[0] == "apiinfo.version")
    assert version[2] is None  # this one method is refused when it comes with a token
    assert {c[0] for c in api.calls} <= sources_zabbix().METHODS

    api.results["problem.get"] = None
    assert call(zabbix, "problems_zbx") == {"error": "zabbix refused problem.get: Invalid params."}
    only = connect({"type": "zabbix", "url": "https://z.example.org"}, name="z")
    assert names(only) == ["problems_z"]


def sources_zabbix() -> Any:
    from paeon.sources import zabbix

    return zabbix


def test_zabbix_can_only_send_its_get_methods() -> None:
    zabbix = sources_zabbix()
    api = Zabbix({})
    http = Reader(
        "zabbix", "https://z", paths=("/api_jsonrpc.php",), transport=httpx.MockTransport(api)
    )
    for method in ("event.acknowledge", "host.delete", "script.execute", "user.login"):
        with pytest.raises(ReadError, match="not a method this source calls"):
            zabbix.Api(http).call(method, {})
    assert api.calls == []
    assert all(method.endswith(".get") or method == "apiinfo.version" for method in zabbix.METHODS)


# --- Beszel ---------------------------------------------------------------------------------


class Beszel:
    """A double of a Beszel hub: a login, then records by collection."""

    def __init__(self) -> None:
        self.records: dict[str, list[dict[str, Any]]] = {}
        self.logins = 0
        self.expired = False
        self.asked: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        path = request.url.path
        if path == "/api/health":
            return httpx.Response(200, json={"message": "API is healthy.", "code": 200})
        if path == "/api/collections/users/auth-with-password":
            if json.loads(request.content) != {"identity": "paeon@example.org", "password": "pw"}:
                return httpx.Response(400, json={"message": "Failed to authenticate."})
            self.logins += 1
            self.expired = False
            return httpx.Response(200, json={"token": f"session-{self.logins}", "record": {}})
        if self.expired or not request.headers.get("authorization", "").startswith("session-"):
            return httpx.Response(401, json={"message": "The request requires valid auth."})
        collection = path.split("/")[3]
        if collection not in self.records:
            return httpx.Response(404, json={"message": "Missing collection context."})
        items = self.records[collection]
        return httpx.Response(
            200, json={"page": 1, "perPage": 500, "totalItems": len(items), "items": items}
        )


BESZEL = {
    "type": "beszel",
    "url": "http://beszel:8090",
    "user": "paeon@example.org",
    "password_env": "P",
}
SYSTEM = {"id": "sys1", "name": "server-1", "status": "up", "info": {"cpu": 12.5, "mp": 61.2}}


def test_beszel_is_polled_for_its_triggered_alerts(store: Store) -> None:
    hub = Beszel()
    cpu = {"id": "al1", "system": "sys1", "name": "CPU", "value": 80, "min": 10, "triggered": True}
    status = {"id": "al2", "system": "sys1", "name": "Status", "triggered": False}
    hub.records = {
        "alerts": [{**cpu, "expand": {"system": SYSTEM}}, {**status, "expand": {"system": SYSTEM}}],
        "systems": [SYSTEM],
    }
    rules = parse_rules({"sources": {"beszel": BESZEL}})
    recorder = Recorder(store, Correlator(store, rules))
    source = sources.build(
        "beszel",
        rules.sources["beszel"],
        {"password": "pw"},
        store=store,
        recorder=recorder,
        transport=httpx.MockTransport(hub),
    )
    assert (source.alerts, source.parse) == ("polled", None)
    assert source.polling is not None
    poller = source.polling.poller
    assert poller.last_data_at() is None
    assert poller.poll(T0) == 1
    assert poller.last_data_at() == T0
    (firing,) = store.events(kind="alert.firing")
    assert (firing.source, firing.fingerprint) == ("beszel", "al1")
    assert firing.payload["title"] == "server-1: CPU"
    assert firing.payload["description"] == "CPU is beyond 80, averaged over 10 minutes."
    assert firing.payload["severity"] == "warning"
    assert "expand" not in firing.payload["raw"]
    assert hub.asked[-1].url.params["expand"] == "system"
    assert [r.method for r in hub.asked] == ["POST", "GET"]  # one sign-in, then only reads

    assert poller.poll(T0 + timedelta(minutes=1)) == 0  # still triggered: no new event
    hub.records["alerts"][0]["triggered"] = False
    hub.records["alerts"][1]["triggered"] = True
    hub.expired = True  # the session ran out: signed in once more, unseen
    assert poller.poll(T0 + timedelta(minutes=2)) == 2
    assert hub.logins == 2
    kinds = [
        (e.kind, e.fingerprint) for e in store.events(kinds=("alert.firing", "alert.resolved"))
    ]
    assert kinds == [("alert.firing", "al1"), ("alert.resolved", "al1"), ("alert.firing", "al2")]
    down = list(store.events(kind="alert.firing"))[-1].payload
    assert (down["severity"], down["description"]) == (
        "critical",
        "The hub lost its connection to the system.",
    )

    # A hub that stops answering raises, and the cursor stays where the last answer left it.
    del hub.records["alerts"]
    with pytest.raises(ReadError, match="beszel answered HTTP 404"):
        poller.poll(T0 + timedelta(minutes=3))
    assert poller.last_data_at() == T0 + timedelta(minutes=2)


def test_beszel_reads_systems_statistics_and_containers() -> None:
    hub = Beszel()
    hub.records = {
        "systems": [SYSTEM, {"id": "sys2", "name": "nas", "status": "down", "info": {}}],
        "system_stats": [
            {
                "created": "2026-11-18 08:00:00.000Z",
                "system": "sys1",
                "type": "1m",
                "stats": {"cpu": 12.5, "mp": 61.2, "dp": 44, "t": [1, 2], "ok": True},
            }
        ],
    }
    beszel = sources.build(
        "hub",
        parse_rules({"sources": {"hub": {**BESZEL, "alerts": False}}}).sources["hub"],
        {"password": "pw"},
        transport=httpx.MockTransport(hub),
    )
    assert beszel.polling is None
    assert names(beszel) == ["system_status_hub", "system_stats_hub", "containers_hub"]
    listed = call(beszel, "system_status_hub")
    assert [(s["name"], s["status"]) for s in listed["systems"]] == [
        ("server-1", "up"),
        ("nas", "down"),
    ]
    assert call(beszel, "system_status_hub", text="NAS")["count"] == 1

    stats = call(beszel, "system_stats_hub", system="Server-1", since_minutes=30)
    assert stats["stats"] == [{"at": "2026-11-18 08:00:00.000Z", "cpu": 12.5, "mp": 61.2, "dp": 44}]
    asked = hub.asked[-1].url.params
    assert asked["filter"].startswith("system='sys1' && type='1m' && created>='")
    assert (asked["sort"], asked["perPage"]) == ("-created", "30")
    assert call(beszel, "system_stats_hub", system="vault") == {"error": "no system with this name"}
    assert call(beszel, "containers_hub") == {"error": "this hub keeps no list of containers"}
    hub.records["containers"] = [
        {
            "name": "api",
            "system": "sys1",
            "status": "running",
            "health": "healthy",
            "cpu": 1.5,
            "memory": 210.4,
            "image": "ghcr.io/example/api:1.4",
            "expand": {"system": SYSTEM},
        }
    ]
    (container,) = call(beszel, "containers_hub", text="api")["containers"]
    assert (container["system"], container["health"]) == ("server-1", "healthy")
    assert beszel.probe is not None
    assert beszel.probe() == "2 systems"
    assert hub.logins == 1

    wrong = sources.build(
        "hub",
        parse_rules({"sources": {"hub": BESZEL}}).sources["hub"],
        {"password": "not it"},
        transport=httpx.MockTransport(hub),
    )
    assert call(wrong, "system_status_hub") == {"error": "beszel answered HTTP 400"}


# --- in the app -----------------------------------------------------------------------------


def app_with(store: Store, document: dict[str, Any], service: Service | None = None) -> TestClient:
    rules = parse_rules(document)
    settings = Settings(
        db_path=Path("unused"),
        host="127.0.0.1",
        port=8080,
        ingest_token=TOKEN,
        openobserve=None,
        heartbeat_url=None,
        heartbeat_seconds=60,
        rules=rules,
        source_secrets={name: {"password": "pw", "token": "tk"} for name in rules.sources},
    )
    transport = None if service is None else service.transport
    app = create_app(settings, store, source_transport=transport)
    return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})


def test_a_source_receives_at_its_own_address(store: Store) -> None:
    http = app_with(
        store,
        {
            "sources": {
                "gatus": {"type": "gatus"},
                "nd-home": {"type": "netdata"},
                "prom": PROM,
            }
        },
    )
    body = {"status": "TRIGGERED", "name": "front end", "group": "core"}
    assert http.post("/ingest/gatus", json=body).json() == {"alerts": 1, "changes": 1}
    assert http.post("/ingest/gatus", json=body).json() == {"alerts": 1, "changes": 0}
    # A shell script sends a form: no value has to be quoted by hand.
    form = {"status": "CRITICAL", "name": "ram_in_use", "chart": "system.ram", "host": "nas"}
    assert http.post("/ingest/nd-home", data=form).json() == {"alerts": 1, "changes": 1}
    fired = [(e.source, e.fingerprint) for e in store.events(kind="alert.firing")]
    assert fired == [("gatus", "core_front-end"), ("nd-home", "nas/system.ram/ram_in_use")]
    assert [e.source for e in store.events(kind="webhook.received")] == [
        "gatus",
        "gatus",
        "nd-home",
    ]

    assert http.post("/ingest/prom", json=body).status_code == 404  # it only reads
    assert http.post("/ingest/nothing", json=body).status_code == 404
    assert http.post("/ingest/gatus", content=b"{not json").status_code == 422
    assert http.post("/ingest/gatus", json=["a list"]).status_code == 422
    assert http.post("/ingest/gatus", content=b"\xff\xfe").status_code == 422
    unsigned = http.post("/ingest/gatus", json=body, headers={"Authorization": "Bearer no"})
    assert unsigned.status_code == 401

    # The two webhooks that need no entry still answer, under the names they always had.
    assert http.post("/ingest/alertmanager", json=AM_WEBHOOK).json() == {"alerts": 1, "changes": 1}
    kuma = {"heartbeat": {"status": 0, "msg": "down"}, "monitor": {"id": 3, "name": "blog"}}
    assert http.post("/ingest/uptime-kuma", json=kuma).json() == {"alerts": 1, "changes": 1}
    assert http.post("/ingest/change", json={"kind": "deploy", "title": "api"}).status_code == 200


def test_an_investigation_is_offered_the_tools_of_every_source(store: Store) -> None:
    service = Service({})
    http = app_with(
        store,
        {
            "sources": {
                "prom": PROM,
                "logs": LOKI,
                "alertmanager": {"type": "alertmanager", "url": "http://am:9093"},
            },
            "models": {"investigate": {"backend": "anthropic", "model": "claude-fable-5-2"}},
            "investigation": {"enabled": True},
        },
        service,
    )
    offered = [spec.name for spec in http.app.state.investigator._toolbox.specs]  # type: ignore[attr-defined]
    assert offered[-6:] == [
        "query_metrics_prom",
        "alert_rules_prom",
        "scrape_targets_prom",
        "search_logs_logs",
        "active_alerts_alertmanager",
        "silences_alertmanager",
    ]


def test_a_polled_source_that_stops_answering_is_reported_stale(store: Store) -> None:
    hub = Beszel()
    hub.records = {"alerts": [], "systems": []}
    service = Service({})
    service.answers = {}
    transport = httpx.MockTransport(hub)
    rules = parse_rules({"sources": {"beszel": {**BESZEL, "poll_seconds": 10}}})
    settings = Settings(
        db_path=Path("unused"),
        host="127.0.0.1",
        port=8080,
        ingest_token=TOKEN,
        openobserve=None,
        heartbeat_url=None,
        heartbeat_seconds=60,
        rules=rules,
        source_secrets={"beszel": {"password": "pw"}},
    )
    app = create_app(settings, store, source_transport=transport)
    polling = app.state.sources["beszel"].polling
    assert polling is not None
    recorder = Recorder(store)
    now = datetime.now(UTC)
    polling.poller.poll(now - timedelta(hours=1))
    recorder.source_health(
        "beszel",
        last_data_at=polling.poller.last_data_at(),
        now=now,
        stale_after=timedelta(seconds=polling.stale_seconds),
    )
    (stale,) = store.events(kind="alert.firing")
    assert (stale.source, stale.fingerprint) == ("paeon", "source_stale:beszel")

    shown = [
        ("beszel", "beszel", "polled", ("system_status_beszel",)),
        ("gatus", "gatus", "webhook", ()),
    ]
    lines = {line.name: line for line in views.sources_view(store, shown, now)}
    assert (lines["beszel"].stale, lines["beszel"].address) == (True, None)
    assert (lines["gatus"].stale, lines["gatus"].address) == (False, "/ingest/gatus")


def test_the_console_lists_the_sources(store: Store) -> None:
    http = app_with(
        store,
        {"ui": {"enabled": True}, "sources": {"gatus": {"type": "gatus"}, "prom": PROM}},
    )
    http.post("/ingest/gatus", json={"status": "TRIGGERED", "name": "blog"})
    page = http.get("/ui/sources").text
    assert 'href="/ui/sources" aria-current="page"' in page
    for text in (
        "/ingest/gatus",
        "query_metrics_prom",
        "/ingest/alertmanager",
        "/ingest/uptime-kuma",
    ):
        assert text in page
    assert "http://prometheus:9090" not in page  # where a source lives is not the console's to say
    shown = [
        (one.name, one.kind, one.alerts, tuple(names(one)))
        for one in http.app.state.sources.values()  # type: ignore[attr-defined]
    ]
    lines = {line.name: line for line in views.sources_view(store, shown, datetime.now(UTC))}
    assert (lines["gatus"].fired, lines["gatus"].alerts) == (1, "webhook")
    assert (lines["prom"].fired, lines["prom"].alerts, lines["prom"].last_at) == (0, "none", None)


# --- the command ----------------------------------------------------------------------------


def test_paeon_source_says_what_a_source_gives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "paeon.yaml"
    path.write_text(
        "sources:\n"
        "  gatus: {type: gatus}\n"
        "  prom: {type: prometheus, url: 'http://prometheus:9090', token_env: T_PROM}\n"
        "  hub: {type: beszel, url: 'http://beszel:8090', user: a@b.c, password_env: T_HUB}\n"
    )
    monkeypatch.setenv("PAEON_CONFIG", str(path))
    assert main(["source", "nothing"]) == 2
    assert "no source 'nothing' under `sources` (gatus, hub, prom)" in capsys.readouterr().err

    assert main(["source", "gatus"]) == 0
    out = capsys.readouterr().out
    assert "gatus: gatus" in out
    assert "receives alerts at /ingest/gatus" in out
    assert "nothing to ask" in out

    assert main(["source", "prom"]) == 2  # its token is named and not set
    assert "sources.prom: T_PROM" in capsys.readouterr().err

    monkeypatch.setenv("T_PROM", "tk")
    monkeypatch.setenv("T_HUB", "pw")
    answers: dict[str, Callable[[httpx.Request], httpx.Response]] = {
        "prometheus": lambda request: httpx.Response(200, json=MATRIX),
        "beszel": lambda request: httpx.Response(502),
    }
    real = httpx.Client

    def client(*args: Any, **kwargs: Any) -> httpx.Client:
        host = httpx.URL(kwargs["base_url"]).host
        return real(*args, **{**kwargs, "transport": httpx.MockTransport(answers[host])})

    monkeypatch.setattr(httpx, "Client", client)
    assert main(["source", "prom"]) == 0
    out = capsys.readouterr().out
    assert "tools for an investigation: alert_rules_prom, scrape_targets_prom" in out
    assert "answered: answered a query" in out

    assert main(["source", "hub"]) == 1
    captured = capsys.readouterr()
    assert "polls for alerts every 60 seconds" in captured.out
    assert "not read: beszel answered HTTP 502" in captured.err


def test_no_adapter_is_left_out_of_the_registry() -> None:
    union = Rules.model_fields["sources"].annotation
    from typing import get_args

    kinds = set()
    for model in get_args(get_args(get_args(union)[1])[0]):
        (kind,) = get_args(model.model_fields["type"].annotation)
        kinds.add(kind)
    assert kinds == {
        "prometheus", "alertmanager", "grafana", "loki", "victorialogs", "openobserve",
        "uptime_kuma", "gatus", "healthchecks", "netdata", "zabbix", "beszel",
    }  # fmt: skip
