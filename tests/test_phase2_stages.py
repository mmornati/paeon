"""What phase 2 gained in its stages: a fallback model, typed plan steps, triage that reads
past postmortems, drafts committed to git, and the record served over MCP."""

from __future__ import annotations

import io
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from acting import ACTIONS
from paeon import mcp, proc
from paeon import postmortem as postmortems
from paeon.cli import main
from paeon.correlation import Correlator
from paeon.investigation import SUBMIT, Extras, Findings, Investigator, catalog_prompt
from paeon.models import ModelError
from paeon.postmortem import PostmortemWriter
from paeon.recorder import Recorder
from paeon.rules import Rules, parse_rules
from paeon.schema import StageState, Status
from paeon.store import Store
from paeon.triage import Triager
from support import T0, alert
from test_investigation import FINDINGS, Scripted, Setup, calls, toolbox
from test_investigation import rules as investigation_rules
from test_postmortem import NARRATIVE, Writer, investigated_incident
from test_postmortem import rules as postmortem_rules
from test_triage import Scripted as TriageModel
from test_triage import answer
from test_triage import rules as triage_rules

LATER = T0 + timedelta(minutes=5)


# --- investigation --------------------------------------------------------------------------


def test_a_declining_model_is_replaced_once_from_the_start(store: Store) -> None:
    config = investigation_rules(fallback_model="triage")
    first = Scripted(calls(("recent_changes", {})), ModelError("the model declined the request"))
    second = Scripted(calls(("inspect_container", {"name": "api"})), calls((SUBMIT, FINDINGS)))
    setup = Setup(store, first, config)
    setup.investigator = Investigator(
        store, config, first, toolbox(setup.tool_log), fallback=second
    )
    setup.recorder.observe(alert(Status.FIRING, 0, title="API down"))
    assert setup.investigator.run_pending(LATER) == 1
    assert store.incident(1).investigation is StageState.DONE  # type: ignore[union-attr]
    switch = next(store.events(kind="investigation.fallback")).payload
    assert switch == {
        "from": "investigate",
        "to": "triage",
        "reason": "model_error: the model declined the request",
    }
    # The second model starts from the context pack, not from the first one's turns.
    assert len(second.seen[0][1]) == 1
    # Tool calls are numbered across both, and the ledger names each alias.
    assert [e.payload["call"] for e in store.events(kind="tool.call")] == ["c1", "c2"]
    assert [e.payload["alias"] for e in store.events(kind="model.call")] == [
        "investigate",
        "investigate",
        "triage",
        "triage",
    ]


def test_without_a_fallback_or_when_both_fail_the_investigation_fails(store: Store) -> None:
    config = investigation_rules(fallback_model="triage")
    both = Setup(store, Scripted(ModelError("HTTP 529")), config)
    both.investigator = Investigator(
        store,
        config,
        Scripted(ModelError("HTTP 529")),
        toolbox([]),
        fallback=Scripted(ModelError("HTTP 500")),
    )
    both.recorder.observe(alert(Status.FIRING, 0, title="API down"))
    both.investigator.run_pending(LATER)
    assert next(store.events(kind="investigation.failed")).payload["reason"] == (
        "model_error: HTTP 500"
    )
    assert store.incident(1).investigation is StageState.FAILED  # type: ignore[union-attr]


def acting_rules() -> Rules:
    return parse_rules(
        {
            "models": {"investigate": {"backend": "anthropic", "model": "claude-opus-5-5"}},
            "investigation": {"enabled": True},
            "actions": ACTIONS,
        }
    )


def typed(target: str, action: str = "restart_container") -> dict[str, Any]:
    step = {"action": "Restart it", "why": "", "risk": "low"}
    return {**FINDINGS, "plan": [{**step, "catalog_action": action, "target": target}]}


