"""The console once Paeon may act: it shows plans and the record, stops automation, and
cannot approve anything."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from paeon.app import create_app
from paeon.config import Settings, load
from paeon.remediation import Journal
from paeon.rules import parse_rules
from paeon.store import Store
from paeon.ui import views
from paeon.ui.explorer import Query, explore
from ui_data import ACTING_RULES, populate, populate_actions

TOKEN = "t" * 32
SAME = {"Sec-Fetch-Site": "same-origin"}


class Switch:
    """Stands for the executor: it only needs to be told to stop."""

    def __init__(self) -> None:
        self.stopped = 0
        self.down = False
        self.answers: list[tuple[int, str, str]] = []

    def kill(self) -> None:
        if self.down:
            raise RuntimeError("unreachable")
        self.stopped += 1

    def journal(self, after: int) -> Journal:
        return Journal([], after)

    def propose(self, proposal: Any) -> dict[str, Any]:  # pragma: no cover - not used here
        return {}

    def cancel(self, proposal_id: int, reason: str) -> None:  # pragma: no cover
        return None

    def vote_links(self, incident_id: int, about: str) -> dict[str, str]:  # pragma: no cover
        return {}

    def answer(self, proposal_id: int, answer: str, by: str) -> dict[str, Any]:
        self.answers.append((proposal_id, answer, by))
        return {"ok": True, "message": "Approved. The plan is running; the result follows."}


@pytest.fixture
def seeded(store: Store, tmp_path: Path) -> dict[str, int]:
    ids = populate(store)
    populate_actions(store, ids, tmp_path / "postmortems")
    return ids


def client(store: Store, tmp_path: Path, switch: Switch | None = None) -> TestClient:
    base = load({"PAEON_INGEST_TOKEN": TOKEN})
    document = {
        **ACTING_RULES,
        "ui": {"enabled": True},
        "models": {"m": {"backend": "openai", "model": "x"}},
        "postmortems": {"enabled": True, "model": "m", "dir": str(tmp_path / "postmortems")},
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
    return TestClient(create_app(settings, store, executor=switch or Switch()))


def test_a_plan_waiting_for_go_needs_you(store: Store, seeded: dict[str, int]) -> None:
    now = datetime.now(UTC)
    page = explore(store, Query(status="needs"), now, ZoneInfo("UTC"), acting=True)
    (row,) = page.rows
    assert row.incident.id == seeded["media"]
    assert row.waiting
    assert [s.state for s in row.track][2:4] == ["model", "waiting"]
    incident = store.incident(seeded["media"])
    assert incident is not None
    strip = views.strip_of(incident, list(store.events(incident_id=incident.id)), now, acting=True)
    cells = {cell.label: cell for cell in strip.cells}
    assert len(strip.cells) == 8
    assert cells["Plan"].value == "restart_container(media-server)"
    assert (cells["Action"].value, cells["Action"].full) == ("waiting", "waiting (level approve)")
    everything = explore(store, Query(range="all"), now, ZoneInfo("UTC"), acting=True)
    worker = next(r for r in everything.rows if r.incident.id == seeded["worker"])
    assert {s.stage: s.state for s in worker.track}["action"] == "code"  # verified
    # Without actions nothing can wait for an answer: no "Needs you" tab.
    plain = explore(store, Query(), now, ZoneInfo("UTC"))
    assert [tab[0] for tab in plain.tabs] == ["all", "open", "resolved"]


def test_a_postmortem_without_a_diagram_keeps_the_strict_policy(
    store: Store, seeded: dict[str, int], tmp_path: Path
) -> None:
    (tmp_path / "postmortems" / "plain.md").write_text("# Plain\n\nNo diagram here.\n")
    page = client(store, tmp_path).get("/ui/postmortems/plain.md")
    assert "<p>No diagram here.</p>" in page.text
    assert "mermaid" not in page.text
    assert "unsafe-inline" not in page.headers["content-security-policy"]


def test_the_incident_page_tells_the_plan_and_cannot_approve(
    store: Store, seeded: dict[str, int], tmp_path: Path
) -> None:
    http = client(store, tmp_path)
    page = http.get(f"/ui/incidents/{seeded['media']}").text
    assert "Plan and action" in page
    assert "Waiting for your Go or No-go, on the notification sent to your phone." in page
    assert "Go is a signed link on your phone: answering in the console is off" in page
    assert "/act/" not in page  # no link to approve from here
    assert "/answer" not in page  # console_approval is off: nothing here runs anything

    worker = http.get(f"/ui/incidents/{seeded['worker']}").text
    assert "It ran without asking (auto). It ran, and the result was verified." in worker
    assert "From the runbook worker-stalled. Level auto." in worker
    assert "restart_container(worker) worked: the container is up and healthy" in worker
    failed = http.get(f"/ui/incidents/{seeded['queue']}").text
    assert "It ran and did not work: its health check says unhealthy." in failed
    assert "restart_container(queue) asks for approval from now on" in failed

    board = http.get("/ui").text
    assert "Needs you" in board
    assert "needs your answer" in board
    assert "Actions <b>by the executor</b>" in board
    assert "Only the executor acts" in board


def test_actions_page_and_the_kill_switch(
    store: Store, seeded: dict[str, int], tmp_path: Path
) -> None:
    switch = Switch()
    http = client(store, tmp_path, switch)
    page = http.get("/ui/actions").text
    assert "Off. Plans are handled at the level your policies give." in page
    assert "restart_container(media-server)" in page
    assert "Everything else" in page
    assert "worker-stalled" in page
    assert "restart_container on worker, then restart_container on vpn" in page
    assert 'outcome-failed">failed' in page

    # Stopping is a form post from the console itself, never from another site.
    assert http.post("/ui/actions/stop").status_code == 403
    cross = {"Sec-Fetch-Site": "cross-site"}
    assert http.post("/ui/actions/stop", headers=cross).status_code == 403
    assert switch.stopped == 0
    done = http.post("/ui/actions/stop", headers=SAME, follow_redirects=False)
    assert (done.status_code, done.headers["location"]) == (303, "/ui/actions?stopped=yes")
    assert switch.stopped == 1

    store.append("killswitch.engaged", {"via": "core"}, source="executor")
    stopped = http.get("/ui/actions").text
    assert "Engaged. Every plan is recorded and nothing else happens." in stopped
    assert "Stop automation</button>" not in stopped  # and no way to resume from here
    assert "/resume" not in stopped

    switch.down = True
    failed = http.post("/ui/actions/stop", headers=SAME, follow_redirects=False)
    assert failed.headers["location"] == "/ui/actions?stopped=failed"
    assert "automation was not stopped from here" in http.get(failed.headers["location"]).text


def test_autonomy_page_shows_the_record_and_what_was_earned(
    store: Store, seeded: dict[str, int], tmp_path: Path
) -> None:
    page = client(store, tmp_path).get("/ui/autonomy").text
    assert "1 action ran 5 times or more in a row, approved and verified." in page
    assert (
        "- { alert: &#34;slo_traefik_5xx&#34;, action: restart_container,"
        " target: &#34;media-server&#34;, level: auto }"
    ) in page
    assert "restart_container<wbr>(queue)" in page
    assert "Policy says auto; held at approve because a verification failed." in page
    view = views.autonomy_view(store, parse_rules(ACTING_RULES), datetime.now(UTC))
    by_target = {record.target: record for record in view.records}
    assert by_target["worker"].effective == "auto"
    assert by_target["queue"].effective == "approve"
    assert [record.target for record in view.earned] == ["media-server"]


def test_postmortems_are_listed_and_shown_with_secrets_masked(
    store: Store, seeded: dict[str, int], tmp_path: Path
) -> None:
    http = client(store, tmp_path)
    listing = http.get("/ui/postmortems").text
    assert "The job worker stopped taking jobs for nine minutes" in listing
    assert 'outcome-draft">draft' in listing
    name = "2026-11-17-worker-stalled.md"
    path = tmp_path / "postmortems" / name
    path.write_text(path.read_text() + "\npassword=hunter2hunter2\n")
    page = http.get(f"/ui/postmortems/{name}")
    assert page.status_code == 200
    assert "Add a liveness probe" in page.text
    # Rendered, not shown as markdown: headings, a table, ticked and empty boxes.
    assert "<h2>Timeline</h2>" in page.text
    assert "<strong>dead VPN route</strong>" in page.text
    assert "<th>What happened</th>" in page.text
    assert page.text.count('<input type="checkbox" disabled') == 2
    assert (
        'href="https://git.example/ops/runbooks/worker.md" rel="noreferrer noopener"' in page.text
    )
    # Nothing the file contains can run or link to a script.
    assert "<script>alert(1)</script>" not in page.text
    assert 'href="javascript' not in page.text
    assert "## Timeline" in page.text  # the source is still there to read
    # A mermaid block is drawn in the browser by the bundled library, never from another host.
    assert '<code class="language-mermaid">sequenceDiagram' in page.text
    assert 'src="/ui/static/vendor/mermaid.min.js' in page.text
    assert http.get("/ui/static/diagrams.js").status_code == 200
    policy = page.headers["content-security-policy"]
    assert "style-src 'self' 'unsafe-inline'" in policy
    assert "script-src 'self';" in policy
    assert "default-src 'none'" in policy
    assert "hunter2hunter2" not in page.text
    assert f"/ui/incidents/{seeded['worker']}" in page.text
    assert http.get("/ui/postmortems/missing.md").status_code == 404
    assert http.get("/ui/postmortems/..%2Fpaeon.sqlite").status_code == 404
    path.unlink()
    assert "No postmortem has been written yet." in http.get("/ui/postmortems").text


def test_the_new_pages_exist_only_with_what_they_show(store: Store, tmp_path: Path) -> None:
    base = load({"PAEON_INGEST_TOKEN": TOKEN})
    settings = Settings(
        db_path=base.db_path,
        host=base.host,
        port=base.port,
        ingest_token=TOKEN,
        openobserve=None,
        heartbeat_url=None,
        heartbeat_seconds=60,
        rules=parse_rules({"ui": {"enabled": True}}),
    )
    http = TestClient(create_app(settings, store))
    board = http.get("/ui").text
    for link in ("/ui/actions", "/ui/autonomy", "/ui/postmortems"):
        assert f'href="{link}"' not in board
    assert "It takes no action on this server." in board
    assert http.post("/ui/actions/stop", headers=SAME).status_code == 404
    assert "No plan was proposed." in http.get("/ui/actions").text
    assert "The catalog is empty" in http.get("/ui/actions").text
    assert "No action has run yet." in http.get("/ui/autonomy").text
    assert http.get("/ui/postmortems/x.md").status_code == 404


def test_every_executor_event_has_a_line(store: Store) -> None:
    samples: dict[str, dict[str, Any]] = {
        "investigation.fallback": {"reason": "model_error: HTTP 529", "to": "second"},
        "postmortem.committed": {"pushed": True},
        "postmortem.commit_failed": {"step": "push"},
        "runbook.skipped": {"reason": "resolved by itself"},
        "runbook.failed": {"reason": "the runbook's fix did not verify"},
        "plan.refused": {"reason": "x is not in the catalog"},
        "plan.not_proposed": {"reason": "unreachable: ConnectError"},
        "plan.cancelled": {"reason": "superseded"},
        "plan.proposed": {"level": "approve", "steps": ["a(b)"], "held_back_by": ["low"]},
        "approval.granted": {},
        "approval.declined": {},
        "approval.expired": {},
        "action.started": {"step": "a(b)"},
        "action.rolled_back": {"step": "a(b)"},
        "action.undone": {"step": "a(b)"},
        "autonomy.reinstated": {"action": "a", "target": "b"},
        "killswitch.engaged": {"via": "link"},
        "killswitch.released": {"via": "cli"},
        "feedback.recorded": {"about": "root_cause", "vote": "down"},
    }
    for kind, payload in samples.items():
        store.append(kind, payload, incident_id=1)
    lines = {event.kind: views.describe(event) for event in store.events()}
    assert all(line != kind for kind, line in lines.items())
    assert lines["plan.proposed"] == "Plan at level approve: a(b) (low)"
    assert lines["feedback.recorded"] == "You said the root cause was wrong"
    assert lines["killswitch.engaged"] == "Automation stopped (from the link)"
    assert views.voice_of("approval.granted") == "in"
    assert views.voice_of("approval.expired") == "code"
    assert views.voice_of("action.started") == "code"

    events = list(store.events())
    for kind, state in (
        ("plan.refused", "refused"),
        ("plan.not_proposed", "not sent"),
    ):
        plan = views.plan_of([e for e in events if e.kind == kind])
        assert plan is not None
        assert plan.state == state
    for level, later, state in (
        ("observe", [], "recorded"),
        ("suggest", [], "suggested"),
        ("approve", ["approval.requested", "approval.declined"], "declined"),
        ("approve", ["approval.requested", "approval.expired"], "expired"),
        ("approve", ["approval.requested", "plan.cancelled"], "cancelled"),
        ("approve", ["approval.requested", "approval.granted"], "running"),
        ("auto", ["plan.completed", "action.undone"], "undone"),
    ):
        fresh = Store(":memory:")
        try:
            fresh.append("plan.proposed", {"proposal": 3, "level": level, "steps": []})
            for kind in later:
                fresh.append(kind, {"proposal": 3, "outcome": "verified", "reason": "r"})
            plan = views.plan_of(list(fresh.events()))
            assert plan is not None
            assert (plan.state, plan.waiting) == (state, False)
        finally:
            fresh.close()


def test_a_proposed_change_is_shown_with_its_pull_request(
    store: Store, seeded: dict[str, int], tmp_path: Path
) -> None:
    media = seeded["media"]
    label = "config_change(compose: services.media-server.mem_limit = 2g)"
    store.append(
        "plan.proposed",
        {
            "proposal": 12,
            "kind": "change",
            "level": "approve",
            "origin": "investigation",
            "steps": [label],
        },
        source="executor",
        incident_id=media,
    )
    for url in ("https://github.com/example/deployment/pull/7", "javascript:alert(1)", None):
        store.append(
            "change.proposed",
            {"proposal": 12, "steps": [label], "branch": "paeon/incident-3-abc", "url": url},
            source="executor",
            incident_id=media,
        )
    page = client(store, tmp_path).get(f"/ui/incidents/{media}").text
    assert "Changes proposed" in page
    assert page.count('href="https://github.com/example/deployment/pull/7"') == 1
    assert "javascript:" not in page.replace("javascript:alert(1)&#34;", "")  # never as a link
    assert 'href="javascript' not in page
    assert page.count("branch pushed") == 2
    assert "Merging the pull request is your approval." in page
    # The plan that waits for Go is still the incident's plan: the change does not hide it.
    assert "Waiting for your Go or No-go" in page
    lines = {e.kind: views.describe(e) for e in store.events(incident_id=media)}
    assert lines["change.proposed"].startswith("Change proposed, nothing applied:")
    store.append("change.failed", {"proposal": 12, "reason": "git push failed"}, incident_id=media)
    failed = views.plan_of(
        [e for e in store.events(incident_id=media) if e.payload.get("proposal") == 12]
    )
    assert failed is not None
    assert (failed.state, failed.waiting) in {("proposed", False), ("failed", False)}
