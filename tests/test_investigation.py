"""The investigation loop with a scripted model and fake tools."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from paeon.correlation import Correlator
from paeon.investigation import SUBMIT, SYSTEM_PROMPT, Investigator, render
from paeon.models import ModelError, ModelReply, ToolCall, ToolSpec, ToolTurn, Turn, UserTurn
from paeon.recorder import Recorder
from paeon.redact import MASK
from paeon.rules import Rules, parse_rules
from paeon.schema import StageState, Status
from paeon.store import Store
from paeon.tools import Tool, Toolbox
from support import T0, alert

LATER = T0 + timedelta(minutes=5)
FINDINGS: dict[str, Any] = {
    "summary": "The API container is OOM killed in a loop since the 07:55 deploy.",
    "impact": "The API is down for all clients since 07:58.",
    "root_cause": {"statement": "Version 2.1 exceeds the 256 MB memory limit.", "confidence": 0.85},
    "evidence": [
        {"claim": "The container was OOM killed 7 times.", "call": "c1"},
        {"claim": "Version 2.1 was deployed at 07:55.", "call": "c2"},
        {"claim": "The disk is fine.", "call": "c9"},
    ],
    "hypotheses_rejected": [{"hypothesis": "Database down", "reason": "db is running"}],
    "plan": [
        {"action": "Roll back the API to 2.0", "why": "restores service", "risk": "low"},
        {"action": "Raise the memory limit", "why": "if 2.1 is needed", "risk": "medium"},
    ],
}


def rules(postmortems: bool = False, **investigation: Any) -> Rules:
    document: dict[str, Any] = {
        "models": {
            "investigate": {
                "backend": "anthropic",
                "model": "claude-opus-5-5",
                "price": {"input_per_mtok": 4.0, "output_per_mtok": 20.0},
            },
            "triage": {"backend": "openai", "model": "auto"},
        },
        "investigation": {"enabled": True, **investigation},
    }
    if postmortems:
        document["models"]["postmortem"] = document["models"]["investigate"]
        document["postmortems"] = {"enabled": True}
    return parse_rules(document)


def calls(*items: tuple[str, dict[str, Any]], text: str = "") -> ModelReply:
    return ModelReply(
        text=text,
        model="answered-by",
        input_tokens=10_000,
        output_tokens=1_000,
        tool_calls=tuple(ToolCall(f"id{i}", name, args) for i, (name, args) in enumerate(items)),
        raw="raw",
    )


class Scripted:
    def __init__(self, *script: ModelReply | Exception) -> None:
        self.script = list(script)
        self.seen: list[tuple[str, list[Turn], list[str]]] = []

    def converse(
        self, *, system: str, turns: Sequence[Turn], tools: Sequence[ToolSpec] = ()
    ) -> ModelReply:
        self.seen.append((system, list(turns), [tool.name for tool in tools]))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def toolbox(log: list[tuple[str, dict[str, Any]]]) -> Toolbox:
    def make(name: str, result: Any) -> Tool:
        def run(args: dict[str, Any]) -> Any:
            log.append((name, args))
            return result

        return Tool(ToolSpec(name, "d", {"type": "object", "properties": {}}), run)

    return Toolbox(
        [
            make(
                "inspect_container",
                {"state": {"oomkilled": True}, "env_hint": "token=abcdef123456"},
            ),
            make("recent_changes", [{"kind": "deploy", "title": "api 2.1"}]),
        ]
    )


class Setup:
    def __init__(self, store: Store, model: Scripted, config: Rules | None = None) -> None:
        self.config = config or rules()
        self.tool_log: list[tuple[str, dict[str, Any]]] = []
        self.published: list[dict[str, Any]] = []
        self.recorder = Recorder(store, Correlator(store, self.config))
        self.investigator = Investigator(
            store,
            self.config,
            model,
            toolbox(self.tool_log),
            ntfy_token="not-a-real-token",
            publish=self.publish,
        )

    def publish(self, ntfy: Any, token: str | None, **message: Any) -> None:
        self.published.append({"topic": ntfy.topic, "token": token, **message})


def kinds(store: Store) -> list[str]:
    skip = ("alert.", "incident.")
    return [e.kind for e in store.events() if not e.kind.startswith(skip)]


def test_investigation_runs_tools_and_records_findings(store: Store) -> None:
    model = Scripted(
        calls(
            ("inspect_container", {"name": "api"}),
            ("recent_changes", {"hours": 3}),
            text="Looking.",
        ),
        calls((SUBMIT, FINDINGS)),
    )
    setup = Setup(
        store, model, rules(ntfy={"url": "https://ntfy.example.org", "topic": "incidents"})
    )
    setup.recorder.observe(
        alert(Status.FIRING, 0, title="API down", description="password=hunter2pass")
    )
    assert setup.investigator.run_pending(LATER) == 1

    assert setup.tool_log == [
        ("inspect_container", {"name": "api"}),
        ("recent_changes", {"hours": 3}),
    ]
    assert kinds(store) == [
        "investigation.started",
        "model.call",
        "tool.call",
        "tool.call",
        "model.call",
        "investigation.completed",
        "notification.sent",
    ]
    started = next(iter(store.events(kind="investigation.started")))
    assert started.payload["tools"] == ["inspect_container", "recent_changes", SUBMIT]
    assert MASK in started.payload["input"]["alerts"][0]["description"]

    tool_1, tool_2 = (e.payload for e in store.events(kind="tool.call"))
    assert (tool_1["call"], tool_1["name"], tool_1["is_error"]) == (
        "c1",
        "inspect_container",
        False,
    )
    assert tool_1["result"] == {
        "state": {"oomkilled": True},
        "env_hint": f"token={MASK}",
    }  # full, redacted
    assert tool_2["call"] == "c2"

    # What the model was sent back: both results in one turn, as data, with the id to cite.
    _, turns, tool_names = model.seen[1]
    assert tool_names == ["inspect_container", "recent_changes", SUBMIT]
    assert isinstance(turns[0], UserTurn)
    assert isinstance(turns[2], ToolTurn)
    first, second = turns[2].results
    assert first.call_id == "id0"
    assert first.content.startswith('{"call": "c1", "untrusted_data": ')
    assert "abcdef123456" not in first.content
    assert '"call": "c2"' in second.content
    assert model.seen[0][0] == SYSTEM_PROMPT

    done = next(iter(store.events(kind="investigation.completed"))).payload
    assert done["findings"]["root_cause"]["confidence"] == 0.85
    assert [e["verified"] for e in done["findings"]["evidence"]] == [
        True,
        True,
        False,
    ]  # c9 never ran
    assert (done["model"], done["tool_calls"]) == ("answered-by", 2)
    assert done["cost_so_far"] == pytest.approx(0.12)  # two calls: 10k in at 4, 1k out at 20
    turns_recorded = [e.payload["turn"] for e in store.events(kind="model.call")]
    assert turns_recorded == [1, 2]

    incident = store.incidents()[0]
    assert incident.investigation is StageState.DONE
    assert incident.postmortem is StageState.OFF

    [message] = setup.published
    assert (message["topic"], message["token"], message["priority"]) == (
        "incidents",
        "not-a-real-token",
        3,
    )
    assert message["title"] == "Incident #1: API down"
    assert "Root cause (85% confident): Version 2.1 exceeds" in message["message"]
    assert "1. Roll back the API to 2.0 [low risk]" in message["message"]
    assert "3 claims, 1 without a tool call" in message["message"]
    assert setup.investigator.run_pending(LATER) == 0  # once per incident


def test_invalid_findings_are_sent_back_and_can_be_corrected(store: Store) -> None:
    model = Scripted(calls((SUBMIT, {"summary": "x"})), calls((SUBMIT, FINDINGS)))
    setup = Setup(store, model)
    setup.recorder.observe(alert(Status.FIRING, 0))
    setup.investigator.run_pending(LATER)
    turn = model.seen[1][1][-1]
    assert isinstance(turn, ToolTurn)
    assert turn.results[0].is_error
    assert "invalid findings: impact: Field required" in turn.results[0].content
    assert store.incidents()[0].investigation is StageState.DONE


def test_tool_budget_then_forced_submission(store: Store) -> None:
    model = Scripted(
        calls(("recent_changes", {}), ("recent_changes", {}), ("recent_changes", {})),
        calls((SUBMIT, FINDINGS)),
    )
    setup = Setup(store, model, rules(max_tool_calls=2))
    setup.recorder.observe(alert(Status.FIRING, 0))
    setup.investigator.run_pending(LATER)
    assert len(setup.tool_log) == 2
    turn = model.seen[1][1][-1]
    assert isinstance(turn, ToolTurn)
    assert [r.is_error for r in turn.results] == [False, False, True]
    assert "tool budget is spent" in turn.results[2].content
    assert store.incidents()[0].investigation is StageState.DONE


def test_unreadable_tool_arguments_are_an_error_result(store: Store) -> None:
    broken = ModelReply(
        text="",
        model="m",
        input_tokens=1,
        output_tokens=1,
        tool_calls=(
            ToolCall("id0", "recent_changes", {}, error="arguments are not a JSON object"),
        ),
        raw="raw",
    )
    model = Scripted(broken, calls((SUBMIT, FINDINGS)))
    setup = Setup(store, model)
    setup.recorder.observe(alert(Status.FIRING, 0))
    setup.investigator.run_pending(LATER)
    assert setup.tool_log == []
    turn = model.seen[1][1][-1]
    assert isinstance(turn, ToolTurn)
    assert turn.results[0].is_error


@pytest.mark.parametrize(
    ("script", "config", "reason"),
    [
        ([ModelError("HTTP 529")], {}, "model_error: HTTP 529"),
        ([calls(text="I think it is fine.")] * 3, {}, "no_findings"),
        ([calls(("recent_changes", {}))] * 5, {"max_tool_calls": 1}, "no_findings"),
        ([calls(("recent_changes", {}))] * 2, {"budget_per_incident": 0.05}, "budget"),
        ([], {"max_seconds": 1e-9}, "timeout"),
    ],
)
def test_failures_leave_the_incident_alone(
    store: Store, script: list[ModelReply | Exception], config: dict[str, Any], reason: str
) -> None:
    setup = Setup(store, Scripted(*script), rules(**config))
    setup.recorder.observe(alert(Status.FIRING, 0))
    setup.investigator.run_pending(LATER)
    [failed] = list(store.events(kind="investigation.failed"))
    assert failed.payload["reason"] == reason
    incident = store.incidents()[0]
    assert incident.investigation is StageState.FAILED
    assert incident.status.value == "open"
    assert setup.published == []


def test_a_text_only_answer_is_nudged_towards_submission(store: Store) -> None:
    model = Scripted(calls(text="All clear."), calls((SUBMIT, FINDINGS)))
    setup = Setup(store, model)
    setup.recorder.observe(alert(Status.FIRING, 0))
    setup.investigator.run_pending(LATER)
    nudge = model.seen[1][1][-1]
    assert isinstance(nudge, UserTurn)
    assert SUBMIT in nudge.text
    assert store.incidents()[0].investigation is StageState.DONE


def test_triage_decides_what_is_investigated(store: Store) -> None:
    config = parse_rules(
        {
            "models": {
                "investigate": {"backend": "anthropic", "model": "m"},
                "triage": {"backend": "openai", "model": "auto"},
            },
            "triage": {"enabled": True},
            "investigation": {"enabled": True},
        }
    )
    model = Scripted(calls((SUBMIT, FINDINGS)))
    setup = Setup(store, model, config)
    for name in ("noisy", "broken", "waiting"):
        setup.recorder.observe(alert(Status.FIRING, 0, name))
    # Triage is still pending everywhere: nothing is investigated yet.
    assert setup.investigator.run_pending(LATER) == 0
    for incident_id, verdict in ((1, "noise"), (2, "real")):
        store.append("triage.verdict", {"effective_verdict": verdict}, incident_id=incident_id)
        store.set_stage(incident_id, "triage", StageState.DONE)
    assert setup.investigator.run_pending(LATER) == 2
    noisy, broken, waiting = store.incidents()
    assert noisy.investigation is StageState.SKIPPED
    assert broken.investigation is StageState.DONE
    assert waiting.investigation is StageState.PENDING
    [skipped] = list(store.events(kind="investigation.skipped"))
    assert (skipped.incident_id, skipped.payload) == (1, {"skip": "triage: noise"})
    # The triage verdict is part of what the investigator is told.
    assert '"verdict": "real"' in model.seen[0][1][0].text  # type: ignore[union-attr]


def test_settle_time_storm_guard_and_daily_budget(store: Store) -> None:
    now = datetime.now(UTC)
    model = Scripted(calls((SUBMIT, FINDINGS)))
    setup = Setup(store, model, rules(max_per_hour=1, settle_seconds=60))
    setup.recorder.observe(alert(Status.FIRING, 0, "a"))
    assert setup.investigator.run_pending(T0 + timedelta(seconds=30)) == 0
    setup.recorder.observe(alert(Status.FIRING, 0, "b"))
    assert setup.investigator.run_pending(now) == 2
    assert [e.payload for e in store.events(kind="investigation.skipped")] == [{"skip": "storm"}]

    budget = Setup(store, Scripted(), rules(daily_budget=0.01))
    budget.recorder.observe(alert(Status.FIRING, 0, "c"))
    budget.investigator.run_pending(now)  # the first investigation cost 0.06 today
    assert [e.payload for e in store.events(kind="investigation.skipped")][-1] == {"skip": "budget"}


def test_a_lost_notification_is_recorded_not_raised(store: Store) -> None:
    setup = Setup(
        store,
        Scripted(calls((SUBMIT, FINDINGS))),
        rules(ntfy={"url": "https://ntfy.example.org", "topic": "incidents"}),
    )

    def failing(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("ntfy answered HTTP 503")

    setup.investigator._publish = failing
    setup.recorder.observe(alert(Status.FIRING, 0))
    setup.investigator.run_pending(LATER)
    [failed] = list(store.events(kind="notification.failed"))
    assert failed.payload["error"] == "ntfy answered HTTP 503"
    assert store.incidents()[0].investigation is StageState.DONE


def test_postmortem_is_queued_when_enabled(store: Store) -> None:
    setup = Setup(store, Scripted(calls((SUBMIT, FINDINGS))), rules(postmortems=True))
    setup.recorder.observe(alert(Status.FIRING, 0))
    setup.investigator.run_pending(LATER)
    assert store.incidents()[0].postmortem is StageState.PENDING


def test_render_without_a_plan() -> None:
    from paeon.investigation import Findings

    findings = Findings.model_validate({**FINDINGS, "plan": [], "evidence": []})
    text = render(findings)
    assert "Suggested" not in text
    assert "Evidence: 0 claims" in text
