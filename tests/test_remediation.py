"""The core's side of acting: it proposes, mirrors, and falls back. It never decides."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from acting import ACTIONS, Bench, proposal
from paeon.app import create_app
from paeon.catalog import Proposal
from paeon.config import ExecutorSettings, Settings, load
from paeon.correlation import Correlator
from paeon.executor_app import create_executor_app
from paeon.investigation import Findings
from paeon.recorder import Recorder
from paeon.remediation import (
    ExecutorClient,
    ExecutorError,
    Journal,
    Remediator,
    pending_approvals,
)
from paeon.rules import Rules, parse_rules
from paeon.schema import IncidentStatus, StageState, Status
from paeon.store import Store
from support import T0, alert

TOKEN = "t" * 32
LATER = T0 + timedelta(minutes=5)


class FakeExecutor:
    def __init__(self) -> None:
        self.proposed: list[Proposal] = []
        self.cancelled: list[tuple[int, str]] = []
        self.entries: list[dict[str, Any]] = []
        self.answer: dict[str, Any] = {"status": "pending", "proposal": 1, "level": "approve"}
        self.down = False
        self.killed = False

    def _check(self) -> None:
        if self.down:
            raise ExecutorError("unreachable: ConnectError")

    def propose(self, proposal: Proposal) -> dict[str, Any]:
        self._check()
        self.proposed.append(proposal)
        return self.answer

    def cancel(self, proposal_id: int, reason: str) -> None:
        self._check()
        self.cancelled.append((proposal_id, reason))

    def journal(self, after: int) -> Journal:
        self._check()
        ids = [e["id"] for e in self.entries if isinstance(e["id"], int)]
        events = [e for e in self.entries if not isinstance(e["id"], int) or e["id"] > after]
        return Journal(events, max(ids, default=0))

    def kill(self) -> None:
        self._check()
        self.killed = True

    def vote_links(self, incident_id: int, about: str) -> dict[str, str]:
        self._check()
        return {"up": f"https://act.example.org/act/up{incident_id}", "down": "https://x/down"}

    def add(self, kind: str, incident_id: int | None = 1, **payload: Any) -> None:
        self.entries.append(
            {
                "id": len(self.entries) + 1,
                "at": T0.isoformat(),
                "kind": kind,
                "incident_id": incident_id,
                "payload": payload,
            }
        )


def rules(**more: Any) -> Rules:
    return parse_rules(
        {
            "models": {"m": {"backend": "openai", "model": "x"}},
            "investigation": {"enabled": True, "model": "m"},
            "postmortems": {"enabled": True, "model": "m"},
            "actions": {**ACTIONS, "settle_seconds": 60},
            "ui": {"public_url": "https://paeon.example.org/"},
            **more,
        }
    )


def setup(store: Store, config: Rules | None = None) -> tuple[Recorder, Remediator, FakeExecutor]:
    config = config or rules()
    fake = FakeExecutor()
    return Recorder(store, Correlator(store, config)), Remediator(store, config, fake), fake


def findings(*plan: dict[str, Any]) -> Findings:
    return Findings.model_validate(
        {
            "summary": "The worker stalled.",
            "impact": "Jobs are late.",
            "root_cause": {"statement": "A deadlock.", "confidence": 0.9},
            "plan": list(plan),
        }
    )


def test_only_typed_steps_of_an_analysis_are_proposed(store: Store) -> None:
    recorder, remediator, fake = setup(store)
    recorder.observe(alert(Status.FIRING, 0, title="worker_down"))
    incident = store.incident(1)
    assert incident is not None
    remediator.on_findings(incident, findings({"action": "Look at the queue"}))
    assert fake.proposed == []
    remediator.on_findings(
        incident,
        findings(
            {"action": "Look at the queue"},
            {"action": "Restart it", "catalog_action": "restart_container", "target": "worker"},
        ),
    )
    (sent,) = fake.proposed
    assert [step.label for step in sent.steps] == ["restart_container(worker)"]
    assert (sent.origin, sent.alerts, sent.confidence) == ("investigation", ("worker_down",), 0.9)
    assert sent.mode == "sequence"

    fake.down = True
    remediator.on_findings(
        incident,
        findings({"action": "r", "catalog_action": "restart_container", "target": "worker"}),
    )
    lost = next(store.events(kind="plan.not_proposed")).payload
    assert lost == {"reason": "unreachable: ConnectError", "steps": ["restart_container(worker)"]}


def test_a_runbook_hit_skips_both_models_and_is_proposed_after_settling(store: Store) -> None:
    recorder, remediator, fake = setup(store)
    recorder.observe(alert(Status.FIRING, 0, title="worker_stalled"))
    incident = store.incident(1)
    assert incident is not None
    assert (incident.triage, incident.investigation) == (StageState.SKIPPED, StageState.SKIPPED)
    matched = next(store.events(kind="runbook.matched")).payload
    assert matched["steps"] == ["restart_container(worker)", "restart_container(vpn)"]

    assert remediator.run_pending(T0 + timedelta(seconds=30)) == 0  # it may clear by itself
    assert fake.proposed == []
    assert remediator.run_pending(LATER) == 1
    (sent,) = fake.proposed
    assert (sent.origin, sent.runbook, sent.mode, sent.confidence) == (
        "runbook",
        "worker-stalled",
        "escalate",
        1.0,
    )
    assert remediator.run_pending(LATER) == 0  # once
    assert len(fake.proposed) == 1


def test_a_runbook_whose_alert_cleared_is_not_run(store: Store) -> None:
    recorder, remediator, fake = setup(store)
    recorder.observe(alert(Status.FIRING, 0, title="worker_stalled"))
    recorder.observe(alert(Status.RESOLVED, 0.5, title="worker_stalled"))
    assert remediator.run_pending(LATER) == 1
    assert fake.proposed == []
    assert next(store.events(kind="runbook.skipped")).payload == {"reason": "resolved by itself"}


def test_a_runbook_that_cannot_run_hands_over_to_the_investigator(store: Store) -> None:
    recorder, remediator, fake = setup(store)
    recorder.observe(alert(Status.FIRING, 0, title="worker_stalled"))
    fake.down = True
    assert remediator.run_pending(LATER) == 0  # the executor may be restarting
    assert store.incident(1).investigation is StageState.SKIPPED  # type: ignore[union-attr]
    assert remediator.run_pending(T0 + timedelta(minutes=20)) == 1
    assert next(store.events(kind="runbook.failed")).payload == {
        "reason": "runbook not run: unreachable: ConnectError"
    }
    assert store.incident(1).investigation is StageState.PENDING  # type: ignore[union-attr]

    # Refused by the executor (its rules differ): the same hand-over.
    recorder.observe(alert(Status.FIRING, 60, "b", title="worker_stalled", labels={"host": "b"}))
    fake.down = False
    fake.answer = {"status": "refused", "reason": "not a runbook of the executor"}
    remediator.run_pending(T0 + timedelta(minutes=70))
    assert store.incident(2).investigation is StageState.PENDING  # type: ignore[union-attr]


def test_the_journal_is_mirrored_once_and_a_failed_runbook_falls_back(store: Store) -> None:
    recorder, remediator, fake = setup(store)
    recorder.observe(alert(Status.FIRING, 0, title="worker_stalled"))
    remediator.run_pending(LATER)
    fake.add("plan.proposed", proposal=1, level="auto", steps=["restart_container(worker)"])
    fake.add("plan.completed", proposal=1, outcome="failed", origin="runbook", reason="down")
    fake.add("killswitch.engaged", incident_id=None, via="link")
    fake.entries.append({"id": "junk"})
    assert remediator.mirror() == 3
    assert remediator.mirror() == 0
    mirrored = [e for e in store.events(source="executor")]
    assert [e.kind for e in mirrored] == ["plan.proposed", "plan.completed", "killswitch.engaged"]
    assert (mirrored[0].incident_id, mirrored[2].incident_id) == (1, None)
    assert mirrored[1].payload["executor_event"] == 2
    incident = store.incident(1)
    assert incident is not None
    # The fix did not work: the model gets the incident, and it will have a postmortem.
    assert incident.investigation is StageState.PENDING
    assert incident.postmortem is StageState.PENDING
    assert (
        "the runbook's fix did not verify"
        in next(store.events(kind="runbook.failed")).payload["reason"]
    )

    fake.down = True
    assert remediator.run_pending(LATER) == 0  # an unreachable executor is not an error
    fake.down = False

    # The executor's database was replaced: its journal starts again, and so does the mirror.
    fake.entries.clear()
    fake.add("killswitch.released", incident_id=None, via="cli")
    assert remediator.mirror() == 1
    assert next(store.events(kind="executor.journal_restarted")).payload == {"was": 3, "now": 1}
    assert remediator.mirror() == 0


def test_an_approval_for_a_resolved_incident_is_cancelled(store: Store) -> None:
    recorder, remediator, fake = setup(store)
    recorder.observe(alert(Status.FIRING, 0, title="worker_down"))
    fake.add("plan.proposed", proposal=7, level="approve", steps=["restart_container(worker)"])
    fake.add("approval.requested", proposal=7, expires_at=(T0 + timedelta(minutes=30)).isoformat())
    remediator.run_pending(LATER)
    assert fake.cancelled == []
    (waiting,) = pending_approvals(store, LATER)
    assert (waiting["incident"], waiting["proposal"]) == (1, 7)
    assert waiting["steps"] == ["restart_container(worker)"]

    recorder.observe(alert(Status.RESOLVED, 6, title="worker_down"))
    assert store.incident(1).status is IncidentStatus.RESOLVED  # type: ignore[union-attr]
    remediator.run_pending(LATER)
    assert fake.cancelled == [(7, "the incident resolved before an answer")]
    fake.add("plan.cancelled", proposal=7, reason="the incident resolved before an answer")
    remediator.run_pending(LATER)
    assert len(fake.cancelled) == 1
    assert pending_approvals(store, LATER) == []


def test_extras_carry_the_page_and_the_vote_buttons(store: Store) -> None:
    recorder, remediator, fake = setup(store)
    recorder.observe(alert(Status.FIRING, 0, title="worker_down"))
    incident = store.incident(1)
    assert incident is not None
    extras = remediator.extras(incident)
    assert extras.click == "https://paeon.example.org/ui/incidents/1"
    assert [(a["label"], a["method"]) for a in extras.actions] == [
        ("Cause is right", "POST"),
        ("Cause is wrong", "POST"),
    ]
    fake.down = True
    assert remediator.extras(incident).actions == ()
    fake.down = False
    remediator.stop()
    assert fake.killed


# --- the two processes together -------------------------------------------------------------


def together(store: Store, bench: Bench, tmp_path: Path) -> tuple[TestClient, TestClient]:
    """The core and the executor as two apps, the core reaching the executor over HTTP."""
    executor_app = TestClient(
        create_executor_app(
            ExecutorSettings(
                db_path=tmp_path / "unused",
                host="127.0.0.1",
                port=8081,
                token=TOKEN,
                rules=bench.rules,
                signing_key=None,
                ntfy_token=None,
                kill_switch=False,
            ),
            bench.executor,
        )
    )

    def forward(request: httpx.Request) -> httpx.Response:
        answer = executor_app.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx.Response(answer.status_code, content=answer.content)

    client = ExecutorClient("http://executor:8081", TOKEN, transport=httpx.MockTransport(forward))
    base = load({"PAEON_INGEST_TOKEN": TOKEN})
    settings = Settings(
        db_path=base.db_path,
        host=base.host,
        port=base.port,
        ingest_token=TOKEN,
        openobserve=None,
        heartbeat_url=None,
        heartbeat_seconds=60,
        rules=parse_rules({"actions": {**ACTIONS, "settle_seconds": 0}, "ui": {"enabled": True}}),
        executor_token=TOKEN,
    )
    return TestClient(create_app(settings, store, executor=client)), executor_app


def test_alert_to_fix_through_both_processes(store: Store, tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    core, _ = together(store, bench, tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    body = {
        "alerts": [
            {
                "status": "firing",
                "fingerprint": "w1",
                "labels": {"alertname": "worker_stalled", "severity": "warning"},
            }
        ]
    }
    assert core.post("/ingest/alertmanager", json=body, headers=auth).json()["changes"] == 1
    remediator = core.app.state.remediator  # type: ignore[attr-defined]
    remediator.run_pending(__import__("datetime").datetime.now(T0.tzinfo) + timedelta(seconds=5))
    # The runbook's policy is `auto`: the executor restarted the worker and said so.
    assert bench.docker.posts == ["restart worker"]
    assert bench.sent[0]["title"] == "Incident #1: fix applied"
    mirrored = [e.kind for e in store.events(source="executor")]
    assert mirrored == [
        "plan.proposed",
        "action.started",
        "action.verified",
        "plan.completed",
        "notification.sent",
    ]
    assert core.get("/autonomy", headers=auth).json()["tuples"][0]["verified"] == 1
    brief = core.get("/brief", headers=auth).json()
    assert [i["title"] for i in brief["open_incidents"]] == ["worker_stalled"]
    assert brief["pending_approvals"] == []
    assert core.get("/postmortems", headers=auth).json() == []

    page = core.get("/ui/incidents/1").text
    assert "It ran without asking (auto). It ran, and the result was verified." in page
    assert "From the runbook worker-stalled. Level auto." in page
    assert "Not sent to a model: a runbook matched this alert." in page


def test_the_executor_client_reports_failures_without_details() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/journal":
            return httpx.Response(200, content=b"not json")
        if request.url.path == "/state":
            raise httpx.ConnectError("no route to http://executor:8081 with Bearer tttt")
        return httpx.Response(401)

    client = ExecutorClient("http://executor:8081", TOKEN, transport=httpx.MockTransport(refuse))
    try:
        for call, message in (
            (lambda: client.propose(proposal()), "HTTP 401"),
            (lambda: client.journal(0), "the answer is not JSON"),
            (lambda: client._call("GET", "/state"), "unreachable: ConnectError"),
        ):
            with pytest.raises(ExecutorError) as caught:
                call()
            assert str(caught.value) == message
    finally:
        client.close()


def test_actions_need_the_executor_token(tmp_path: Path) -> None:
    from paeon.config import ConfigError

    rules_file = tmp_path / "paeon.yaml"
    rules_file.write_text("actions: {enabled: true}\n")
    env = {"PAEON_INGEST_TOKEN": TOKEN, "PAEON_CONFIG": str(rules_file)}
    with pytest.raises(ConfigError, match="PAEON_EXECUTOR_TOKEN"):
        load(env)
    assert load({**env, "PAEON_EXECUTOR_TOKEN": "x" * 24}).executor_token == "x" * 24


def test_the_digest_asks_about_each_verdict_with_two_buttons(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from paeon import digest as digests

    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        digests, "publish_ntfy", lambda ntfy, token, **message: sent.append(message)
    )
    base = load({"PAEON_INGEST_TOKEN": TOKEN})
    document = {
        "actions": ACTIONS,
        "ui": {"public_url": "https://paeon.example.org"},
        "digest": {
            "hour": 0,
            "feedback": 2,
            "ntfy": {"url": "https://ntfy.example.org", "topic": "incidents"},
        },
    }
    settings = Settings(
        db_path=base.db_path,
        host=base.host,
        port=base.port,
        ingest_token=TOKEN,
        openobserve=None,
        heartbeat_url=None,
        heartbeat_seconds=60,
        rules=parse_rules(document),
        executor_token=TOKEN,
    )
    fake = FakeExecutor()
    send = create_app(settings, store, executor=fake).state.send_digest
    recorder = Recorder(store, Correlator(store, settings.rules))
    verdicts: list[tuple[str, str | None, str | None]] = [
        ("real", None, None),
        ("noise", None, None),
        ("noise", "budget", None),
        ("transient", None, "up"),
        ("known_issue", None, None),
    ]
    for number, (verdict, fail_open, vote) in enumerate(verdicts, 1):
        labels = {"host": f"h{number}"}
        recorder.observe(
            alert(Status.FIRING, number, f"a{number}", title=f"m{number}", labels=labels)
        )
        store.append(
            "triage.verdict",
            {
                "verdict": verdict,
                "effective_verdict": "real" if fail_open else verdict,
                "fail_open": fail_open,
                "confidence": 0.9,
                "reason": f"because {number}",
            },
            incident_id=number,
        )
        if vote:
            store.append("feedback.recorded", {"about": "triage", "vote": vote}, incident_id=number)
    assert send(T0 + timedelta(hours=1))
    summary, *questions = sent
    assert "answer the questions that follow" in summary["message"]
    assert summary["click"] == "https://paeon.example.org/ui"
    # Two at most, the demoted one first; never a fail-open or an already rated verdict.
    assert [q["title"] for q in questions] == [
        "#2 m2: noise, 90% sure",
        "#5 m5: known_issue, 90% sure",
    ]
    first = questions[0]
    assert first["message"] == "because 2\n\nWas triage right?"
    assert first["priority"] == 1
    assert first["click"] == "https://paeon.example.org/ui/incidents/2"
    assert [(a["label"], a["url"]) for a in first["actions"]] == [
        ("Right", "https://act.example.org/act/up2"),
        ("Wrong", "https://x/down"),
    ]
    assert next(store.events(kind="digest.sent")).payload["asked"] == 2

    # Without the executor there is nobody to sign a button: the digest alone goes out.
    sent.clear()
    fake.down = True
    assert send(T0 + timedelta(hours=17))  # the next day, with the same incidents in reach
    assert len(sent) == 1
