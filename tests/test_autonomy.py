"""Earned autonomy: the record decides what is proposed; a person decides what changes."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from acting import rules
from paeon import autonomy
from paeon.rules import RulesError, parse_rules
from paeon.store import Store
from support import T0

WORKER = ["worker_down", "restart_container", "worker"]


def run(store: Store, outcome: str = "verified", days_ago: float = 0, **more: Any) -> None:
    store.append(
        "plan.completed",
        {"outcome": outcome, "level": "approve", "tuples": [WORKER], **more},
        incident_id=1,
        now=T0 - timedelta(days=days_ago),
    )


def test_five_verified_runs_in_a_row_earn_a_proposal(store: Store, tmp_path: Path) -> None:
    actions = rules(tmp_path).actions
    for _ in range(4):
        run(store)
    (record,) = autonomy.matrix(store.events(), actions, T0)
    assert (record.runs, record.verified, record.streak) == (4, 4, 4)
    assert (record.policy, record.effective, record.proposal) == ("approve", "approve", None)
    run(store)
    (record,) = autonomy.matrix(store.events(), actions, T0)
    assert record.proposal == (
        '- { alert: "worker_down", action: restart_container, target: "worker", level: auto }'
    )
    # The line it proposes is a valid policy, and it gives what it says.
    policy = {"alert": "worker_down", "action": "restart_container", "target": "worker"}
    promoted = rules(tmp_path, policies=[{**policy, "level": "auto"}]).actions
    assert promoted.level_for(*WORKER) == "auto"
    assert autonomy.matrix(store.events(), promoted, T0)[0].proposal is None  # already there


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("plan.completed", {"outcome": "failed", "tuples": [WORKER]}),
        ("action.undone", {"tuples": [WORKER]}),
        ("feedback.recorded", {"about": "action", "vote": "down", "tuples": [WORKER]}),
    ],
)
def test_one_bad_sign_resets_the_count(
    store: Store, tmp_path: Path, kind: str, payload: dict[str, Any]
) -> None:
    for _ in range(5):
        run(store)
    store.append(kind, payload, incident_id=1, now=T0)
    run(store)
    (record,) = autonomy.matrix(store.events(), rules(tmp_path).actions, T0)
    assert record.streak == 1
    assert record.proposal is None
    assert record.failed + record.undone + record.wrong == 1


def test_old_runs_and_other_feedback_do_not_count(store: Store, tmp_path: Path) -> None:
    for _ in range(5):
        run(store, days_ago=120)
    store.append("feedback.recorded", {"about": "triage", "vote": "down"}, incident_id=1)
    (record,) = autonomy.matrix(store.events(), rules(tmp_path).actions, T0)
    assert (record.verified, record.streak, record.wrong) == (5, 0, 0)


def test_demotion_holds_auto_at_approve_until_reinstated(store: Store, tmp_path: Path) -> None:
    actions = rules(tmp_path).actions
    stalled = {"alert": "worker_stalled", "action": "restart_container", "target": "worker"}
    store.append("autonomy.demoted", {**stalled, "reason": "a verification failed"})
    (record,) = autonomy.matrix(store.events(), actions, T0)
    assert (record.policy, record.effective) == ("auto", "approve")
    assert record.demoted == "a verification failed"
    assert autonomy.demotions(store.events()) == {
        ("worker_stalled", "restart_container", "worker"): "a verification failed"
    }
    store.append("autonomy.reinstated", stalled)
    assert autonomy.demotions(store.events()) == {}
    assert autonomy.matrix(store.events(), actions, T0) == []  # no run, nothing to show


def test_levels_and_the_kill_switch(store: Store) -> None:
    assert autonomy.lowest(["auto", "approve", "auto"]) == "approve"
    assert autonomy.lowest([]) == "observe"
    assert autonomy.tuples_of({"tuples": [WORKER, "junk", ["a", "b"]]}) == [tuple(WORKER)]
    assert not autonomy.killed(store.events())
    store.append("killswitch.engaged", {"via": "link"})
    assert autonomy.killed(store.events())
    store.append("killswitch.released", {"via": "cli"})
    assert not autonomy.killed(store.events())
    assert '\\"' in autonomy.policy_line('say "hi"', "restart_container", "worker")


def test_the_postmortem_review_tells_what_was_done(store: Store) -> None:
    def events() -> list[Any]:
        return list(store.events())

    assert autonomy.level_in_force(events()) == "observe"
    assert autonomy.review(events(), 2)[0].endswith("It suggested 2 steps.")
    store.append("plan.proposed", {"level": "approve", "steps": ["restart_container(worker)"]})
    assert autonomy.level_in_force(events()) == "approve"
    assert autonomy.review(events(), 0)[1:] == [
        "- Nothing was executed.",
        "- Could this be handled at a higher autonomy level next time?",
    ]
    store.append("approval.expired", {})
    assert "expired without an answer" in autonomy.review(events(), 0)[1]
    store.append("approval.declined", {})
    assert "Declined" in autonomy.review(events(), 0)[1]
    store.append("approval.granted", {})
    store.append("action.rolled_back", {})
    store.append("action.undone", {})
    store.append("plan.completed", {"outcome": "failed"})
    store.append("autonomy.demoted", {})
    assert autonomy.review(events(), 0) == [
        "- Plan proposed at level `approve`: restart_container(worker).",
        "- Approved by a person (Go).",
        "- Executed: failed, rolled back; undone on request.",
        "- A tuple demoted by this incident stays at `approve` until you reinstate it.",
    ]


@pytest.mark.parametrize(
    ("actions", "message"),
    [
        ({"catalog": {"reboot_host": {"targets": ["*"]}}}, "'reboot_host' is not an action"),
        ({"catalog": {"stop_container": {"targets": ["*"]}}}, "is not an action"),
        ({"policies": [{"action": "reboot", "level": "auto"}]}, "'reboot' is not an action"),
        ({"policies": [{"level": "always"}]}, "level"),
        ({"catalog": {"restart_container": {"targets": []}}}, "targets"),
        (
            {
                "catalog": {"restart_container": {"targets": ["worker"]}},
                "runbooks": [
                    {
                        "name": "x",
                        "match": {},
                        "steps": [{"action": "restart_container", "target": "db"}],
                    }
                ],
            },
            "restart_container on db is not allowed",
        ),
        ({"public_url": "not a url"}, "public_url"),
    ],
)
def test_the_rules_refuse_what_the_catalog_cannot_hold(
    actions: dict[str, Any], message: str
) -> None:
    with pytest.raises(RulesError, match=message):
        parse_rules({"actions": actions})


def test_policies_first_match_wins_and_runbooks_need_actions_on(tmp_path: Path) -> None:
    actions = rules(tmp_path).actions
    assert actions.level_for("worker_stalled", "restart_container", "worker") == "auto"
    assert actions.level_for("worker_stalled", "start_container", "worker") == "approve"
    assert actions.level_for("disk_full", "restart_container", "worker") == "suggest"
    from paeon.schema import Status
    from support import alert as make

    firing = make(Status.FIRING, 0, title="worker_stalled")
    assert actions.runbook_for(firing) is not None
    assert rules(tmp_path, enabled=False).actions.runbook_for(firing) is None
    with pytest.raises(RulesError, match="fallback_model"):
        parse_rules(
            {
                "models": {"m": {"backend": "openai", "model": "x"}},
                "investigation": {"enabled": True, "model": "m", "fallback_model": "other"},
            }
        )
