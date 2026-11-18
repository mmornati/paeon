from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import yaml

from paeon.correlation import Correlator
from paeon.models import ModelError, ModelReply, ToolSpec, Turn, UserTurn
from paeon.postmortem import TODO, PostmortemWriter
from paeon.recorder import Recorder
from paeon.rules import Rules, parse_rules
from paeon.schema import StageState, Status
from paeon.store import Store
from support import T0, alert

NARRATIVE = {
    "title": "The API was unreachable for 12 minutes",
    "summary": "Version 2.1 exceeded its memory limit and was killed in a loop.",
    "impact": "All API clients failed from 08:00 to 08:12.",
    "detection": "The uptime monitor fired within a minute.",
    "root_cause": "The new cache is unbounded.",
    "trigger": "The 07:55 deploy.",
    "contributing_factors": ["No memory alert on the container"],
    "resolution": "Rolled back to 2.0.",
    "went_well": ["Fast detection"],
    "went_badly": ["No canary"],
    "lucky": [],
    "action_items": [{"type": "prevent", "action": "Bound the cache"}],
    "lessons": ["Deploys need a memory check"],
}
FINDINGS = {
    "summary": "OOM loop",
    "root_cause": {"statement": "memory limit", "confidence": 0.85},
    "evidence": [{"claim": "killed", "call": "c1", "verified": True}, {"claim": "x", "call": "c7"}],
    "plan": [{"action": "Roll back", "risk": "low"}],
}


class Writer:
    def __init__(self, answer: str | Exception) -> None:
        self.answer = answer
        self.inputs: list[str] = []

    def converse(
        self, *, system: str, turns: Sequence[Turn], tools: Sequence[ToolSpec] = ()
    ) -> ModelReply:
        [first] = turns
        assert isinstance(first, UserTurn)
        self.inputs.append(first.text)
        if isinstance(self.answer, Exception):
            raise self.answer
        return ModelReply(text=self.answer, model="writer-1", input_tokens=2000, output_tokens=500)


def rules(directory: Path) -> Rules:
    return parse_rules(
        {
            "models": {
                "postmortem": {
                    "backend": "anthropic",
                    "model": "m",
                    "price": {"input_per_mtok": 4, "output_per_mtok": 20},
                }
            },
            "postmortems": {"enabled": True, "dir": str(directory)},
        }
    )


def investigated_incident(store: Store, config: Rules) -> None:
    rec = Recorder(store, Correlator(store, config))
    host = {"host": "server-1"}
    rec.observe(alert(Status.FIRING, 0, "api", title="API down", labels=host))
    store.append(
        "triage.verdict",
        {"effective_verdict": "real", "verdict": "real", "confidence": 0.9},
        incident_id=1,
    )
    store.append("investigation.started", {}, incident_id=1)
    store.append(
        "tool.call",
        {"call": "c1", "name": "inspect_container", "input": {"name": "api"}},
        incident_id=1,
    )
    store.append("investigation.completed", {"findings": FINDINGS, "tool_calls": 1}, incident_id=1)
    rec.observe(alert(Status.RESOLVED, 12, "api", title="API down", labels=host))
    store.set_stage(1, "postmortem", StageState.PENDING)


