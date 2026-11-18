"""Replay: what was recorded, asked again of the model as it is today."""

from __future__ import annotations

import io
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from paeon import replay
from paeon.cli import main
from paeon.correlation import Correlator
from paeon.investigation import SUBMIT
from paeon.models import ModelError
from paeon.recorder import Recorder
from paeon.schema import Status
from paeon.store import Store
from paeon.triage import Triager
from support import T0, alert
from test_investigation import FINDINGS, Scripted, Setup, calls
from test_triage import Scripted as TriageModel
from test_triage import answer
from test_triage import rules as triage_rules

LATER = T0 + timedelta(minutes=5)


def triaged(store: Store) -> None:
    """Four incidents: rated right (real), rated right (noise), rated wrong, not rated; and
    one that failed open, which has no verdict to compare with."""
    config = triage_rules()
    model = TriageModel(
        answer("real", 0.9), answer("noise", 0.9), answer("noise", 0.9), answer("transient", 0.9),
        ModelError("HTTP 500"),
    )  # fmt: skip
    recorder = Recorder(store, Correlator(store, config))
    triager = Triager(store, config, model)
    for number in range(1, 6):
        labels = {"host": f"h{number}"}
        recorder.observe(
            alert(Status.FIRING, number, f"a{number}", title=f"m{number}", labels=labels)
        )
    triager.run_pending(T0 + timedelta(hours=1))
    for incident, vote in ((1, "up"), (2, "up"), (3, "down")):
        store.append("feedback.recorded", {"about": "triage", "vote": vote}, incident_id=incident)
    store.append("feedback.recorded", {"about": "root_cause", "vote": "down"}, incident_id=1)


def test_triage_cases_carry_the_recorded_pack_verdict_and_rating(store: Store) -> None:
    triaged(store)
    cases = replay.triage_cases(store)
    assert [(c.incident_id, c.verdict, c.label) for c in cases] == [
        (1, "real", "right"),
        (2, "noise", "right"),
        (3, "noise", "wrong"),
        (4, "transient", None),
    ]
    assert cases[0].pack["incident"]["title"] == "m1"
    assert replay.triage_cases(store, since=T0 + timedelta(days=400)) == []


def test_a_rated_right_verdict_that_changes_is_a_regression(store: Store) -> None:
    triaged(store)
    cases = replay.triage_cases(store)
    config = triage_rules()
    alias = config.models["triage"]
    # Today's model: calls the real one noise, keeps the noise, fixes the wrong one, and
    # answers nonsense twice on the last.
    model = TriageModel(
        answer("noise", 0.8), answer("noise", 0.95), answer("real", 0.7), "no idea", "still none"
    )
    before = len(list(store.events()))
    results = replay.replay_triage(cases, model, alias)
    assert len(list(store.events())) == before  # a replay writes nothing
    assert [(r.verdict, r.same, r.regression, r.missed) for r in results] == [
        ("noise", False, True, True),
        ("noise", True, False, False),
        ("real", False, False, False),
        (None, False, False, False),
    ]
    assert results[3].error == "invalid_output"
    assert model.calls[0][1] == json.dumps(cases[0].pack, indent=1, sort_keys=True)
    report = replay.report_triage(results)
    assert report.failed
    assert report.lines[0] == "MISSED     #1 m1: real -> noise, you said right"
    assert report.lines[1].startswith("ok ")
    assert report.lines[2] == "changed    #3 m3: noise -> real, you said wrong"
    assert "no verdict (invalid_output)" in report.lines[3]
    assert report.lines[-2:] == [
        "4 verdicts replayed: 1 unchanged, 3 changed.",
        "Of the 2 you rated right, 1 still are; 1 would now be called noise.",
    ]
    assert report.cost > 0

    down = replay.replay_triage(cases[:1], TriageModel(ModelError("HTTP 529")), alias)
    assert (down[0].verdict, down[0].error, down[0].regression) == (None, "HTTP 529", True)
    same = replay.report_triage(
        replay.replay_triage(cases[1:2], TriageModel(answer("noise", 0.9)), alias)
    )
    assert not same.failed


def investigated(store: Store, findings: dict[str, Any] = FINDINGS) -> Setup:
    model = Scripted(
        calls(("inspect_container", {"name": "api"}), ("recent_changes", {"hours": 3})),
        calls((SUBMIT, findings)),
    )
    setup = Setup(store, model)
    setup.recorder.observe(alert(Status.FIRING, 0, title="API down"))
    setup.investigator.run_pending(LATER)
    return setup