def test_a_typed_step_must_be_in_the_catalog_before_anyone_sees_it(store: Store) -> None:
    config = acting_rules()
    model = Scripted(
        calls((SUBMIT, typed("media-db"))),  # denied by the catalog
        calls((SUBMIT, typed("worker", "delete_volume"))),  # not an action
        calls((SUBMIT, typed("worker; rm -rf /"))),  # not a name
        calls((SUBMIT, typed("worker"))),
    )
    handed: list[tuple[int, Findings]] = []
    setup = Setup(store, model, config)
    setup.investigator = Investigator(
        store,
        config,
        model,
        toolbox([]),
        after=lambda incident, findings: handed.append((incident.id, findings)),
    )
    setup.recorder.observe(alert(Status.FIRING, 0, title="worker_down"))
    setup.investigator.run_pending(LATER)

    system, _, tools = model.seen[0]
    assert "restart_container: restart a container; containers: worker, vpn, media-*" in system
    assert "never: media-db" in system
    assert tools[-1] == SUBMIT
    refusals = [turn.results[0].content for _, turns, _ in model.seen[1:] for turn in turns[-1:]]  # type: ignore[union-attr]
    assert "restart_container is not allowed on 'media-db'" in refusals[0]
    assert "catalog_action must be one of" in refusals[1]
    assert "catalog_action must be one of" in refusals[2]
    ((incident_id, findings),) = handed
    assert incident_id == 1
    assert findings.plan[0].typed
    done = next(store.events(kind="investigation.completed")).payload["findings"]
    assert done["plan"][0]["catalog_action"] == "restart_container"


def test_with_actions_off_a_typed_step_is_only_its_sentence(store: Store) -> None:
    model = Scripted(calls((SUBMIT, typed("worker"))))
    setup = Setup(store, model)
    setup.recorder.observe(alert(Status.FIRING, 0, title="worker_down"))
    setup.investigator.run_pending(LATER)
    plan = next(store.events(kind="investigation.completed")).payload["findings"]["plan"]
    assert plan[0]["catalog_action"] is None
    assert plan[0]["target"] is None
    assert "catalog" not in model.seen[0][0].lower()
    assert "catalog_action" not in json.dumps(Investigator.__init__.__defaults__, default=str)
    assert catalog_prompt({}).strip().endswith("addresses the cause.")


def test_the_analysis_carries_buttons_and_survives_their_failure(store: Store) -> None:
    ntfy = {"url": "https://ntfy.example.org", "topic": "incidents"}
    config = investigation_rules(ntfy=ntfy)
    button = {"action": "http", "label": "Cause is right", "url": "https://x", "method": "POST"}

    def boom(*args: Any) -> Any:
        raise RuntimeError("the executor is down")

    for position, (extras, after) in enumerate(
        (
            (
                lambda incident: Extras(click="https://paeon.example.org/ui", actions=(button,)),
                boom,
            ),
            (boom, None),
        )
    ):
        model = Scripted(calls((SUBMIT, FINDINGS)))
        setup = Setup(store, model, config)
        setup.investigator = Investigator(
            store,
            config,
            model,
            toolbox([]),
            publish=setup.publish,
            extras=extras,
            after=after,
        )
        setup.recorder.observe(
            alert(Status.FIRING, position * 60, f"a{position}", labels={"host": f"h{position}"})
        )
        setup.investigator.run_pending(LATER + timedelta(minutes=position * 60))
        (sent,) = setup.published
        assert sent["click"] == ("https://paeon.example.org/ui" if position == 0 else None)
        assert len(sent["actions"]) == (1 if position == 0 else 0)
    # Neither a failing hand-over nor missing buttons cost the analysis.
    assert len(list(store.events(kind="investigation.completed"))) == 2


# --- triage ---------------------------------------------------------------------------------


