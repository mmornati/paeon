"""What a person does from the console besides reading: answer a plan, ask an incident again,
close it, review a postmortem; and what the analysis says a plan will become."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acting import Bench, proposal
from paeon import knowledge
from paeon import postmortem as postmortems
from paeon.app import create_app
from paeon.config import Settings, load
from paeon.correlation import Correlator
from paeon.investigation import outcome_line
from paeon.operations import Incidents, openobserve_checker, tool_checker
from paeon.recorder import Recorder
from paeon.remediation import Journal
from paeon.rules import parse_rules
from paeon.schema import Alert, IncidentStatus, Severity, Status
from paeon.store import Store
from paeon.ui import views
from test_ui_actions import Switch
from ui_data import ACTING_RULES, populate, populate_actions

TOKEN = "t" * 32
SAME = {"Sec-Fetch-Site": "same-origin"}
PROXY = "p" * 32
VIEWER = {
    "X-Paeon-Proxy": PROXY,
    "X-authentik-groups": "family",
    "X-authentik-username": "guest",
    **SAME,
}
OPERATOR = {
    "X-Paeon-Proxy": PROXY,
    "X-authentik-groups": "paeon-ops",
    "X-authentik-username": "marco",
    **SAME,
}
T0 = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)


# --- what the analysis says the plan becomes --------------------------------------------------


def test_an_analysis_says_a_recorded_plan_will_not_run_and_why() -> None:
    recorded = {
        "status": "recorded",
        "level": "observe",
        "held_back_by": ["restart_container(vpn): a policy sets it to observe (target vpn)"],
    }
    line = outcome_line(recorded)
    assert line.startswith(
        "Level observe. Recorded only: nothing will run and nothing will be asked."
    )
    assert "a policy sets it to observe (target vpn)" in line
    assert outcome_line({"status": "pending", "level": "approve", "held_back_by": []}) == (
        "Level approve. Waiting for your Go: it comes in its own message, with Go and No-go."
    )
    assert "Why: unreachable" in outcome_line({"status": "not_sent", "reason": "unreachable"})


def test_the_executor_says_which_policy_held_a_plan(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    answer = bench.executor.propose(proposal(("restart_container", "vpn"), alert="watched"), T0)
    assert answer["status"] == "recorded"
    assert answer["level"] == "observe"
    assert answer["held_back_by"] == ["restart_container(vpn): a policy sets it to observe"]
    # A retry of the same plan says the same.
    again = bench.executor.propose(proposal(("restart_container", "vpn"), alert="watched"), T0)
    assert again == answer


# --- Go and No-go from the console ------------------------------------------------------------


def test_the_executor_answers_the_console_only_when_its_own_rules_say_so(tmp_path: Path) -> None:
    from paeon.links import LinkError

    off = Bench(tmp_path / "off")
    off.executor.propose(proposal(), T0)
    with pytest.raises(LinkError, match="not enabled on the executor"):
        off.executor.answer(1, "go", by="marco", now=T0)
    assert off.docker.posts == []

    on = Bench(tmp_path / "on", console_approval=True)
    on.executor.propose(proposal(), T0)
    outcome = on.executor.answer(1, "go", by="marco", now=T0 + timedelta(minutes=1))
    assert outcome.ok
    assert on.docker.posts == ["restart worker"]
    assert on.last("approval.granted") == {"proposal": 1, "via": "console", "by": "marco"}
    assert on.titles()[1] == "Incident #1: Go from the console"
    # The phone's buttons no longer work: the first answer counted.
    go = on.link("Go", 0)
    assert not on.executor.act(go, T0 + timedelta(minutes=2)).ok
    with pytest.raises(LinkError, match="no longer waiting"):
        on.executor.answer(1, "nogo", by="marco", now=T0 + timedelta(minutes=2))


def test_the_console_cannot_answer_an_expired_or_stopped_plan(tmp_path: Path) -> None:
    from paeon.links import LinkError

    bench = Bench(tmp_path, console_approval=True)
    bench.executor.propose(proposal(), T0)
    with pytest.raises(LinkError, match="expired"):
        bench.executor.answer(1, "go", by="marco", now=T0 + timedelta(hours=2))
    stopped = Bench(tmp_path / "stopped", console_approval=True)
    stopped.executor.propose(proposal(), T0)
    stopped.executor._journal("killswitch.engaged", {"via": "test"}, None)
    with pytest.raises(LinkError, match="stopped"):
        stopped.executor.answer(1, "go", by="marco", now=T0)
    nogo = Bench(tmp_path / "nogo", console_approval=True)
    nogo.executor.propose(proposal(), T0)
    assert nogo.executor.answer(1, "nogo", by="", now=T0).ok
    assert nogo.last("approval.declined") == {"proposal": 1, "via": "console", "by": "an operator"}
    assert nogo.docker.posts == []


def console(
    store: Store,
    tmp_path: Path,
    *,
    approval: bool = True,
    switch: Switch | None = None,
    **more: Any,
) -> TestClient:
    base = load({"PAEON_INGEST_TOKEN": TOKEN})
    actions = {**ACTING_RULES["actions"], "console_approval": approval}
    document = {
        **ACTING_RULES,
        "actions": actions,
        "ui": {
            "enabled": True,
            "require_header": "X-authentik-username",
            "proxy_secret": {},
            "roles": {"admin": ["paeon-admins"], "operator": ["paeon-ops"]},
        },
        "models": {"m": {"backend": "openai", "model": "x"}},
        "postmortems": {"enabled": True, "model": "m", "dir": str(tmp_path / "postmortems")},
        **more,
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
        ui_proxy_secret=PROXY,
    )
    return TestClient(
        create_app(settings, store, executor=switch or Switch()), follow_redirects=False
    )


@pytest.fixture
def seeded(store: Store, tmp_path: Path) -> dict[str, int]:
    ids = populate(store)
    populate_actions(store, ids, tmp_path / "postmortems")
    return ids


def test_an_operator_says_go_in_the_console(
    store: Store, seeded: dict[str, int], tmp_path: Path
) -> None:
    switch = Switch()
    http = console(store, tmp_path, switch=switch)
    media = seeded["media"]
    page = http.get(f"/ui/incidents/{media}", headers=OPERATOR).text
    assert "Go: run it" in page
    assert 'name="proposal" value="11"' in page
    assert "Go: run it" not in http.get(f"/ui/incidents/{media}", headers=VIEWER).text

    form = {"proposal": "11", "reply": "go"}
    assert http.post(f"/ui/incidents/{media}/answer", data=form, headers=VIEWER).status_code == 403
    no_origin = {k: v for k, v in OPERATOR.items() if k != "Sec-Fetch-Site"}
    assert (
        http.post(f"/ui/incidents/{media}/answer", data=form, headers=no_origin).status_code == 403
    )
    wrong = http.post(
        f"/ui/incidents/{media}/answer", data={**form, "proposal": "9"}, headers=OPERATOR
    )
    assert wrong.status_code == 409  # not the plan that waits
    done = http.post(f"/ui/incidents/{media}/answer", data=form, headers=OPERATOR)
    assert done.headers["location"] == f"/ui/incidents/{media}?done=go#status"
    assert switch.answers == [(11, "go", "marco")]


def test_with_console_approval_off_there_is_no_go_in_the_console(
    store: Store, seeded: dict[str, int], tmp_path: Path
) -> None:
    http = console(store, tmp_path, approval=False)
    media = seeded["media"]
    assert "Go: run it" not in http.get(f"/ui/incidents/{media}", headers=OPERATOR).text
    form = {"proposal": "11", "reply": "go"}
    assert (
        http.post(f"/ui/incidents/{media}/answer", data=form, headers=OPERATOR).status_code == 404
    )


def test_a_refusal_of_the_executor_is_recorded(
    store: Store, seeded: dict[str, int], tmp_path: Path
) -> None:
    class Refusing(Switch):
        def answer(self, proposal_id: int, answer: str, by: str) -> dict[str, Any]:
            return {"ok": False, "message": "answering from the console is not enabled"}

    http = console(store, tmp_path, switch=Refusing())
    media = seeded["media"]
    done = http.post(
        f"/ui/incidents/{media}/answer", data={"proposal": "11", "reply": "go"}, headers=OPERATOR
    )
    assert done.headers["location"].endswith("?done=refused#status")
    refused = next(store.events(kind="approval.refused"))
    assert refused.payload["reason"] == "answering from the console is not enabled"


def test_the_record_says_who_answered_in_the_console(store: Store, tmp_path: Path) -> None:
    store.append(
        "plan.proposed", {"proposal": 3, "level": "approve", "steps": ["s"]}, incident_id=1
    )
    store.append("approval.requested", {"proposal": 3}, incident_id=1)
    store.append(
        "approval.granted", {"proposal": 3, "via": "console", "by": "marco"}, incident_id=1
    )
    plan = views.plan_of(list(store.events(incident_id=1)))
    assert plan is not None
    assert plan.story.startswith("marco said, in the console, Go.")
    row = views.row_of(next(iter(store.events(kind="approval.granted"))))
    assert (row.text, row.tag) == ("marco said Go, in the console", "you")


# --- re-check and close -----------------------------------------------------------------------


def alert(
    status: Status, at: datetime, fingerprint: str = "fp1", source: str = "openobserve"
) -> Alert:
    return Alert(
        source=source,
        fingerprint=fingerprint,
        status=status,
        severity=Severity.WARNING,
        title="arr_health_issues",
        labels={"alert_id": fingerprint},
        starts_at=at,
        observed_at=at,
    )


def opened(store: Store) -> tuple[Recorder, int]:
    rules = parse_rules({})
    recorder = Recorder(store, Correlator(store, rules))
    recorder.observe(alert(Status.FIRING, T0))
    incident = store.incidents()[0]
    return recorder, incident.id


def test_a_recheck_that_finds_the_alert_normal_resolves_the_incident(store: Store) -> None:
    recorder, incident_id = opened(store)
    asked: list[tuple[str, str]] = []

    def checker(source: str) -> Any:
        def check(fingerprint: str, title: str) -> tuple[str, str]:
            asked.append((fingerprint, title))
            return "resolved", "normal at its last evaluation, 20 Nov 09:00"

        return check if source == "openobserve" else None

    later = T0 + timedelta(days=1)
    book = Incidents(store, recorder, checker, lambda: later)
    found = book.recheck(incident_id, by="marco")
    assert [c.answer for c in found] == ["resolved"]
    assert asked == [("fp1", "arr_health_issues")]
    incident = store.incident(incident_id)
    assert incident is not None
    assert incident.status is IncidentStatus.RESOLVED
    kinds = [e.kind for e in store.events(incident_id=incident_id)]
    assert kinds[-3:] == ["alert.resolved", "incident.resolved", "incident.rechecked"]
    resolved = next(store.events(kind="alert.resolved"))
    assert resolved.payload["labels"]["resolved_by"] == "re-check"


def test_a_recheck_that_cannot_ask_leaves_the_incident_open(store: Store) -> None:
    recorder, incident_id = opened(store)
    book = Incidents(store, recorder, lambda source: None, lambda: T0 + timedelta(hours=1))
    found = book.recheck(incident_id, by="marco")
    assert [(c.answer, c.detail) for c in found] == [("unknown", "this source cannot be asked")]
    incident = store.incident(incident_id)
    assert incident is not None
    assert incident.status is IncidentStatus.OPEN


def test_closing_by_hand_resolves_and_a_new_firing_opens_it_again(store: Store) -> None:
    recorder, incident_id = opened(store)
    book = Incidents(store, recorder, lambda source: None, lambda: T0 + timedelta(hours=2))
    book.close(incident_id, by="marco", note="fixed by hand, the source never sends recovery")
    incident = store.incident(incident_id)
    assert incident is not None
    assert incident.status is IncidentStatus.RESOLVED
    closed = next(store.events(kind="incident.closed"))
    assert closed.payload == {
        "by": "marco",
        "note": "fixed by hand, the source never sends recovery",
        "alerts": 1,
    }
    with pytest.raises(ValueError, match="already resolved"):
        book.close(incident_id, by="marco", note="again")
    # The alert fires again, the next day: an incident again.
    recorder.observe(alert(Status.FIRING, T0 + timedelta(days=1)))
    assert any(i.status is IncidentStatus.OPEN for i in store.incidents())


def test_openobserve_is_asked_for_the_alerts_latest_evaluation() -> None:
    queries: list[str] = []
    rows: list[dict[str, Any]] = [
        {"_timestamp": 1795165200000000, "key": "arr/fp1", "status": "normal"}
    ]

    def search(sql: str, start: int, end: int, size: int) -> list[dict[str, Any]]:
        queries.append(sql)
        return rows

    check = openobserve_checker(search, lambda: T0)
    answer, detail = check("fp1", "arr")
    assert answer == "resolved"
    assert detail.startswith("normal at its last evaluation")
    assert "LIKE '%/fp1'" in queries[0]
    assert "ORDER BY _timestamp DESC" in queries[0]
    rows[0]["status"] = "firing"
    assert check("fp1", "arr")[0] == "firing"
    rows.clear()
    assert check("fp1", "arr") == ("unknown", "no evaluation in the last 7 days")
    # An id that is not one OpenObserve gives never reaches the query.
    assert check("x' OR 1=1 --", "arr")[0] == "unknown"
    assert len(queries) == 3


def test_kuma_and_alertmanager_answer_through_their_own_tools() -> None:
    kuma = tool_checker(
        lambda args: {"monitors": [{"name": "horae daily-brief", "status": "up"}]}, "kuma"
    )
    assert kuma("96", "horae daily-brief") == ("resolved", "uptime-kuma says up")
    am = tool_checker(
        lambda args: {"alerts": [{"fingerprint": "abc", "state": "active"}]}, "alertmanager"
    )
    assert am("abc", "x")[0] == "firing"
    assert am("zzz", "x") == ("resolved", "Alertmanager no longer lists it")


def test_an_operator_closes_an_incident_from_the_console(
    store: Store, seeded: dict[str, int], tmp_path: Path
) -> None:
    http = console(store, tmp_path)
    media = seeded["media"]
    page = http.get(f"/ui/incidents/{media}", headers=OPERATOR).text
    assert "Still open?" in page
    assert "cannot be asked" in page
    assert "Close by hand" in page
    assert "Re-check now" not in page  # no source here can be asked
    viewer = http.get(f"/ui/incidents/{media}", headers=VIEWER).text
    assert "take the operator role" in viewer
    form = {"note": "the box was rebooted"}
    assert http.post(f"/ui/incidents/{media}/close", data=form, headers=VIEWER).status_code == 403
    assert (
        http.post(f"/ui/incidents/{media}/close", data={"note": ""}, headers=OPERATOR).status_code
        == 422
    )
    done = http.post(f"/ui/incidents/{media}/close", data=form, headers=OPERATOR)
    assert done.headers["location"] == f"/ui/incidents/{media}?done=closed#status"
    after = http.get(f"/ui/incidents/{media}?done=closed", headers=OPERATOR).text
    assert "Closed by hand" in after
    assert "the box was rebooted" in after
    assert "marco" in after
    assert http.post(f"/ui/incidents/{media}/close", data=form, headers=OPERATOR).status_code == 409
    log = http.get("/ui/log?show=feedback", headers=OPERATOR).text
    assert "Closed by hand by marco: the box was rebooted" in log


def test_an_incident_none_of_whose_alerts_was_seen_is_marked_quiet(store: Store) -> None:
    _, incident_id = opened(store)
    incident = store.incident(incident_id)
    assert incident is not None
    assert views.quiet_days(store, incident, T0 + timedelta(hours=20)) is None
    assert views.quiet_days(store, incident, T0 + timedelta(days=3, hours=1)) == 3
    bays = {bay.key: bay for bay in views.board(store, T0 + timedelta(days=3, hours=1))}
    assert "quiet 3 days" in bays["open"].strips[0].flags


# --- postmortems ------------------------------------------------------------------------------

DRAFT = """---
id: 2026-11-20-vpn
incident: 21
status: draft
severity: critical
---