def test_an_investigation_is_replayed_from_its_recorded_tool_results(store: Store) -> None:
    setup = investigated(store)
    store.append("feedback.recorded", {"about": "root_cause", "vote": "up"}, incident_id=1)
    cases, skipped = replay.investigation_cases(store)
    assert skipped == 0
    (case,) = cases
    assert [spec["name"] for spec in case.specs] == ["inspect_container", "recent_changes", SUBMIT]
    assert [call["name"] for call in case.calls] == ["inspect_container", "recent_changes"]
    assert case.label == "right"

    # Today's model asks the same question, then one it never asked, then hands in.
    model = Scripted(
        calls(("inspect_container", {"name": "api"}), ("inspect_container", {"name": "db"})),
        calls((SUBMIT, {**FINDINGS, "summary": "Still the OOM loop."})),
    )
    tools_before = len(setup.tool_log)
    before = len(list(store.events()))
    (result,) = replay.replay_investigation(cases, model, setup.config)
    assert len(setup.tool_log) == tools_before  # no tool ran: the record answered
    assert len(list(store.events())) == before
    assert (result.asked, result.unrecorded, result.failure) == (2, 1, None)
    assert result.findings is not None
    assert result.findings["summary"] == "Still the OOM loop."
    assert not result.regression
    answers = [json.loads(r.content) for r in model.seen[1][1][-1].results]  # type: ignore[union-attr]
    assert answers[0]["untrusted_data"]["state"] == {"oomkilled": True}
    assert answers[1]["untrusted_data"] == {"error": replay.NOT_RECORDED}
    assert [name for name in model.seen[0][2]] == ["inspect_container", "recent_changes", SUBMIT]
    report = replay.report_investigation([result], skipped=2)
    assert report.lines[0] == "ok         #1 API down, you said the cause was right"
    assert "2 tool calls, 1 not in the record" in report.lines[3]
    assert report.lines[-1].startswith("2 older ones cannot be replayed")
    assert not report.failed


def test_no_findings_or_another_typed_plan_is_a_regression(store: Store) -> None:
    setup = investigated(store)
    store.append("feedback.recorded", {"about": "root_cause", "vote": "up"}, incident_id=1)
    cases, _ = replay.investigation_cases(store)
    (lost,) = replay.replay_investigation(cases, Scripted(ModelError("HTTP 500")), setup.config)
    assert (lost.findings, lost.regression, lost.confidence) == (None, True, None)
    assert "no findings (model_error: HTTP 500)" in replay.report_investigation([lost]).lines[2]

    typed = {
        **FINDINGS,
        "plan": [{"action": "r", "catalog_action": "restart_container", "target": "api"}],
    }
    other = replay.InvestigationResult(cases[0], typed)
    assert (other.same_plan, other.regression) == (False, True)
    unrated = replay.InvestigationCase(**{**cases[0].__dict__, "label": None})
    assert not replay.InvestigationResult(unrated, typed).regression


def test_investigations_recorded_without_their_tools_are_counted_not_replayed(store: Store) -> None:
    investigated(store)
    recorder = Recorder(store)
    recorder.observe(alert(Status.FIRING, 60, "old", title="old one", labels={"host": "x"}))
    incident = store.open_incident(
        title="old",
        severity=__import__("paeon.schema").schema.Severity.INFO,
        host=None,
        opened_at=T0,
        maintenance=None,
        triage=__import__("paeon.schema").schema.StageState.OFF,
    )
    store.append("investigation.started", {"tools": ["x"], "input": {}}, incident_id=incident)
    store.append("investigation.completed", {"findings": FINDINGS}, incident_id=incident)
    store.append("investigation.completed", {"findings": FINDINGS}, incident_id=incident + 7)
    cases, skipped = replay.investigation_cases(store)
    assert (len(cases), skipped) == (1, 2)


def test_a_corpus_goes_to_a_file_and_back(store: Store, tmp_path: Path) -> None:
    triaged(store)
    investigated(store)
    cases: list[replay.TriageCase | replay.InvestigationCase] = [
        *replay.triage_cases(store),
        *replay.investigation_cases(store)[0],
    ]
    out = io.StringIO()
    assert replay.write_corpus(cases, out) == 5
    back = replay.read_corpus([*out.getvalue().splitlines(), ""])
    assert back[0] == replay.triage_cases(store)
    assert back[1] == replay.investigation_cases(store)[0]


def test_cli_replay_lists_exports_and_fails_on_a_regression(
    store: Store,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    triaged(store)
    investigated(store)
    db = str(tmp_path / "paeon.sqlite")
    rules = tmp_path / "paeon.yaml"
    rules.write_text(
        "models:\n  triage: {backend: openai, model: auto, api_key_env: K}\n"
        "  investigate: {backend: anthropic, model: claude-opus-5-5}\n"
        "triage: {enabled: true}\ninvestigation: {enabled: true}\n"
    )
    monkeypatch.setenv("PAEON_CONFIG", str(rules))
    monkeypatch.setenv("K", "k")
    assert main(["replay", "triage", "--db", db, "--dry-run", "--limit", "2"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "#3 m3 (you said wrong)",
        "#4 m4",
        "2 cases, 0 that cannot be replayed.",
    ]
    corpus = tmp_path / "corpus.jsonl"
    assert main(["replay", "investigation", "--db", db, "--export", str(corpus)]) == 0
    assert "1 cases written" in capsys.readouterr().out
    assert main(["replay", "investigation", "--corpus", str(corpus), "--dry-run"]) == 0
    assert "#6 API down" in capsys.readouterr().out

    class Today:
        closed = False

        def converse(self, **kwargs: Any) -> Any:
            return TriageModel(answer("noise", 0.9)).converse(**kwargs)

        def close(self) -> None:
            Today.closed = True

    monkeypatch.setattr("paeon.models.build_client", lambda alias, key: Today())
    assert main(["replay", "triage", "--db", db, "--days", "36500"]) == 1  # #1 would be missed
    out, err = capsys.readouterr()
    assert "MISSED     #1 m1" in out
    assert "Cost of this replay:" in out
    assert "the model is `auto`" in err
    assert Today.closed

    rules.write_text("models: {}\n")
    assert main(["replay", "triage", "--db", db]) == 2
    assert "is not defined under `models`" in capsys.readouterr().err