def test_triage_is_given_the_past_postmortems_that_match(store: Store, tmp_path: Path) -> None:
    (tmp_path / "2026-10-01-disk-full.md").write_text(
        "# Disk full on server-1\n\n## Root cause\n\nThe backup filled /srv. token=abcdef123456\n"
    )
    (tmp_path / "unrelated.md").write_text("# Wifi\n\nThe access point rebooted.\n")
    model = TriageModel(answer("known_issue", 0.9))
    config = triage_rules()
    recorder = Recorder(store, Correlator(store, config))
    triager = Triager(store, config, model, knowledge_paths=[tmp_path])
    recorder.observe(alert(Status.FIRING, 0, title="Disk full", labels={"host": "server-1"}))
    triager.run_pending(LATER)
    pack = json.loads(model.calls[0][1])
    (past,) = pack["past_postmortems"]
    assert past["path"] == "2026-10-01-disk-full.md"
    assert "[REDACTED]" in past["excerpt"]
    assert "abcdef123456" not in model.calls[0][1]
    assert pack["not_available"] == ["the alert's query result"]
    assert "matched_postmortem" in model.calls[0][0]


# --- postmortems ----------------------------------------------------------------------------


def writer_rules(directory: Path, **git: Any) -> Rules:
    document = postmortem_rules(directory).model_dump(mode="json")
    document["postmortems"]["git"] = git
    return parse_rules(document)


def test_a_draft_is_committed_and_pushed_with_the_deploy_key(store: Store, tmp_path: Path) -> None:
    config = writer_rules(
        tmp_path, push=True, identity_file="/keys/deploy", known_hosts_file="/keys/known_hosts"
    )
    investigated_incident(store, config)
    ran: list[tuple[list[str], dict[str, str]]] = []

    def run(argv: Any, **kwargs: Any) -> tuple[int, str]:
        ran.append((list(argv), dict(kwargs["env"])))
        return 0, ""

    writer = PostmortemWriter(store, config, Writer(json.dumps(NARRATIVE)), run=run)
    assert writer.run_pending(T0 + timedelta(hours=2)) == 1
    verbs = [argv[argv.index(str(tmp_path)) + 1] for argv, _ in ran]
    assert verbs == ["add", "commit", "push"]
    assert ran[0][0][-2:] == ["--", "2026-11-18-api-down.md"]
    env = ran[2][1]
    assert env["GIT_AUTHOR_NAME"] == "Paeon"
    assert "-i /keys/deploy" in env["GIT_SSH_COMMAND"]
    assert "StrictHostKeyChecking=yes" in env["GIT_SSH_COMMAND"]
    assert next(store.events(kind="postmortem.committed")).payload == {
        "path": "2026-11-18-api-down.md",
        "pushed": True,
    }


def test_a_failed_commit_keeps_the_draft(store: Store, tmp_path: Path) -> None:
    config = writer_rules(tmp_path)
    investigated_incident(store, config)

    def run(argv: Any, **kwargs: Any) -> tuple[int, str]:
        if "commit" in argv:
            raise FileNotFoundError("git")
        return 0, ""

    PostmortemWriter(store, config, Writer(json.dumps(NARRATIVE)), run=run).run_pending(
        T0 + timedelta(hours=2)
    )
    assert next(store.events(kind="postmortem.commit_failed")).payload == {
        "step": "commit (FileNotFoundError)"
    }
    assert (tmp_path / "2026-11-18-api-down.md").exists()
    assert store.incident(1).postmortem is StageState.DONE  # type: ignore[union-attr]

    off = writer_rules(tmp_path / "off", commit=False)
    assert off.postmortems.git is not None
    writer = PostmortemWriter(store, off, Writer("x"), run=run)
    writer._commit(store.incident(1), tmp_path / "x.md")  # type: ignore[arg-type]
    assert len(list(store.events(kind="postmortem.commit_failed"))) == 1


def test_a_real_commit_lands_in_the_working_copy(store: Store, tmp_path: Path) -> None:
    code, _ = proc.run(["git", "init", "--quiet", str(tmp_path)], env=proc.GIT_ENV)
    assert code == 0
    config = writer_rules(tmp_path)
    investigated_incident(store, config)
    PostmortemWriter(store, config, Writer(json.dumps(NARRATIVE))).run_pending(
        T0 + timedelta(hours=2)
    )
    code, log = proc.run([*proc.GIT, "-C", str(tmp_path), "log", "--format=%an|%s"])
    assert log.strip() == "Paeon|Postmortem draft: incident 1"