# VPN port forwarding

> Draft written by Paeon from its event log. Review before relying on it.

## Summary

_To be written at review._

## Action items

- [ ] Check connectability, not only the port sync
"""


def test_a_draft_is_saved_reviewed_and_set_back(tmp_path: Path) -> None:
    (tmp_path / "vpn.md").write_text(DRAFT)
    assert postmortems.missing_prose(DRAFT)
    reviewed = postmortems.set_status(tmp_path, "vpn.md", "reviewed")
    assert reviewed.status == "reviewed"
    text = (tmp_path / "vpn.md").read_text()
    assert "status: reviewed\n" in text
    assert text.count("status:") == 1
    assert text.replace("status: reviewed", "status: draft") == DRAFT  # nothing else moved
    edited = DRAFT.replace("_To be written at review._", "The tunnel came back with a new port.")
    saved = postmortems.save_text(tmp_path, "vpn.md", edited)
    assert saved.status == "draft"
    assert not postmortems.missing_prose((tmp_path / "vpn.md").read_text())
    for broken, said in (
        ("# no front matter", "must start with its front matter"),
        ("---\nincident: 21\nstatus: maybe\n---\n", "status must be one of"),
        ("---\nincident: 22\nstatus: draft\n---\n", "incident of a postmortem cannot be changed"),
        ("---\nincident: [\n---\n", "not valid YAML"),
    ):
        with pytest.raises(postmortems.DraftError, match=said):
            postmortems.save_text(tmp_path, "vpn.md", broken)
    with pytest.raises(postmortems.DraftError, match="no longer exists"):
        postmortems.set_status(tmp_path, "gone.md", "reviewed")


def test_triage_reads_a_postmortem_once_it_is_reviewed(tmp_path: Path) -> None:
    (tmp_path / "vpn.md").write_text(DRAFT.replace("_To be written at review._", "port forwarding"))
    found = knowledge.search([tmp_path], "port forwarding", limit=3, drafts=False)
    assert found == []
    postmortems.set_status(tmp_path, "vpn.md", "reviewed")
    found = knowledge.search([tmp_path], "port forwarding", limit=3, drafts=False)
    assert [hit.path.rsplit("/", 1)[-1] for hit in found] == ["vpn.md"]


def test_an_operator_reviews_and_edits_a_postmortem(
    store: Store, seeded: dict[str, int], tmp_path: Path
) -> None:
    directory = tmp_path / "postmortems"
    directory.mkdir(exist_ok=True)
    (directory / "2026-11-20-vpn.md").write_text(DRAFT)
    http = console(store, tmp_path)
    page = http.get("/ui/postmortems/2026-11-20-vpn.md", headers=OPERATOR).text
    assert "Mark reviewed" in page
    assert "Edit the text" in page
    assert 'class="nav-count"' in page or "to review" in http.get("/ui", headers=OPERATOR).text
    assert (
        http.post(
            "/ui/postmortems/2026-11-20-vpn.md/status", data={"to": "reviewed"}, headers=VIEWER
        ).status_code
        == 403
    )
    done = http.post(
        "/ui/postmortems/2026-11-20-vpn.md/status", data={"to": "reviewed"}, headers=OPERATOR
    )
    assert done.headers["location"] == "/ui/postmortems/2026-11-20-vpn.md?done=reviewed"
    assert "status: reviewed" in (directory / "2026-11-20-vpn.md").read_text()
    reviewed = next(store.events(kind="postmortem.reviewed"))
    assert reviewed.payload == {"path": "2026-11-20-vpn.md", "status": "reviewed", "by": "marco"}

    editor = http.get("/ui/postmortems/2026-11-20-vpn.md/edit", headers=OPERATOR)
    assert editor.status_code == 200
    assert "Check connectability" in editor.text
    bad = http.post(
        "/ui/postmortems/2026-11-20-vpn.md/edit", data={"text": "no front matter"}, headers=OPERATOR
    )
    assert bad.status_code == 422
    assert "Not saved" in bad.text
    assert "no front matter" in bad.text
    new = (directory / "2026-11-20-vpn.md").read_text().replace("- [ ]", "- [x]")
    saved = http.post(
        "/ui/postmortems/2026-11-20-vpn.md/edit", data={"text": new}, headers=OPERATOR
    )
    assert saved.headers["location"].endswith("?done=saved")
    assert "- [x] Check connectability" in (directory / "2026-11-20-vpn.md").read_text()


def test_a_postmortem_holding_a_secret_is_not_edited_in_the_console(
    store: Store, seeded: dict[str, int], tmp_path: Path
) -> None:
    directory = tmp_path / "postmortems"
    directory.mkdir(exist_ok=True)
    leaked = DRAFT.replace("## Action items", "token: ghp_" + "a" * 36 + "\n\n## Action items")
    (directory / "leak.md").write_text(leaked)
    http = console(store, tmp_path)
    page = http.get("/ui/postmortems/leak.md", headers=OPERATOR).text
    assert "Edit the text" not in page
    assert "edit the file itself" in page
    assert http.get("/ui/postmortems/leak.md/edit", headers=OPERATOR).status_code == 409
    assert (
        http.post(
            "/ui/postmortems/leak.md/edit", data={"text": DRAFT}, headers=OPERATOR
        ).status_code
        == 409
    )


def test_an_invalid_answer_is_asked_again_once(store: Store, tmp_path: Path) -> None:
    from test_postmortem import Writer as Model
    from test_postmortem import investigated_incident, rules

    class Twice(Model):
        def __init__(self) -> None:
            super().__init__('{"title": "x", "summary": "unterminated')
            self.calls = 0

        def converse(self, **kwargs: Any) -> Any:
            self.calls += 1
            if self.calls == 2:
                self.answer = '{"title": "VPN", "summary": "The tunnel came back with a new port."}'
            return super().converse(**kwargs)

    config = rules(tmp_path / "pm")
    investigated_incident(store, config)
    model = Twice()
    postmortems.PostmortemWriter(store, config, model).run_pending(T0 + timedelta(days=30))
    assert model.calls == 2
    assert "Your previous answer was not valid JSON" in model.inputs[1]
    text = next((tmp_path / "pm").iterdir()).read_text()
    assert "The tunnel came back with a new port." in text
    errors = [e.payload.get("error") for e in store.events(kind="model.call")]
    assert errors == ["invalid_output", None]


def test_the_remediator_mirrors_the_journal_after_an_answer(store: Store) -> None:
    from paeon.remediation import Remediator

    class Executor:
        def __init__(self) -> None:
            self.read = 0

        def answer(self, proposal_id: int, answer: str, by: str) -> dict[str, Any]:
            return {"ok": True, "message": "Approved."}

        def journal(self, after: int) -> Journal:
            self.read += 1
            return Journal([], after)

    executor = Executor()
    remediator = Remediator(store, parse_rules({}), executor)  # type: ignore[arg-type]
    assert remediator.answer(3, "go", "marco") == (True, "Approved.")
    assert executor.read == 1
