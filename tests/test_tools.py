"""The investigator's tools: what they return, and above all what they refuse."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from paeon import knowledge
from paeon.correlation import Correlator
from paeon.recorder import Recorder
from paeon.redact import MASK
from paeon.rules import DockerTools, OpenObserveTools, Rules
from paeon.schema import Status
from paeon.store import Store
from paeon.tools import (
    Tool,
    Toolbox,
    ToolError,
    demux_logs,
    docker_tools,
    openobserve_tools,
    paeon_tools,
)
from support import alert


def box(tools: list[Tool], **kwargs: Any) -> Toolbox:
    return Toolbox(tools, **kwargs)


# --- toolbox --------------------------------------------------------------------------------------


def test_toolbox_redacts_bounds_and_contains_errors() -> None:
    def leaky(args: dict[str, Any]) -> Any:
        return {"line": "login with password=hunter2pass failed", "n": args.get("n")}

    def broken(args: dict[str, Any]) -> Any:
        raise ConnectionError("GET http://dockerproxy:2375/secret?token=abc failed")

    def refused(args: dict[str, Any]) -> Any:
        raise ToolError("not readable")

    def big(args: dict[str, Any]) -> Any:
        return ["x" * 100] * 50

    from paeon.models import ToolSpec

    schema = {"type": "object", "properties": {}}
    toolbox = box(
        [
            Tool(ToolSpec(name, "d", schema), run)
            for name, run in (
                ("leaky", leaky),
                ("broken", broken),
                ("refused", refused),
                ("big", big),
            )
        ],
        max_result_chars=600,
    )
    assert [spec.name for spec in toolbox.specs] == ["leaky", "broken", "refused", "big"]
    assert toolbox.call("leaky", {"n": 1}) == (
        {"line": f"login with password={MASK} failed", "n": 1},
        False,
    )
    # An unexpected error keeps its kind only: its message may quote a URL.
    assert toolbox.call("broken", {}) == ({"error": "the tool failed: ConnectionError"}, True)
    assert toolbox.call("refused", {}) == ({"error": "not readable"}, True)
    assert toolbox.call("nope", {}) == ({"error": "unknown tool 'nope'"}, True)
    result, is_error = toolbox.call("big", {})
    assert not is_error
    assert "narrow the request" in result["truncated"]
    assert len(result["start"]) == 600


# --- Paeon's own record ---------------------------------------------------------------------------


def test_paeon_tools_read_the_record(store: Store, tmp_path: Path) -> None:
    rec = Recorder(store, Correlator(store, Rules()))
    host = {"host": "server-1"}
    rec.observe(alert(Status.FIRING, 0, "api", title="API down", labels=host))
    rec.observe(alert(Status.RESOLVED, 5, "api", title="API down", labels=host))
    rec.observe(alert(Status.FIRING, 600, "disk", title="Disk nearly full", labels={"host": "nas"}))
    store.append("triage.verdict", {"effective_verdict": "real", "reason": "new"}, incident_id=1)
    store.append(
        "investigation.completed",
        {
            "findings": {
                "summary": "OOM kill",
                "root_cause": {"statement": "leak", "confidence": 0.8},
            }
        },
        incident_id=1,
    )
    store.append("feedback.recorded", {"vote": "up"}, incident_id=1)
    store.append("change.recorded", {"kind": "deploy", "title": "api v2"})
    (tmp_path / "runbook-api.md").write_text(
        "# API restarts\n\nWhen the API is OOM killed, check the cache size.\n"
    )

    toolbox = box(paeon_tools(store, [tmp_path]))
    opened, _ = toolbox.call("open_incidents", {})
    assert [(i["title"], i["host"]) for i in opened] == [("Disk nearly full", "nas")]

    past, _ = toolbox.call("past_incidents", {"text": "api", "days": 365})
    assert len(past) == 1
    assert past[0]["status"] == "resolved"
    assert past[0]["triage"] == {"verdict": "real", "reason": "new"}
    assert past[0]["investigation"]["summary"] == "OOM kill"
    assert past[0]["feedback"] == "up"
    assert (
        toolbox.call("past_incidents", {"host": "nas", "days": 365})[0][0]["title"]
        == "Disk nearly full"
    )
    assert toolbox.call("past_incidents", {"text": "nothing like it"})[0] == []
    assert toolbox.call("past_incidents", {"days": "many"}) == (
        {"error": "days must be an integer"},
        True,
    )

    assert toolbox.call("recent_changes", {"hours": 1})[0] == [
        {"kind": "deploy", "title": "api v2"}
    ]
    found, _ = toolbox.call("search_knowledge", {"query": "API OOM"})
    assert found[0]["path"] == "runbook-api.md"
    assert "cache size" in found[0]["excerpt"]
    assert toolbox.call("search_knowledge", {"query": ""})[1]


def test_knowledge_search_ranks_titles_and_skips_what_it_cannot_read(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# Notes\n\nThe proxy was mentioned once.\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "proxy-outage.md").write_text("# Proxy outage\n\nCertificates expired.\n")
    (tmp_path / "ignored.txt").write_text("proxy proxy proxy")
    hits = knowledge.search([tmp_path, tmp_path / "missing"], "proxy certificates")
    assert [hit.path for hit in hits] == ["sub/proxy-outage.md", "a.md"]
    assert hits[0].title == "Proxy outage"
    assert knowledge.search([tmp_path], "!!") == []
    assert knowledge.search([tmp_path], "unrelated-term") == []


def test_knowledge_search_knows_a_draft_and_can_leave_it_out(tmp_path: Path) -> None:
    (tmp_path / "draft.md").write_text(
        "---\nid: x\nstatus: draft\n---\n\n# Proxy outage\n\nProxy alerts are noise.\n"
    )
    (tmp_path / "read.md").write_text(
        "---\nid: y\nstatus: reviewed\n---\n\n# Proxy outage in May\n\nThe proxy died.\n"
    )
    (tmp_path / "plain.md").write_text("# Proxy runbook\n\nstatus: draft is only a sentence.\n")
    drafts = {hit.path: hit.draft for hit in knowledge.search([tmp_path], "proxy")}
    assert drafts == {"draft.md": True, "read.md": False, "plain.md": False}
    kept = knowledge.search([tmp_path], "proxy", drafts=False)
    assert sorted(hit.path for hit in kept) == ["plain.md", "read.md"]


# --- OpenObserve ----------------------------------------------------------------------------------


class FakeTelemetry:
    def __init__(self) -> None:
        self.sql: list[tuple[str, int]] = []
        self.promql: list[tuple[str, int]] = []

    def search(self, sql: str, start: int, end: int, size: int) -> list[dict[str, Any]]:
        assert end - start > 0
        self.sql.append((sql, size))
        return [{"log": "upstream timed out", "level": "error"}] * 3

    def query_range(self, promql: str, start: int, end: int, step: int) -> Any:
        self.promql.append((promql, step))
        return {"resultType": "matrix", "result": []}


def test_search_logs_reads_only_listed_streams_with_a_plain_filter() -> None:
    telemetry = FakeTelemetry()
    config = OpenObserveTools(streams=("traefik", "docker"), max_rows=20)
    toolbox = box(openobserve_tools(telemetry, config))
    assert [spec.name for spec in toolbox.specs] == ["search_logs"]  # metrics are opt-in

    result, is_error = toolbox.call(
        "search_logs",
        {
            "stream": "traefik",
            "where": "status >= 500",
            "columns": ["status", "path"],
            "limit": 500,
        },
    )
    assert not is_error
    assert result["count"] == 3
    assert telemetry.sql == [
        ('SELECT status, path FROM "traefik" WHERE status >= 500 ORDER BY _timestamp DESC', 20)
    ]
    toolbox.call("search_logs", {"stream": "docker"})
    assert telemetry.sql[-1][0] == 'SELECT * FROM "docker" ORDER BY _timestamp DESC'


@pytest.mark.parametrize(
    "args",
    [
        {"stream": "finance"},
        {"stream": 'traefik" UNION SELECT * FROM "finance'},
        {"stream": "traefik", "where": "1 = 1 UNION SELECT * FROM finance"},
        {"stream": "traefik", "where": "x IN (select secret from finance)"},
        {"stream": "traefik", "where": "1 = 1; DROP TABLE x"},
        {"stream": "traefik", "where": "1 = 1 -- comment"},
        {"stream": "traefik", "where": "1 = 1 /* comment */"},
        {"stream": "traefik", "columns": ["a, (SELECT 1)"]},
        {"stream": "traefik", "columns": "status"},
        {"stream": "traefik", "limit": "all"},
        {},
    ],
)
def test_search_logs_refuses_everything_else(args: dict[str, Any]) -> None:
    telemetry = FakeTelemetry()
    toolbox = box(openobserve_tools(telemetry, OpenObserveTools(streams=("traefik",))))
    _, is_error = toolbox.call("search_logs", args)
    assert is_error
    assert telemetry.sql == []


def test_query_metrics_reads_only_listed_metrics() -> None:
    telemetry = FakeTelemetry()
    config = OpenObserveTools(metrics=("container_*", "up"))
    toolbox = box(openobserve_tools(telemetry, config))
    assert [spec.name for spec in toolbox.specs] == ["query_metrics"]
    allowed = [
        "up",
        'sum by (name) (rate(container_cpu_usage_seconds_total{name="api"}[5m]))',
        "container_memory_usage_bytes / on (name) group_left container_spec_memory_limit_bytes",
        'max_over_time(up{job="x and node_secret"}[1h] offset 5m) > bool 0',
        'up{job="a#b", path=~"/x{1,2}"} == 1',
        "container_memory_usage_bytes {name='api'}",
    ]
    for promql in allowed:
        result, is_error = toolbox.call("query_metrics", {"promql": promql, "since_minutes": 120})
        assert (result, is_error) == ({"resultType": "matrix", "result": []}, False), promql
    assert [query for query, _ in telemetry.promql] == allowed
    assert telemetry.promql[0][1] == 120


@pytest.mark.parametrize(
    "promql",
    [
        "finance_balance",
        "up + finance_balance",
        "sum(rate(finance_transactions_total[5m]))",
        'up or {__name__=~"finance.*"}',
        '{job="bank"}',
        'up or {job="bank"}',
        'up atan2 {job="bank"}',
        'up ATAN2 {job="bank"}',
        'sum {job="bank"}',
        'up + sum({job="bank"})',
        'up or {"finance_balance"}',
        'up{"finance_balance", job="x"}',
        'up or on () {job="bank"}',
        "up # } or finance_balance{",
        'up{job="a"} # x\nor finance_balance',
        "up\nor finance_balance",
        'sum(up) by (job) {job="bank"}',
        'up{job="x',
        "up[5m",
        "42",
    ],
)
def test_query_metrics_refuses_everything_else(promql: str) -> None:
    telemetry = FakeTelemetry()
    toolbox = box(openobserve_tools(telemetry, OpenObserveTools(metrics=("container_*", "up"))))
    _, is_error = toolbox.call("query_metrics", {"promql": promql})
    assert is_error, promql
    assert telemetry.promql == []


def test_metric_names() -> None:
    from paeon.tools import metric_names

    assert metric_names("histogram_quantile(0.9, sum by (le) (rate(http_seconds_bucket[5m])))") == {
        "http_seconds_bucket"
    }
    assert metric_names("a:b:rate5m unless on (x) c") == {"a:b:rate5m", "c"}


# --- Docker ---------------------------------------------------------------------------------------

INSPECT = {
    "Name": "/api",
    "Created": "2026-11-01T10:00:00Z",
    "RestartCount": 7,
    "Path": "/entrypoint.sh",
    "Args": ["--db-password", "from-the-command-line"],
    "State": {
        "Status": "restarting",
        "Running": False,
        "Restarting": True,
        "OOMKilled": True,
        "ExitCode": 137,
        "Error": "",
        "StartedAt": "2026-11-18T07:58:00Z",
        "FinishedAt": "2026-11-18T07:59:10Z",
        "Health": {
            "Status": "unhealthy",
            "FailingStreak": 4,
            "Log": [{"Output": "curl: (7) refused"}],
        },
    },
    "Config": {
        "Image": "ghcr.io/example/api:2.1",
        "Env": ["DATABASE_URL=postgres://api:from-the-environment@db/api"],
        "Cmd": ["--token", "from-the-command"],
        "Labels": {
            "com.example.team": "home",
            "traefik.http.middlewares.auth.basicauth.users": "u:hash",
        },
    },
    "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}, "Memory": 268435456},
    "NetworkSettings": {"Networks": {"apps": {"IPAddress": "172.20.0.5", "Aliases": ["api"]}}},
    "Mounts": [
        {"Type": "bind", "Source": "/srv/data/api", "Destination": "/data", "RW": True},
        {"Type": "bind", "Source": "/home/owner/.ssh", "Destination": "/keys", "RW": False},
    ],
}


API_ID = "a1" * 32
DB_ID = "b2" * 32


def frame(stream: int, text: str) -> bytes:
    payload = text.encode()
    return bytes([stream, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload


def docker(seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/containers/json":
            return httpx.Response(
                200,
                json=[
                    {
                        "Id": API_ID,
                        "Names": ["/api"],
                        "Image": "api:2.1",
                        "State": "restarting",
                        "Status": "Restarting (137)",
                    },
                    {
                        "Id": DB_ID,
                        "Names": ["/db"],
                        "Image": "postgres:17",
                        "State": "running",
                        "Status": "Up 3 days",
                    },
                    {"Id": "not-an-id/../x", "Names": ["/odd"]},
                ],
            )
        if path == f"/containers/{API_ID}/json":
            return httpx.Response(200, json=INSPECT)
        if path == f"/containers/{DB_ID}/json":
            return httpx.Response(500)
        if path == f"/containers/{API_ID}/logs":
            return httpx.Response(
                200,
                content=frame(1, "2026-11-18T07:59:08Z starting\n")
                + frame(2, "2026-11-18T07:59:09Z Killed\n"),
            )
        return httpx.Response(404, json={"message": "No such container"})

    return httpx.Client(base_url="http://dockerproxy:2375", transport=httpx.MockTransport(handler))


def docker_box(seen: list[httpx.Request], **config: Any) -> Toolbox:
    return box(docker_tools(docker(seen), DockerTools(url="http://dockerproxy:2375", **config)))


def test_docker_list_and_inspect_never_show_the_environment() -> None:
    seen: list[httpx.Request] = []
    toolbox = docker_box(seen, labels=("com.example.team",), mount_sources=("/srv/data",))
    listed, _ = toolbox.call("list_containers", {})
    assert [c["name"] for c in listed] == ["api", "db", "odd"]

    inspected, is_error = toolbox.call("inspect_container", {"name": "api"})
    assert not is_error
    assert inspected["state"]["oomkilled"] is True
    assert inspected["state"]["exitcode"] == 137
    assert inspected["restart_count"] == 7
    assert inspected["health"] == {
        "status": "unhealthy",
        "failing_streak": 4,
        "last_output": "[not shown]",  # written by the container: shown only where logs are
    }
    with_logs = docker_box([], logs=("api",)).call("inspect_container", {"name": "api"})[0]
    assert with_logs["health"]["last_output"] == "curl: (7) refused"
    assert inspected["labels"] == {"com.example.team": "home"}
    assert inspected["mounts"] == [
        {"destination": "/data", "type": "bind", "read_write": True, "source": "/srv/data/api"},
        {"destination": "/keys", "type": "bind", "read_write": False, "source": "[not shown]"},
    ]
    everything = json.dumps(inspected)
    for secret in ("from-the-environment", "from-the-command", "basicauth", "DATABASE_URL"):
        assert secret not in everything
    assert all(request.method == "GET" for request in seen)


def test_docker_logs_only_for_listed_containers() -> None:
    seen: list[httpx.Request] = []
    toolbox = docker_box(seen, logs=("api", "media-*"), max_log_lines=50)
    result, is_error = toolbox.call(
        "container_logs", {"name": "api", "tail": 500, "since_minutes": 30}
    )
    assert not is_error
    assert result == {"lines": ["2026-11-18T07:59:08Z starting", "2026-11-18T07:59:09Z Killed"]}
    listing, logs = seen
    assert listing.url.path == "/containers/json"
    assert logs.url.path == f"/containers/{API_ID}/logs"  # by id, never by the argument
    assert logs.url.params["tail"] == "50"  # capped
    assert "since" in logs.url.params

    refused, is_error = toolbox.call("container_logs", {"name": "db"})
    assert is_error
    assert "not readable" in refused["error"]
    assert [r.url.path for r in seen[2:]] == ["/containers/json"]  # resolved, logs never asked


def test_docker_accepts_exact_names_only() -> None:
    """Docker resolves an id or an id prefix where a name is expected: the tool must not."""
    seen: list[httpx.Request] = []
    toolbox = docker_box(seen, logs=("*",))
    for argument in (DB_ID, DB_ID[:12], "b2b2", "ap"):
        for tool in ("container_logs", "inspect_container"):
            result, is_error = toolbox.call(tool, {"name": argument})
            assert is_error
            assert "no such container" in result["error"]
    assert {r.url.path for r in seen} == {"/containers/json"}
    odd, is_error = toolbox.call("inspect_container", {"name": "odd"})
    assert is_error
    assert odd == {"error": "docker returned an unexpected container id"}


@pytest.mark.parametrize("name", ["../../secrets", "api/json?x=1", "", "a b", 7])
def test_docker_refuses_names_that_are_not_names(name: Any) -> None:
    seen: list[httpx.Request] = []
    toolbox = docker_box(seen, logs=("*",))
    assert toolbox.call("inspect_container", {"name": name})[1]
    assert toolbox.call("container_logs", {"name": name})[1]
    assert seen == []


def test_docker_errors_do_not_carry_the_url() -> None:
    seen: list[httpx.Request] = []
    toolbox = docker_box(seen)
    ghost, is_error = toolbox.call("inspect_container", {"name": "ghost"})
    assert is_error
    assert "no such container" in ghost["error"]
    assert toolbox.call("inspect_container", {"name": "db"}) == (
        {"error": "docker answered HTTP 500"},
        True,
    )

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    http = httpx.Client(
        base_url="http://dockerproxy:2375", transport=httpx.MockTransport(unreachable)
    )
    offline = box(docker_tools(http, DockerTools(url="http://dockerproxy:2375")))
    assert offline.call("list_containers", {}) == (
        {"error": "docker is unreachable: ConnectError"},
        True,
    )


def test_demux_logs_with_and_without_a_tty() -> None:
    assert demux_logs(frame(1, "out\n") + frame(2, "err\n")) == "out\nerr\n"
    assert demux_logs(b"plain tty output\n") == "plain tty output\n"
    assert demux_logs(b"") == ""


def test_mount_sources_match_whole_path_components() -> None:
    from paeon.tools import _under

    assert _under("/srv/data", ("/srv/data/",))
    assert _under("/srv/data/api", ("/srv/data",))
    assert not _under("/srv/data-private/api", ("/srv/data",))
    assert not _under("/home/owner/.ssh", ("/srv/data",))