def test_drafts_are_listed_with_their_status_and_open_items(store: Store, tmp_path: Path) -> None:
    config = postmortem_rules(tmp_path)
    investigated_incident(store, config)
    store.append(
        "plan.proposed", {"level": "approve", "steps": ["restart_container(api)"]}, incident_id=1
    )
    store.append("plan.completed", {"outcome": "verified"}, incident_id=1)
    PostmortemWriter(store, config, Writer(json.dumps(NARRATIVE))).run_pending(
        T0 + timedelta(hours=2)
    )
    (draft,) = postmortems.drafts(tmp_path)
    assert (draft.status, draft.incident, draft.severity) == ("draft", 1, "warning")
    assert draft.title == NARRATIVE["title"]
    assert len(draft.open_items) == len(NARRATIVE["action_items"])
    found = postmortems.read_draft(tmp_path, draft.name)
    assert found is not None
    text = found[1]
    assert "autonomy: approve" in text
    assert "- Plan proposed at level `approve`: restart_container(api)." in text
    assert "- Executed: verified." in text
    assert "Plan verified" in text  # the timeline has the executor's part

    reviewed = text.replace("status: draft", "status: reviewed").replace("- [ ]", "- [x]")
    (tmp_path / draft.name).write_text(reviewed)
    assert postmortems.drafts(tmp_path)[0].status == "reviewed"
    assert postmortems.drafts(tmp_path)[0].open_items == ()
    (tmp_path / "notes.md").write_text("---\n: : :\n---\nno title")
    assert postmortems.read_draft(tmp_path, "notes.md")[0].title == "notes.md"  # type: ignore[index]
    for name in ("../paeon.sqlite", "missing.md", "/etc/passwd", ".hidden.md", "a/b.md"):
        assert postmortems.read_draft(tmp_path, name) is None
    assert postmortems.drafts(tmp_path / "missing") == []


# --- MCP and the command line ---------------------------------------------------------------


