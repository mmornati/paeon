"""The executor: it trusts its own rules, asks before it acts, and checks what it did."""

from __future__ import annotations

import fcntl
import os
from datetime import timedelta
from pathlib import Path

import pytest

from acting import Bench, FakeDocker, proposal
from paeon import autonomy
from paeon.executor import target_lock
from support import T0

LATER = T0 + timedelta(minutes=5)


@pytest.mark.parametrize(
    ("steps", "reason"),
    [
        ([("delete_volume", "worker")], "delete_volume is not in the catalog"),
        ([("stop_container", "worker")], "stop_container is not in the catalog"),
        ([("restart_container", "db")], "restart_container is not allowed on db"),
        ([("restart_container", "media-db")], "restart_container is not allowed on media-db"),
    ],
)
def test_a_plan_outside_the_catalog_is_refused(
    tmp_path: Path, steps: list[tuple[str, str]], reason: str
) -> None:
    bench = Bench(tmp_path)
    assert bench.executor.propose(proposal(*steps), T0) == {"status": "refused", "reason": reason}
    assert bench.kinds() == ["plan.refused"]
    assert bench.docker.posts == []
    assert bench.sent == []


def test_a_runbook_must_be_one_of_the_executors_own(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    forged = proposal(
        ("restart_container", "vpn"), origin="runbook", runbook="worker-stalled", mode="escalate"
    )
    assert bench.executor.propose(forged, T0)["status"] == "refused"


def test_default_level_only_records_a_suggestion(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    answer = bench.executor.propose(proposal(alert="something_else"), T0)
    assert answer == {
        "status": "recorded",
        "proposal": 1,
        "level": "suggest",
        "held_back_by": [
            "restart_container(worker): no policy matches, the default level is suggest"
        ],
    }
    assert bench.kinds() == ["plan.proposed"]
    assert bench.sent == []
    assert bench.docker.posts == []
    # `observe` is a policy like another.
    assert bench.executor.propose(proposal(alert="watched", incident=2), T0)["level"] == "observe"


def test_approve_asks_and_go_runs_verifies_and_reports(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    answer = bench.executor.propose(proposal(), T0)
    assert answer == {"status": "pending", "proposal": 1, "level": "approve", "held_back_by": []}
    assert bench.docker.posts == []  # nothing runs without Go
    asked = bench.sent[0]
    assert asked["title"] == "Incident #1: run this fix?"
    assert "1. Restart a container (worker)" in asked["message"]
    assert asked["click"] == "https://paeon.example.org/ui/incidents/1"
    assert [a["label"] for a in asked["actions"]] == ["Go", "No-go"]

    go = bench.link("Go")
    # Opening the link shows what it does and applies nothing.
    assert bench.executor.describe(go, LATER) == (
        "Run the plan of incident #1: restart_container(worker)."
    )
    assert bench.docker.posts == []

    outcome = bench.executor.act(go, LATER)
    assert outcome.ok
    assert bench.docker.posts == ["restart worker"]
    assert bench.kinds() == [
        "plan.proposed",
        "approval.requested",
        "notification.sent",
        "approval.granted",
        "action.started",
        "action.verified",
        "plan.completed",
        "notification.sent",
        "notification.sent",  # the tap is acknowledged
    ]
    done = bench.last("plan.completed")
    assert done["outcome"] == "verified"
    assert done["level"] == "approve"
    assert done["tuples"] == [["worker_down", "restart_container", "worker"]]
    result = bench.sent[1]
    assert result["title"] == "Incident #1: fix applied"
    # A restart has no inverse: no Undo, but always a way to say it was wrong.
    assert [a["label"] for a in result["actions"]] == ["This was wrong", "Stop automation"]

    # The tap itself is answered: a button gives no sign of its own that it arrived.
    received = bench.sent[2]
    assert received["title"] == "Incident #1: Go received"
    assert "1. Restart a container (worker)" in received["message"]
    assert received["actions"] == []

    # A link works once, and No-go is dead once Go was given.
    assert bench.executor.act(go, LATER).message == "this link was already used"
    assert "no longer waiting" in bench.executor.act(bench.link("No-go", 0), LATER).message
    assert bench.docker.posts == ["restart worker"]
    # A dead button says so, once: tapping it five times is one message, not five.
    assert bench.executor.act(go, LATER).message == "this link was already used"
    assert bench.titles()[3:] == [
        "Incident #1: that button did nothing",
        "Incident #1: that button did nothing",
    ]
    assert bench.sent[3]["message"].startswith("This link was already used.")
    assert "This plan is no longer waiting (it is done)." in bench.sent[4]["message"]
    assert "Nothing was run. Look for a newer message" in bench.sent[4]["message"]


def test_a_forged_link_gets_no_answer_and_an_expired_one_does(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    bench.executor.propose(proposal(), T0)
    go = bench.link("Go")
    body, _, signature = go.partition(".")
    for forged in (f"{body}.{signature[:-2]}xx", "x.y", ""):
        assert bench.executor.act(forged, LATER).message == "this link is not valid"
    assert bench.titles() == ["Incident #1: run this fix?"]  # nobody of ours is waiting
    assert bench.executor.act(go, T0 + timedelta(hours=2)).message == "this link has expired"
    assert bench.titles()[-1] == "Incident #1: that button did nothing"
    assert bench.sent[-1]["message"].startswith("This link has expired.")
    assert bench.docker.posts == []


def test_every_kind_of_tap_is_acknowledged(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    bench.executor.propose(proposal(), T0)
    bench.executor.act(bench.link("No-go"), LATER)
    assert (bench.titles()[-1], bench.sent[-1]["message"]) == (
        "Incident #1: No-go received",
        "Declined. Nothing was run.",
    )
    vote = bench.executor.vote_links(7, "triage", T0)["up"].removeprefix(
        "https://act.example.org/act/"
    )
    bench.executor.act(vote, LATER)
    assert (bench.titles()[-1], bench.sent[-1]["priority"]) == ("Incident #7: feedback recorded", 1)

    auto = Bench(tmp_path / "auto", policies=[{"level": "auto"}])
    auto.executor.propose(proposal(), T0)
    auto.executor.act(auto.link("This was wrong"), LATER)
    assert auto.titles()[-1] == "Incident #1: recorded as wrong"
    auto.executor.act(auto.link("Stop automation"), LATER)
    assert auto.titles()[-1] == "Paeon: automation stopped"
    assert auto.sent[-1]["click"] == "https://paeon.example.org/ui/actions"


def test_no_go_expiry_and_cancel_run_nothing(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    bench.executor.propose(proposal(incident=1), T0)
    assert bench.executor.act(bench.link("No-go"), LATER).message == "Declined. Nothing was run."
    assert (
        "no longer waiting (it is declined)" in bench.executor.act(bench.link("Go"), LATER).message
    )

    bench.executor.propose(proposal(incident=2), T0)
    go = bench.link("Go")
    assert bench.executor.expire(T0 + timedelta(minutes=29)) == 0
    assert bench.executor.expire(T0 + timedelta(minutes=31)) == 1
    assert bench.sent[-1]["title"] == "Incident #2: no answer, nothing was run"
    assert bench.sent[-1]["actions"] == []
    assert bench.executor.act(go, T0 + timedelta(minutes=32)).message == "this link has expired"

    third = bench.executor.propose(proposal(incident=3), T0)["proposal"]
    assert bench.executor.cancel(third, "the incident resolved")
    assert not bench.executor.cancel(third, "again")
    assert not bench.executor.cancel(99, "unknown")
    assert not bench.executor.act(bench.link("Go"), LATER).ok
    assert bench.docker.posts == []
    assert [k for k in bench.kinds() if k.startswith(("approval.", "plan.c"))] == [
        "approval.requested",
        "approval.declined",
        "approval.requested",
        "approval.expired",
        "approval.requested",
        "plan.cancelled",
    ]


def test_a_new_plan_kills_the_links_of_the_old_one_and_a_retry_is_the_same_plan(
    tmp_path: Path,
) -> None:
    bench = Bench(tmp_path)
    first = bench.executor.propose(proposal(), T0)
    assert bench.executor.propose(proposal(), T0) == first  # the core retried
    assert len(bench.sent) == 1
    old_go = bench.link("Go")
    bench.executor.propose(proposal(("restart_container", "vpn")), T0)
    assert bench.last("plan.cancelled") == {"proposal": 1, "reason": "superseded"}
    assert "no longer waiting (it is cancelled)" in bench.executor.act(old_go, LATER).message
    assert bench.docker.posts == []


def test_auto_runs_then_notifies_and_this_was_wrong_demotes(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    answer = bench.executor.propose(proposal(alert="worker_stalled", confidence=0.95), T0)
    assert answer["level"] == "auto"
    assert bench.docker.posts == ["restart worker"]
    assert "approval.requested" not in bench.kinds()
    assert "Ran without asking (auto):" in bench.sent[0]["message"]

    wrong = bench.link("This was wrong")
    assert "Record as wrong" in bench.executor.describe(wrong, LATER)
    assert bench.executor.act(wrong, LATER).ok
    assert bench.last("autonomy.demoted") == {
        "alert": "worker_stalled",
        "action": "restart_container",
        "target": "worker",
        "reason": "you said the action was wrong",
    }
    # The same tuple now asks, until the owner reinstates it.
    again = bench.executor.propose(proposal(alert="worker_stalled", incident=2), T0)
    assert again["level"] == "approve"
    assert "is demoted" in bench.last("plan.proposed")["held_back_by"][0]
    bench.store.append(
        "autonomy.reinstated",
        {"alert": "worker_stalled", "action": "restart_container", "target": "worker"},
    )
    assert (
        bench.executor.propose(proposal(alert="worker_stalled", incident=3), T0)["level"] == "auto"
    )


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"alerts": ("worker_stalled", "disk_full")}, "more than one alert class"),
        ({"confidence": 0.5}, "confidence is under the threshold"),
    ],
)
def test_auto_is_held_back_to_approve(
    tmp_path: Path, change: dict[str, object], reason: str
) -> None:
    bench = Bench(tmp_path, policies=[{"level": "auto"}])
    answer = bench.executor.propose(proposal(alert="worker_stalled", **change), T0)  # type: ignore[arg-type]
    assert answer["level"] == "approve"
    assert reason in bench.last("plan.proposed")["held_back_by"][0]
    assert bench.docker.posts == []


def test_one_automated_attempt_per_incident_and_a_rate_limit(tmp_path: Path) -> None:
    bench = Bench(tmp_path, policies=[{"level": "auto"}], max_per_hour=2)
    assert bench.executor.propose(proposal(), T0)["level"] == "auto"
    second = bench.executor.propose(proposal(("restart_container", "vpn")), T0)
    assert second["level"] == "approve"
    assert bench.last("plan.proposed")["held_back_by"] == ["a plan already ran for this incident"]
    assert bench.executor.propose(proposal(incident=2), T0)["level"] == "auto"
    limited = bench.executor.propose(proposal(incident=3), T0 + timedelta(minutes=1))
    assert limited["level"] == "suggest"
    assert bench.docker.posts == ["restart worker", "restart worker"]


def test_without_a_channel_or_a_docker_endpoint_nothing_goes_above_suggest(tmp_path: Path) -> None:
    quiet = Bench(tmp_path / "a", signer=False)
    assert quiet.executor.propose(proposal(), T0)["level"] == "suggest"
    assert "no approval channel" in quiet.last("plan.proposed")["held_back_by"][0]
    assert quiet.executor.vote_links(1, "triage", T0) == {}
    assert not quiet.executor.act("x.y", T0).ok
    handless = Bench(tmp_path / "b", with_docker=False, policies=[{"level": "auto"}])
    assert handless.executor.propose(proposal(), T0)["level"] == "suggest"
    off = Bench(tmp_path / "c", enabled=False, runbooks=[])
    assert off.executor.propose(proposal(), T0)["level"] == "observe"


def test_a_failed_verification_stops_the_plan_and_demotes(tmp_path: Path) -> None:
    docker = FakeDocker(worker={}, vpn={})
    docker.broken.add("worker")
    bench = Bench(tmp_path, docker=docker, policies=[{"level": "auto"}])
    plan = proposal(("restart_container", "worker"), ("restart_container", "vpn"))
    bench.executor.propose(plan, T0)
    assert docker.posts == ["restart worker"]  # a sequence stops at the first failure
    assert bench.last("action.failed")["reason"] == "the container is not running"
    assert bench.last("plan.completed")["outcome"] == "failed"
    assert bench.last("autonomy.demoted")["reason"] == "a verification failed"
    assert bench.sent[0]["title"] == "Incident #1: fix failed"
    assert bench.sent[0]["priority"] == 5
    assert bench.store.proposals(proposal_id=1)[0].status == "failed"
    assert bench.slept.count(5.0) == 2  # polled for the whole timeout


def test_a_runbook_escalates_until_a_step_works(tmp_path: Path) -> None:
    docker = FakeDocker(worker={}, vpn={})
    docker.broken.add("worker")
    bench = Bench(tmp_path, docker=docker)
    book = proposal(
        ("restart_container", "worker"),
        ("restart_container", "vpn"),
        alert="worker_stalled",
        origin="runbook",
        runbook="worker-stalled",
        mode="escalate",
        confidence=1.0,
    )
    assert bench.executor.propose(book, T0)["level"] == "auto"
    assert docker.posts == ["restart worker", "restart vpn"]
    done = bench.last("plan.completed")
    assert (done["outcome"], done["origin"]) == ("verified", "runbook")
    assert "autonomy.demoted" not in bench.kinds()  # the first step failing is the runbook working


def test_health_checks_decide(tmp_path: Path) -> None:
    docker = FakeDocker(worker={"Health": {"Status": "unhealthy"}})
    bench = Bench(tmp_path, docker=docker, policies=[{"level": "auto"}])
    bench.executor.propose(proposal(), T0)
    assert bench.last("action.failed")["reason"] == "its health check says unhealthy"

    docker = FakeDocker(worker={"Health": {"Status": "healthy"}})
    slow = Bench(
        tmp_path / "url",
        docker=docker,
        policies=[{"level": "auto"}],
        catalog={
            "restart_container": {
                "targets": ["worker"],
                "verify": {"wait_seconds": 3, "http": "http://worker:8080/healthz"},
            }
        },
        runbooks=[],
    )
    slow.healthy_url = False
    slow.executor.propose(proposal(), T0)
    assert slow.last("action.failed")["reason"] == "the health URL does not answer"
    assert slow.slept[0] == 3


def test_start_is_checked_first_rolled_back_on_failure_and_can_be_undone(tmp_path: Path) -> None:
    start = ("start_container", "worker")
    docker = FakeDocker(worker={"Running": True})
    bench = Bench(tmp_path / "running", docker=docker, policies=[{"level": "auto"}])
    bench.executor.propose(proposal(start), T0)
    assert bench.last("action.failed")["reason"] == "it is already running"
    assert docker.posts == []  # the world was not as the plan assumed

    docker = FakeDocker(worker={"Running": False})
    docker.broken.add("worker")
    bench = Bench(tmp_path / "broken", docker=docker, policies=[{"level": "auto"}])
    bench.executor.propose(proposal(start), T0)
    assert docker.posts == ["start worker", "stop worker"]
    assert "action.rolled_back" in bench.kinds()
    assert bench.last("plan.completed")["rolled_back"] is True

    docker = FakeDocker(worker={"Running": False})
    bench = Bench(tmp_path / "fine", docker=docker, policies=[{"level": "auto"}])
    bench.executor.propose(proposal(start), T0)
    assert [a["label"] for a in bench.sent[-1]["actions"]] == [
        "Undo",
        "This was wrong",
        "Stop automation",
    ]
    undo = bench.link("Undo")
    assert bench.executor.describe(undo, LATER).startswith("Undo the plan of incident #1")
    assert bench.executor.act(undo, LATER).message.startswith("Undone.")
    assert docker.posts == ["start worker", "stop worker"]
    assert bench.last("action.undone")["applied"] is True
    assert bench.last("autonomy.demoted")["reason"] == "you undid the action"
    assert bench.store.proposals(proposal_id=1)[0].status == "undone"
    # What was undone cannot also be marked wrong: the plan is no longer "done".
    assert "did not run" in bench.executor.act(bench.link("This was wrong"), LATER).message


def test_unpause_needs_a_paused_container(tmp_path: Path) -> None:
    docker = FakeDocker(worker={"Paused": True})
    bench = Bench(tmp_path, docker=docker, policies=[{"level": "auto"}])
    bench.executor.propose(proposal(("unpause_container", "worker")), T0)
    assert docker.posts == ["unpause worker"]
    bench.executor.propose(proposal(("unpause_container", "worker"), incident=2), T0)
    assert bench.last("action.failed")["reason"] == "it is not paused"


def test_docker_errors_a_missing_container_and_a_held_lock_fail_the_step(tmp_path: Path) -> None:
    auto = [{"level": "auto"}]
    bench = Bench(tmp_path / "missing", docker=FakeDocker(worker={}), policies=auto)
    bench.executor.propose(proposal(("restart_container", "vpn")), T0)
    assert bench.last("action.failed")["reason"] == "no such container"

    docker = FakeDocker(worker={})
    docker.status = 500
    bench = Bench(tmp_path / "error", docker=docker, policies=auto)
    bench.executor.propose(proposal(), T0)
    assert bench.last("action.failed")["reason"] == "docker: docker answered HTTP 500"

    # Another repairer (a watchdog script) holds the container's lock file.
    bench = Bench(tmp_path / "locked", policies=auto)
    locks = tmp_path / "locked" / "locks"
    locks.mkdir()
    held = os.open(locks / "worker.lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        bench.executor.propose(proposal(), T0)
    finally:
        os.close(held)
    assert bench.last("action.failed")["reason"] == "another repair holds this container's lock"
    assert bench.docker.posts == []
    with target_lock(str(locks), "worker") as free:
        assert free


def test_the_kill_switch_records_everything_and_runs_nothing(tmp_path: Path) -> None:
    bench = Bench(tmp_path, policies=[{"level": "auto"}])
    bench.executor.propose(proposal(), T0)
    stop = bench.link("Stop automation")
    assert bench.executor.describe(stop, LATER).startswith("Stop automation")
    assert bench.executor.act(stop, LATER).ok
    assert bench.executor.killed()
    assert bench.executor.state(LATER)["kill_switch"] is True
    answer = bench.executor.propose(proposal(incident=2), LATER)
    assert answer["level"] == "observe"
    assert bench.last("plan.proposed")["held_back_by"] == ["the kill switch is engaged"]
    assert bench.docker.posts == ["restart worker"]

    bench.store.append("killswitch.released", {"via": "cli"})
    assert not bench.executor.killed()
    assert Bench(tmp_path / "env", kill_env=True).executor.killed()


def test_engaging_the_kill_switch_cancels_what_waits_and_stops_a_go(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    bench.executor.propose(proposal(), T0)
    go = bench.link("Go")
    bench.executor.engage_kill("core")
    assert bench.last("plan.cancelled")["reason"] == "the kill switch was engaged"
    assert not bench.executor.act(go, LATER).ok
    assert bench.docker.posts == []
    assert bench.executor.state(LATER)["pending"] == []

    # Engaged between the Go and the run: the approved plan is dropped, not executed.
    other = Bench(tmp_path / "race")
    other.executor.propose(proposal(), T0)
    other.store.move(1, to="approved", only_from=("pending",))
    other.store.append("killswitch.engaged", {"via": "cli"})
    other.executor.execute(1, "approve")
    assert other.docker.posts == []
    assert other.store.proposals(proposal_id=1)[0].status == "cancelled"


def test_vote_links_record_feedback_once(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    links = bench.executor.vote_links(4, "root_cause", T0)
    token = links["down"].removeprefix("https://act.example.org/act/")
    assert bench.executor.describe(token, LATER) == (
        "Record that the root cause of incident #4 was wrong."
    )
    assert bench.executor.act(token, LATER).ok
    event = next(bench.store.events(kind="feedback.recorded"))
    assert event.incident_id == 4
    assert event.payload == {"about": "root_cause", "vote": "down", "note": "", "via": "link"}
    assert not bench.executor.act(token, LATER).ok


def test_a_lost_notification_is_recorded(tmp_path: Path) -> None:
    bench = Bench(tmp_path)

    bench.fail("ntfy answered HTTP 500")
    bench.executor.propose(proposal(), T0)
    assert bench.last("notification.failed")["error"] == "ntfy answered HTTP 500"
    assert bench.executor.state(T0)["pending"][0]["steps"] == ["restart_container(worker)"]


def test_the_journal_is_what_the_core_mirrors(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    bench.executor.propose(proposal(), T0)
    journal = bench.executor.journal(0)
    assert [entry["kind"] for entry in journal] == bench.kinds()
    assert journal[0]["incident_id"] == 1
    assert bench.executor.journal(journal[-1]["id"]) == []
    records = autonomy.matrix(bench.store.events(), bench.rules.actions, LATER)
    assert records == []  # nothing ran yet


def test_a_restart_closes_what_it_interrupted_and_resumes_nothing(tmp_path: Path) -> None:
    bench = Bench(tmp_path, policies=[{"level": "auto"}])
    bench.executor._spawn = lambda job: None  # the process dies before the plan runs
    bench.executor.propose(proposal(alert="worker_stalled"), T0)
    assert bench.store.proposals(proposal_id=1)[0].status == "approved"
    assert bench.executor.head() == 1

    assert bench.executor.recover() == 1  # what the next start does
    assert bench.docker.posts == []
    assert bench.store.proposals(proposal_id=1)[0].status == "failed"
    done = bench.last("plan.completed")
    assert (done["outcome"], done["reason"]) == (
        "failed",
        "the executor restarted while the plan was running",
    )
    assert bench.sent[-1]["title"] == "Incident #1: fix failed"
    assert bench.last("autonomy.demoted")["target"] == "worker"
    assert bench.executor.recover() == 0


def test_a_burst_hits_the_rate_limit_before_any_plan_has_finished(tmp_path: Path) -> None:
    bench = Bench(tmp_path, policies=[{"level": "auto"}], max_per_hour=2)
    bench.executor._spawn = lambda job: None  # plans are approved and have not run yet
    levels = [
        bench.executor.propose(proposal(incident=number), T0)["level"] for number in (1, 2, 3)
    ]
    assert levels == ["auto", "auto", "suggest"]
    # And a second plan for an incident whose first is still queued does not run alone.
    again = bench.executor.propose(proposal(("restart_container", "vpn"), incident=1), T0)
    assert again["level"] == "suggest"
    assert "plan.completed" not in bench.kinds()


def test_reading_and_acting_can_go_through_two_proxies(tmp_path: Path) -> None:
    """The proxy that may act allows only the actions; listing goes through the read one."""
    import httpx2 as httpx

    from paeon.executor import DockerActuator

    docker = FakeDocker(worker={})
    seen: list[str] = []

    def write_only(request: httpx.Request) -> httpx.Response:
        seen.append(f"write {request.method} {request.url.path}")
        return httpx.Response(403) if request.method == "GET" else docker.handle(request)

    def read_only(request: httpx.Request) -> httpx.Response:
        seen.append(f"read {request.method} {request.url.path.rsplit('/', 1)[-1]}")
        return docker.handle(request) if request.method == "GET" else httpx.Response(403)

    actuator = DockerActuator(
        httpx.Client(base_url="http://act", transport=httpx.MockTransport(write_only)),
        httpx.Client(base_url="http://read", transport=httpx.MockTransport(read_only)),
    )
    bench = Bench(tmp_path, docker=docker, policies=[{"level": "auto"}])
    bench.executor._docker = actuator
    try:
        bench.executor.propose(proposal(), T0)
    finally:
        actuator.close()
    assert bench.last("plan.completed")["outcome"] == "verified"
    assert [line for line in seen if line.startswith("write")] == [
        f"write POST /containers/{'a' * 64}/restart"
    ]
    assert seen[0] == "read GET json"


def test_a_listing_the_proxy_refuses_fails_the_step_and_says_so(tmp_path: Path) -> None:
    import httpx2 as httpx

    from paeon.executor import DockerActuator

    actuator = DockerActuator(
        httpx.Client(
            base_url="http://act", transport=httpx.MockTransport(lambda r: httpx.Response(403))
        )
    )
    bench = Bench(tmp_path, policies=[{"level": "auto"}])
    bench.executor._docker = actuator
    try:
        bench.executor.propose(proposal(), T0)
    finally:
        actuator.close()
    assert bench.last("action.failed")["reason"] == (
        "docker: docker answered HTTP 403 to the listing"
    )


def test_page_buttons_show_the_page_and_the_page_runs_the_plan(tmp_path: Path) -> None:
    bench = Bench(tmp_path, buttons="page")
    bench.executor.propose(proposal(), T0)
    go, nogo = bench.sent[0]["actions"]
    assert (go["action"], go["label"], nogo["label"]) == ("view", "Go", "No-go")
    assert "method" not in go  # the phone opens it: a GET, which changes nothing
    token = str(go["url"]).removeprefix("https://act.example.org/act/")
    assert bench.executor.describe(token, LATER).startswith("Run the plan of incident #1")
    assert bench.docker.posts == []
    assert bench.executor.act(token, LATER).message.startswith("Approved.")
    assert bench.docker.posts == ["restart worker"]
    # The buttons of the result open a page too.
    later = [a for sent in bench.sent[1:] for a in sent["actions"]]
    assert later
    assert {a["action"] for a in later} == {"view"}