def test_draft_is_written_with_front_matter_timeline_and_review(
    store: Store, tmp_path: Path
) -> None:
    config = rules(tmp_path / "pm")
    investigated_incident(store, config)
    model = Writer("Here it is:\n" + json.dumps(NARRATIVE))
    writer = PostmortemWriter(store, config, model)

    # It may still reopen: wait for the correlation window after it resolved.
    assert writer.run_pending(T0 + timedelta(minutes=20)) == 0
    assert writer.run_pending(T0 + timedelta(minutes=50)) == 1

    [path] = list((tmp_path / "pm").iterdir())
    assert path.name == "2026-11-18-api-down.md"
    text = path.read_text()
    _, front_matter, body = text.split("---\n", 2)
    front = yaml.safe_load(front_matter)
    assert front == {
        "id": "2026-11-18-api-down",
        "incident": 1,
        "status": "draft",
        "severity": "warning",
        "hosts": ["server-1"],
        "alerts": ["API down"],
        "alert_fingerprints": ["test/api"],
        "detected_at": "2026-11-18T08:00:00+00:00",
        "resolved_at": "2026-11-18T08:12:00+00:00",
        "metrics": {"ttr_min": 12.0},
        "flapping": False,
        "maintenance_window": None,
        "autonomy": "observe",
        "llm_cost": 0.018,
    }
    assert body.startswith("\n# The API was unreachable for 12 minutes\n")
    for heading in (
        "## Summary",
        "## Impact",
        "## Timeline",
        "## Detection",
        "## Root cause",
        "## Trigger",
        "## Contributing factors",
        "## Resolution",
        "## What went well",
        "## What went badly",
        "## Where we got lucky",
        "## Automation review",
        "## Action items",
        "## Lessons learned",
        "## Appendix",
    ):
        assert heading in body
    assert "- 2026-11-18 08:00:00 UTC Alert firing: API down (test)" in body
    assert "- 2026-11-18 08:12:00 UTC Alert resolved: API down (test)" in body
    assert "Investigation completed after 1 tool calls" in body
    assert "- [ ] (prevent) Bound the cache" in body
    assert "- Triage: real (model said real, confidence 0.9); was it right?" in body
    assert "1 of 2 claims tied to a tool call" in body
    assert "It suggested 1 steps." in body
    assert '- `c1` inspect_container {"name": "api"}' in body
    assert f"## Where we got lucky\n\n{TODO}" in body  # an empty list is left to the reviewer

    assert '"findings"' in model.inputs[0]
    drafted = next(iter(store.events(kind="postmortem.drafted"))).payload
    assert drafted == {"path": "2026-11-18-api-down.md", "model": "writer-1", "narrative": True}
    assert store.incidents()[0].postmortem is StageState.DONE
    assert writer.run_pending(T0 + timedelta(hours=2)) == 0


def test_draft_is_still_written_when_the_model_fails(store: Store, tmp_path: Path) -> None:
    config = rules(tmp_path / "pm")
    investigated_incident(store, config)
    for answer in (ModelError("HTTP 529"), "not json at all"):
        store.set_stage(1, "postmortem", StageState.PENDING)
        PostmortemWriter(store, config, Writer(answer)).run_pending(T0 + timedelta(hours=1))
    first, second = sorted((tmp_path / "pm").iterdir())
    assert (first.name, second.name) == (
        "2026-11-18-api-down-1.md",
        "2026-11-18-api-down.md",
    )  # never overwritten
    text = second.read_text()
    assert "# API down" in text
    assert f"## Summary\n\n{TODO}" in text
    assert "## Timeline\n\n- 2026-11-18" in text
    errors = [e.payload.get("error") for e in store.events(kind="model.call")]
    assert errors == ["HTTP 529", "invalid_output"]
    assert [e.payload["narrative"] for e in store.events(kind="postmortem.drafted")] == [
        False,
        False,
    ]


def test_spent_budget_writes_the_draft_without_a_model_call(store: Store, tmp_path: Path) -> None:
    config = rules(tmp_path / "pm").model_copy(deep=True)
    config = parse_rules(
        {
            **config.model_dump(mode="json"),
            "postmortems": {"enabled": True, "dir": str(tmp_path / "pm"), "daily_budget": 0.01},
        }
    )
    investigated_incident(store, config)
    store.append("model.call", {"cost": 0.02})  # today's spend, from any stage
    model = Writer(json.dumps(NARRATIVE))
    from datetime import UTC, datetime

    PostmortemWriter(store, config, model).draft(store.incidents()[0], datetime.now(UTC))
    assert model.inputs == []
    [path] = list((tmp_path / "pm").glob("*.md"))
    assert f"## Summary\n\n{TODO}" in path.read_text()
    assert next(iter(store.events(kind="postmortem.drafted"))).payload["narrative"] is False


def test_open_incidents_are_not_drafted(store: Store, tmp_path: Path) -> None:
    config = rules(tmp_path / "pm")
    Recorder(store, Correlator(store, config)).observe(alert(Status.FIRING, 0))
    store.set_stage(1, "postmortem", StageState.PENDING)
    assert PostmortemWriter(store, config, Writer("{}")).run_pending(T0 + timedelta(days=1)) == 0
    assert not (tmp_path / "pm").exists()


def test_unwritable_directory_is_recorded(store: Store, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    config = rules(blocker / "pm")
    investigated_incident(store, config)
    writer = PostmortemWriter(store, config, Writer(json.dumps(NARRATIVE)))
    assert writer.run_pending(T0 + timedelta(hours=1)) == 1
    [failed] = list(store.events(kind="postmortem.failed"))
    assert failed.payload["error"] in {"NotADirectoryError", "FileExistsError"}
    assert store.incidents()[0].postmortem is StageState.FAILED