def ask(store: Store, rules: Rules, *messages: Any) -> list[dict[str, Any]]:
    stdin = io.StringIO("\n".join(json.dumps(m) if not isinstance(m, str) else m for m in messages))
    stdout = io.StringIO()
    mcp.serve(store, rules, stdin, stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def call(number: int, tool: str, **arguments: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": number,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }


def test_the_record_is_served_read_only_over_mcp(store: Store, tmp_path: Path) -> None:
    config = postmortem_rules(tmp_path)
    investigated_incident(store, config)
    store.append("feedback.recorded", {"vote": "up", "note": "token=abcdef123456"}, incident_id=1)
    PostmortemWriter(store, config, Writer(json.dumps(NARRATIVE))).run_pending(
        T0 + timedelta(hours=2)
    )
    name = postmortems.drafts(tmp_path)[0].name
    replies = ask(
        store,
        config,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        "",
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        call(3, "list_incidents", text="api", days=3650, status="resolved"),
        call(4, "get_incident", id=1),
        call(5, "search_knowledge", query="API unreachable"),
        call(6, "list_postmortems"),
        call(7, "read_postmortem", name=name),
        call(8, "read_postmortem", name="../paeon.sqlite"),
        call(9, "get_incident", id="one"),
        call(10, "delete_everything"),
        {"jsonrpc": "2.0", "id": 11, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": 12, "method": "ping"},
        "{not json",
        call(13, "list_incidents", text="nothing like it", status="open"),
        call(14, "get_incident", id=99),
    )
    by_id = {reply["id"]: reply for reply in replies}
    assert len(replies) == 15  # the notification got no answer
    assert by_id[1]["result"]["protocolVersion"] == mcp.PROTOCOL_VERSIONS[0]
    assert by_id[1]["result"]["serverInfo"]["name"] == "paeon"
    tools = by_id[2]["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "list_incidents",
        "get_incident",
        "search_knowledge",
        "list_postmortems",
        "read_postmortem",
    ]
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools)

    def text(number: int) -> Any:
        result = by_id[number]["result"]
        assert not result["isError"]
        return json.loads(result["content"][0]["text"])

    assert text(3)[0]["title"] == "API down"
    incident = text(4)
    assert incident["incident"]["status"] == "resolved"
    assert any("Investigation completed" in line for line in incident["timeline"])
    assert "abcdef123456" not in json.dumps(incident)  # redacted like everything a model reads
    assert text(5)[0]["path"] == name
    assert text(6)[0]["status"] == "draft"
    assert text(7)["text"].startswith("---\n")
    assert by_id[8]["result"]["isError"]
    assert by_id[9]["result"] == {
        "content": [{"type": "text", "text": "invalid arguments"}],
        "isError": True,
    }
    assert by_id[10]["error"]["code"] == -32602
    assert by_id[11]["error"]["code"] == -32601
    assert by_id[12]["result"] == {}
    assert by_id[None]["error"]["code"] == -32700
    assert text(13) == []
    assert by_id[14]["result"]["content"][0]["text"] == "no such incident"
    assert (
        mcp.handle(store, config, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})[  # type: ignore[index]
            "result"
        ]["protocolVersion"]
        == mcp.PROTOCOL_VERSIONS[0]
    )
    assert mcp.handle(store, config, ["not", "a", "request"]) is None


def test_cli_switches_write_the_owners_decisions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db = str(tmp_path / "executor.sqlite")
    assert main(["stop", "--db", db]) == 1  # no executor database there
    assert "no executor database" in capsys.readouterr().err
    Store(db).close()
    assert main(["stop", "--db", db]) == 0
    assert main(["resume", "--db", db]) == 0
    assert main(["reinstate", "worker_stalled", "restart_container", "worker", "--db", db]) == 0
    store = Store(db)
    try:
        events = [(e.kind, e.payload) for e in store.events()]
    finally:
        store.close()
    assert events == [
        ("killswitch.engaged", {"via": "cli"}),
        ("killswitch.released", {"via": "cli"}),
        (
            "autonomy.reinstated",
            {
                "alert": "worker_stalled",
                "action": "restart_container",
                "target": "worker",
                "via": "cli",
            },
        ),
    ]


def test_cli_autonomy_and_mcp(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db = str(tmp_path / "paeon.sqlite")
    rules_file = tmp_path / "paeon.yaml"
    rules_file.write_text(
        "actions:\n  enabled: true\n  autonomy: {promote_after: 2}\n"
        "  catalog: {restart_container: {targets: [worker]}}\n"
        "  policies: [{level: approve}]\n"
    )
    monkeypatch.setenv("PAEON_CONFIG", str(rules_file))
    assert main(["autonomy", "--db", db]) == 0
    assert capsys.readouterr().out == "No action has run yet.\n"
    store = Store(db)
    try:
        worker = ["worker_down", "restart_container", "worker"]
        for _ in range(2):
            store.append("plan.completed", {"outcome": "verified", "tuples": [worker]})
        store.append("killswitch.engaged", {"via": "link"})
        store.append(
            "autonomy.demoted",
            {"alert": "x", "action": "restart_container", "target": "worker", "reason": "undo"},
        )
    finally:
        store.close()
    assert main(["autonomy", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "The kill switch is engaged" in out
    assert "restart_container(worker) on worker_down: approve; 2 verified of 2" in out
    assert 'earned, add under actions.policies: - { alert: "worker_down"' in out
    assert "demoted: undo" in out

    monkeypatch.setattr("sys.stdin", io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n'))
    assert main(["mcp", "--db", db]) == 0
    assert json.loads(capsys.readouterr().out) == {"jsonrpc": "2.0", "id": 1, "result": {}}

    rules_file.write_text("actions: {catalog: {reboot: {targets: [x]}}}\n")
    assert main(["autonomy", "--db", db]) == 2
    assert "is not an action" in capsys.readouterr().err
    monkeypatch.delenv("PAEON_CONFIG")
    monkeypatch.delenv("PAEON_EXECUTOR_TOKEN", raising=False)
    assert main(["executor"]) == 2
    assert "PAEON_EXECUTOR_TOKEN" in capsys.readouterr().err
