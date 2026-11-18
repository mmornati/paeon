"""The tools that lift phase 2's limits: an alert's own query, monitors, git history, other
hosts. Each reads only what a list allows, and none takes a free-form argument anywhere."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from paeon import knowledge, proc
from paeon.recorder import Recorder
from paeon.rules import GitTools, OpenObserveTools, RulesError, SshTools, parse_rules
from paeon.schema import Severity, Status
from paeon.sources.openobserve import (
    AlertDefinition,
    HttpSearchClient,
    OpenObserveSource,
    parse_definition,
)
from paeon.store import Store
from paeon.tools import (
    Toolbox,
    git_tools,
    openobserve_tools,
    paeon_tools,
    parse_monitors,
    sql_streams,
    ssh_tools,
    uptime_kuma_tools,
)
from support import alert
from test_openobserve import KEY, FakeClient, row
from test_tools import FakeTelemetry

ALERT_ID = KEY.rpartition("/")[2]
V2 = {
    "id": ALERT_ID,
    "name": "slo_fast_burn_photo_indexer_health",
    "description": "The photo indexer burns its error budget fast.",
    "stream_type": "logs",
    "stream_name": "traefik",
    "enabled": True,
    "query_condition": {"type": "sql", "sql": 'SELECT count(*) AS n FROM "traefik" WHERE s >= 500'},
    "trigger_condition": {"period": 10, "operator": ">=", "threshold": 5, "silence": 0},
    "destinations": ["ntfy_with_a_token_in_its_url"],
    "context_attributes": {"severity": "Critical"},
}


def test_an_alert_definition_is_parsed_without_its_destinations() -> None:
    definition = parse_definition(V2)
    assert definition is not None
    assert (definition.kind, definition.stream, definition.period_minutes) == ("sql", "traefik", 10)
    assert definition.severity is Severity.CRITICAL
    assert definition.query == V2["query_condition"]["sql"]  # type: ignore[index]
    shown = definition.as_dict()
    assert shown["fires_when"] == ">= 5"
    assert "destinations" not in shown
    assert "ntfy" not in str(shown)

    custom = parse_definition({"name": "disk", "query_condition": {"conditions": [{"c": 1}]}})
    assert custom is not None
    assert (custom.id, custom.kind, custom.query, custom.severity) == (
        "disk",
        "custom",
        None,
        Severity.UNKNOWN,
    )
    assert custom.as_dict()["fires_when"] is None
    promql = parse_definition({"name": "up", "query_condition": {"promql": "up == 0"}})
    assert promql is not None
    assert promql.kind == "promql"
    junk: list[Any] = [None, [], {"name": ""}, {"id": 3, "name": 4}]
    assert [parse_definition(item) for item in junk] == [None] * 4


def client_for(handler: Any) -> HttpSearchClient:
    return HttpSearchClient(
        "http://openobserve:5080", "default", "u", "p", transport=httpx.MockTransport(handler)
    )


# One entry of `GET /api/v2/{org}/alerts`, with the keys an OpenObserve 1.1 instance returns.
LISTED = {
    "alert_id": ALERT_ID,
    "folder_id": "default",
    "name": V2["name"],
    "owner": "admin@example.org",
    "description": V2["description"],
    "alert_type": "scheduled",
    "stream_name": "traefik",
    "stream_type": "logs",
    "condition": {**V2["query_condition"], "conditions": None, "promql": None},  # type: ignore[dict-item]
    "trigger_condition": V2["trigger_condition"],
    "enabled": True,
    "last_outcome": "firing",
    "level": "critical",
}


def test_definitions_come_from_one_request_to_the_v2_list() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url.path}?{request.url.query.decode()}")
        return httpx.Response(200, json={"list": [LISTED]})

    client = client_for(handler)
    try:
        by_id = client.alert_definition(ALERT_ID)
        assert by_id is not None
        assert (by_id.stream, by_id.kind, by_id.threshold) == ("traefik", "sql", 5.0)
        assert by_id.sql.startswith("SELECT count")
        # `level` is the alert's state right now, not a severity someone chose.
        assert by_id.severity is Severity.UNKNOWN
        assert client.alert_definition(V2["name"]) == by_id  # type: ignore[arg-type]
        assert client.alert_definition("unknown") is None
        assert seen == ["/api/v2/default/alerts?page_size=1000"]  # listed once, cached
    finally:
        client.close()


def test_a_list_without_queries_is_completed_alert_by_alert() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/v2/default/alerts":
            return httpx.Response(200, json={"list": [{"alert_id": ALERT_ID, "name": V2["name"]}]})
        assert request.url.path == f"/api/v2/default/alerts/{ALERT_ID}"
        return httpx.Response(200, json=V2)

    client = client_for(handler)
    try:
        found = client.alert_definition(ALERT_ID)
        assert found is not None
        assert found.stream == "traefik"
        assert len(seen) == 2
    finally:
        client.close()


def test_slo_and_promql_alerts_say_what_they_watch() -> None:
    slo = parse_definition(
        {
            "alert_id": "s1",
            "name": "slo_fast_burn",
            "condition": {
                "type": "slo",
                "slo_condition": {
                    "slo_id": "7",
                    "kind": "burn_rate",
                    "operator": ">",
                    "critical": 14.4,
                },
            },
        }
    )
    assert slo is not None
    assert (slo.kind, slo.query) == ("slo", None)
    assert slo.as_dict()["conditions"]["kind"] == "burn_rate"
    metric = parse_definition(
        {
            "alert_id": "m1",
            "name": "health",
            "condition": {
                "type": "promql",
                "promql": "sum(up)",
                "promql_condition": {"column": "value", "operator": ">", "value": 0},
            },
        }
    )
    assert metric is not None
    assert metric.as_dict()["conditions"] == {"column": "value", "operator": ">", "value": 0}
    toolbox, telemetry = alert_query(
        {"name": "slo", "condition": {"type": "slo", "slo_condition": {"kind": "burn_rate"}}},
        OpenObserveTools(streams=("traefik",), metrics=("up",)),
    )
    result, _ = toolbox.call("alert_query", {"alert": "slo"})
    assert "no query to re-run" in result["not_run"]
    assert (telemetry.sql, telemetry.promql) == ([], [])


def test_definitions_fall_back_to_the_older_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/v2/" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, json={"list": [{**V2, "id": None}, "junk"]})

    client = client_for(handler)
    try:
        found = client.alert_definition(V2["name"])  # type: ignore[arg-type]
        assert found is not None
        assert found.sql.startswith("SELECT count")
    finally:
        client.close()

    gone = client_for(lambda request: httpx.Response(404))
    try:
        assert gone.alert_definition("x") is None
    finally:
        gone.close()


def test_a_firing_is_enriched_once_with_its_definition(store: Store) -> None:
    asked: list[str] = []

    def definitions(key: str) -> AlertDefinition | None:
        asked.append(key)
        return parse_definition(V2)

    rows = [row(0, "firing"), row(5, "firing"), row(10, "normal")]
    source = OpenObserveSource(FakeClient(rows), store, Recorder(store), definitions=definitions)
    source.poll(__import__("paeon.schema").schema.from_micros(rows[-1]["_timestamp"] + 1))
    assert asked == [ALERT_ID]  # per firing, not per evaluation row
    firing = next(store.events(kind="alert.firing")).payload
    assert firing["description"] == "The photo indexer burns its error budget fast."
    assert firing["severity"] == "critical"
    assert firing["query"].startswith("SELECT count")
    assert firing["labels"]["stream"] == "traefik"


def test_an_unreadable_definition_changes_nothing(store: Store) -> None:
    def broken(key: str) -> AlertDefinition | None:
        raise httpx.ConnectError("down")

    for position, definitions in enumerate((broken, lambda key: None)):
        rows = [row(position * 20, "firing", key=f"alert_{position}/id{position}")]
        source = OpenObserveSource(
            FakeClient(rows), store, Recorder(store), name=f"oo{position}", definitions=definitions
        )
        source.poll(__import__("paeon.schema").schema.from_micros(rows[-1]["_timestamp"] + 1))
    firings = [e.payload for e in store.events(kind="alert.firing")]
    assert [f["description"] for f in firings] == ["", ""]


def alert_query(
    definition: dict[str, Any] | None, config: OpenObserveTools
) -> tuple[Toolbox, FakeTelemetry]:
    telemetry = FakeTelemetry()

    def definitions(key: str) -> AlertDefinition | None:
        if key == "boom":
            raise RuntimeError("http://openobserve:5080 said no")
        return parse_definition(definition) if key != "unknown" else None

    return Toolbox(openobserve_tools(telemetry, config, definitions)), telemetry


def test_alert_query_reruns_the_query_only_on_readable_data() -> None:
    allowed = OpenObserveTools(streams=("traefik",), metrics=("up",))
    toolbox, telemetry = alert_query(V2, allowed)
    result, error = toolbox.call("alert_query", {"alert": V2["name"], "since_minutes": 30})
    assert not error
    assert result["definition"]["stream"] == "traefik"
    assert result["result"]["count"] == 3
    assert telemetry.sql == [(V2["query_condition"]["sql"], 50)]  # type: ignore[index]

    # The alert reads a stream that is not listed, or joins one: the definition, no rows.
    for sql in ('SELECT * FROM "finance"', 'SELECT * FROM "traefik" JOIN finance ON 1 = 1'):
        hidden = {**V2, "query_condition": {"type": "sql", "sql": sql}}
        toolbox, telemetry = alert_query(hidden, allowed)
        result, _ = toolbox.call("alert_query", {"alert": "x"})
        assert "not on the allow-list" in result["not_run"]
        assert "result" not in result
        assert telemetry.sql == []
    other_stream = {**V2, "stream_name": "finance"}
    assert "not_run" in alert_query(other_stream, allowed)[0].call("alert_query", {"alert": "x"})[0]


def test_alert_query_on_metrics_conditions_and_errors() -> None:
    allowed = OpenObserveTools(streams=("traefik",), metrics=("up",))
    up = {"name": "up", "query_condition": {"type": "promql", "promql": 'up{job="api"} == 0'}}
    toolbox, telemetry = alert_query(up, allowed)
    assert "result" in toolbox.call("alert_query", {"alert": "up"})[0]
    assert telemetry.promql[0][0] == 'up{job="api"} == 0'
    for promql in ("node_secret_metric > 1", '{__name__=~".+"}'):
        hidden = {"name": "x", "query_condition": {"type": "promql", "promql": promql}}
        toolbox, telemetry = alert_query(hidden, allowed)
        assert "not_run" in toolbox.call("alert_query", {"alert": "x"})[0]
        assert telemetry.promql == []
    custom = {"name": "disk", "stream_name": "traefik", "query_condition": {"conditions": []}}
    result, _ = alert_query(custom, allowed)[0].call("alert_query", {"alert": "disk"})
    assert "no query to re-run" in result["not_run"]

    toolbox, _ = alert_query(V2, allowed)
    assert toolbox.call("alert_query", {"alert": "unknown"}) == (
        {"error": "no alert with this name or id"},
        True,
    )
    # An error from the client would quote its URL: only its kind is shown.
    assert toolbox.call("alert_query", {"alert": "boom"}) == (
        {"error": "the alert definitions were not read: RuntimeError"},
        True,
    )
    assert "alert_query" not in [t.spec.name for t in openobserve_tools(FakeTelemetry(), allowed)]


def test_alert_history_is_the_record_of_any_monitor(store: Store, tmp_path: Path) -> None:
    recorder = Recorder(store)
    for minute, status in ((0, Status.FIRING), (3, Status.RESOLVED), (9, Status.FIRING)):
        recorder.observe(alert(status, minute, "m1", source="uptime-kuma", title="Media HTTP"))
    recorder.observe(alert(Status.FIRING, 1, "m2", source="uptime-kuma", title="VPN ping"))
    toolbox = Toolbox(paeon_tools(store, [tmp_path]))
    found, error = toolbox.call("alert_history", {"text": "media", "days": 3650})
    assert not error
    (media,) = found
    assert (media["title"], media["firings"], media["now"]) == ("Media HTTP", 2, "firing")
    assert [change["status"] for change in media["changes"]] == ["firing", "resolved", "firing"]
    assert toolbox.call("alert_history", {"text": "nothing"})[0] == []
    assert toolbox.call("alert_history", {})[1]


def _metric(name: str, monitor: str, kind: str, url: str, host: str, value: str) -> str:
    labels = (
        f'monitor_name="{monitor}",monitor_type="{kind}",monitor_url="{url}",'
        f'monitor_hostname="{host}",monitor_port="null"'
    )
    return f"{name}{{{labels}}} {value}"


_MEDIA = ("Media HTTP", "http", "https://u:pw@media.example.org", "null")
_VPN = ('VPN \\"ping\\"', "ping", "null", "10.0.0.2")
METRICS = "\n".join(
    [
        "# HELP monitor_status Monitor Status (1 = UP, 0= DOWN, 2= PENDING, 3= MAINTENANCE)",
        "# TYPE monitor_status gauge",
        _metric("monitor_status", *_MEDIA, "0"),
        _metric("monitor_status", *_VPN, "1"),
        _metric("monitor_response_time", *_MEDIA, "812"),
        _metric("monitor_cert_days_remaining", *_MEDIA, "41"),
        _metric("monitor_response_time", *_VPN, "NaN"),
        "process_cpu_seconds_total 12.5",
        "",
    ]
)


def test_monitor_status_reads_the_state_and_leaves_the_urls_out() -> None:
    monitors = parse_monitors(METRICS)
    assert monitors[0] == {
        "name": "Media HTTP",
        "type": "http",
        "status": "down",
        "response_ms": 812.0,
        "certificate_days_left": 41.0,
    }
    assert monitors[1]["status"] == "up"
    assert "pw" not in str(monitors)
    assert "10.0.0.2" not in str(monitors)

    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, text=METRICS)

    with httpx.Client(base_url="http://kuma:3001", transport=httpx.MockTransport(handler)) as http:
        toolbox = Toolbox(uptime_kuma_tools(http, "uk1_key"))
        result, error = toolbox.call("monitor_status", {"text": "media"})
        assert not error
        assert (result["count"], result["monitors"][0]["status"]) == (1, "down")
        assert seen[0] is not None
        assert seen[0].startswith("Basic ")
        Toolbox(uptime_kuma_tools(http, None)).call("monitor_status", {})
        assert seen[1] is None

    def refuse(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/metrics" and request.headers.get("x-fail"):
            raise httpx.ConnectError("http://kuma:3001 is down")
        return httpx.Response(401)

    with httpx.Client(base_url="http://kuma:3001", transport=httpx.MockTransport(refuse)) as http:
        toolbox = Toolbox(uptime_kuma_tools(http, None))
        assert toolbox.call("monitor_status", {})[0] == {"error": "uptime-kuma answered HTTP 401"}
        http.headers["x-fail"] = "1"
        assert toolbox.call("monitor_status", {})[0] == {
            "error": "uptime-kuma is unreachable: ConnectError"
        }


def git(repo: Path, *args: str) -> None:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}
    code, output = proc.run(["git", "-C", str(repo), *args], env={**proc.GIT_ENV, **env})
    assert code == 0, output


def test_recent_commits_of_a_listed_repository(tmp_path: Path) -> None:
    repo = tmp_path / "deployment"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "compose.yaml").write_text("services: {}\n")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "Raise the worker's memory limit")
    toolbox = Toolbox(git_tools(GitTools(repos={"deployment": str(repo)})))
    result, error = toolbox.call("recent_commits", {"repo": "deployment"})
    assert not error
    (commit,) = result["commits"]
    assert commit["subject"] == "Raise the worker's memory limit"
    assert commit["files"] == ["compose.yaml"]
    assert "example.org" not in str(result)  # no author

    assert toolbox.call("recent_commits", {"repo": str(repo)})[1]  # a name, never a path
    assert toolbox.call("recent_commits", {"repo": "../deployment"})[1]
    assert git_tools(GitTools()) == []

    gone = Toolbox(git_tools(GitTools(repos={"gone": str(tmp_path / "missing")})))
    assert gone.call("recent_commits", {"repo": "gone"})[0] == {
        "error": "git could not read this repository"
    }

    def no_git(argv: Any, **kwargs: Any) -> tuple[int, str]:
        raise FileNotFoundError("git")

    missing = Toolbox(git_tools(GitTools(repos={"deployment": str(repo)}), run=no_git))
    assert missing.call("recent_commits", {"repo": "deployment"})[0] == {
        "error": "git could not be run: FileNotFoundError"
    }


def test_git_never_runs_what_a_repository_configures(tmp_path: Path) -> None:
    repo = tmp_path / "hostile"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "a").write_text("a")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "one")
    marker = tmp_path / "ran"
    with (repo / ".git" / "config").open("a") as config:
        config.write(f'[core]\n\tfsmonitor = "touch {marker}"\n\tpager = "touch {marker}"\n')
    result, error = Toolbox(git_tools(GitTools(repos={"h": str(repo)}))).call(
        "recent_commits", {"repo": "h"}
    )
    assert not error
    assert result["count"] == 1
    assert not marker.exists()


SSH = {
    "identity_file": "/keys/paeon",
    "known_hosts_file": "/keys/known_hosts",
    "hosts": {"nas": {"target": "paeon@nas.example.org", "commands": {"disk": "df -h /volume1"}}},
}


def test_host_command_runs_the_owners_command_by_name() -> None:
    ran: list[list[str]] = []

    def run(argv: Any, **kwargs: Any) -> tuple[int, str]:
        ran.append(list(argv))
        return 0, "Filesystem  Use%\n/volume1  91%\n"

    toolbox = Toolbox(ssh_tools(SshTools.model_validate(SSH), run=run))
    result, error = toolbox.call("host_command", {"host": "nas", "command": "disk"})
    assert not error
    assert result == {"exit_code": 0, "lines": ["Filesystem  Use%", "/volume1  91%"]}
    argv = ran[0]
    assert argv[0] == "ssh"
    assert argv[-3:] == ["--", "paeon@nas.example.org", "df -h /volume1"]
    for option in ("BatchMode=yes", "StrictHostKeyChecking=yes", "ForwardAgent=no"):
        assert option in argv
    assert "UserKnownHostsFile=/keys/known_hosts" in argv

    # The model chooses among names. A command line of its own is not a name.
    for args in (
        {"host": "nas", "command": "rm -rf /"},
        {"host": "nas", "command": "disk; id"},
        {"host": "router", "command": "disk"},
        {"host": "nas"},
    ):
        assert toolbox.call("host_command", args)[1]
    assert len(ran) == 1

    def unreachable(argv: Any, **kwargs: Any) -> tuple[int, str]:
        raise subprocess.TimeoutExpired(argv, 20)

    down = Toolbox(ssh_tools(SshTools.model_validate(SSH), run=unreachable))
    assert down.call("host_command", {"host": "nas", "command": "disk"})[0] == {
        "error": "the host was not reached: TimeoutExpired"
    }
    assert ssh_tools(SshTools.model_validate({**SSH, "hosts": {}})) == []


@pytest.mark.parametrize(
    "hosts",
    [
        {"nas": {"target": "nas.example.org", "commands": {"disk": "df"}}},  # no user
        {"nas": {"target": "u@h -oProxyCommand=x", "commands": {"disk": "df"}}},
        {"nas": {"target": "u@h", "commands": {}}},
        {"nas": {"target": "u@h", "commands": {"a b": "df"}}},
        {"-oProxyCommand": {"target": "u@h", "commands": {"disk": "df"}}},
    ],
)
def test_ssh_rules_refuse_what_is_not_a_plain_name(hosts: dict[str, Any]) -> None:
    with pytest.raises(RulesError):
        parse_rules({"investigation": {"tools": {"ssh": {**SSH, "hosts": hosts}}}})


def test_the_knowledge_index_follows_the_files(tmp_path: Path) -> None:
    index = knowledge.Index()
    try:
        note = tmp_path / "2026-11-02-proxy-outage.md"
        note.write_text(
            "# Proxy outage\n\nIntro.\n\n## Root cause\n\nThe certificates expired on Sunday.\n\n"
            "## Fix\n\nRenewed by hand.\n"
        )
        (tmp_path / "big.md").write_text("certificates " * 60_000)
        (hit,) = index.search([tmp_path], "expired certificate")  # stemmed: certificate(s)
        assert (hit.path, hit.title, hit.heading) == (note.name, "Proxy outage", "Root cause")
        assert "expired on Sunday" in hit.excerpt
        assert index.search([tmp_path], "2026-11-02")[0].path == note.name  # the file name counts

        note.write_text("# Proxy outage\n\nNow about DNS only.\n")
        assert index.search([tmp_path], "certificates") == []
        assert index.search([tmp_path], "dns")[0].heading == "Proxy outage"
        note.unlink()
        assert index.search([tmp_path], "dns") == []
        assert index.search([], "dns") == []
    finally:
        index.close()
    assert knowledge.sections("no heading\n") == [("", "no heading")]
    assert knowledge.sections("# A\n\n## B\n\ntext") == [("A", ""), ("B", "text")]


@pytest.mark.parametrize(
    "sql",
    [
        'SELECT * FROM "traefik", finance',  # a comma join
        'SELECT * FROM "traefik" t, finance f',
        'SELECT * FROM traefik AS t , "finance"',
        'SELECT * FROM"finance"',  # no space before the name
        'SELECT * FROM "traefik" -- JOIN x\nJOIN finance ON 1 = 1',
        'SELECT * FROM "traefik" /* */ JOIN finance ON 1 = 1',
        'SELECT 1 FROM "traefik"; SELECT * FROM finance',
        'SELECT * FROM "my-stream"',  # a name this reader cannot hold
        "SELECT * FROM `finance`",
        "SELECT extract(hour FROM ts) FROM traefik",  # `ts` reads as a table: refused
        "SELECT * FROM traefik WHERE msg = 'it''s",  # an unterminated string
        "SELECT 1",
        # A quote inside a quoted identifier must not open a string that hides a join.
        'SELECT * FROM "traefik" WHERE "a\'" = 1 JOIN finance ON "b\'" = 2',
        "SELECT * FROM traefik WHERE m = E'\\' JOIN finance ON m = '",
        "SELECT * FROM traefik.finance",  # a qualified name
        'SELECT * FROM traefik."finance"',
        'SELECT * FROM traefik"finance"',
        "SELECT * FROM traefik(1)",  # a table function
        "SELECT * FROM (SELECT 1 FROM traefik) t, finance",  # a comma join after a sub-query
        "SELECT * FROM traefik JOIN a ON a.x = 1, finance",
        "SELECT * FROM traefik UNION SELECT * FROM finance",
        "SELECT * FROM traefik)",
        "SELECT * FROM (SELECT * FROM traefik",
        "SELECT * FROM traefik WHERE x IN (SELECT y FROM finance)",
        "SELECT 1FROM finance",  # a number that runs into a keyword
        "SELECT * FROM traefik WHERE x = 1e5JOIN finance ON 1 = 1",
        "SELECT * FROM traefik t JOIN traefik u ON t.where = 1, finance",  # a column named where
        "SELECT * FROM traefik t JOIN traefik u ON t.limit = u.order, finance f",
        "SELECT * FROM traefik\u00a0JOIN finance ON 1 = 1",  # not a space to this reader
        "SELECT * FROM tr\u00e6fik",
    ],
)
def test_sql_that_cannot_be_accounted_for_is_never_rerun(sql: str) -> None:
    streams = sql_streams(sql)
    assert streams is None or not streams <= {"traefik"}  # unknown, or more than what is listed
    hidden = {**V2, "query_condition": {"type": "sql", "sql": sql}}
    toolbox, telemetry = alert_query(hidden, OpenObserveTools(streams=("traefik",)))
    result, _ = toolbox.call("alert_query", {"alert": "x"})
    assert "not_run" in result
    assert telemetry.sql == []


def test_sql_streams_reads_plain_queries() -> None:
    assert sql_streams('SELECT count(*) FROM "traefik" WHERE s >= 500') == {"traefik"}
    assert sql_streams("select a from traefik where m = 'from finance, x'") == {"traefik"}
    assert sql_streams("SELECT * FROM a JOIN b ON a.x = b.x") == {"a", "b"}
    assert sql_streams("SELECT * FROM (SELECT x FROM inner_one) AS t") == {"inner_one"}
    assert sql_streams("SELECT a, b FROM traefik t WHERE f(a, 1) > 2 GROUP BY a, b") == {"traefik"}
    assert sql_streams("SELECT a FROM x UNION SELECT a, b FROM y ORDER BY a, b") == {"x", "y"}
    assert sql_streams("SELECT t.from, 1.5e3 FROM traefik t WHERE t.limit > 10") == {"traefik"}
    assert sql_streams('select "a" from "traefik" as t where "m" = \'x, y\'') == {"traefik"}
