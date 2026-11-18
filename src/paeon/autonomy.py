"""Earned autonomy: what the record says about each (alert class, action, target).

Policy is written by a person in the rules file. This module never changes it. It reads the
executor's events and answers three questions:

- which tuples are demoted (one failed verification, one Undo or one "This was wrong" sends a
  tuple back to `approve` until a person reinstates it);
- what each tuple's track record is;
- which tuples have earned a proposal: enough approved, verified runs in a row. The proposal is
  a line to add to the rules file, for a person to review like any other change.

The same functions serve the executor (on its own journal, where they decide) and the core (on
its mirror of that journal, where they only inform the console and the postmortems).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from paeon.rules import LEVELS, Actions, Level
from paeon.store import Event

Tuple = tuple[str, str, str]  # alert class (the alert's title), action, target
RANK: dict[str, int] = {level: rank for rank, level in enumerate(LEVELS)}
KINDS = (
    "plan.completed",
    "action.undone",
    "feedback.recorded",
    "autonomy.demoted",
    "autonomy.reinstated",
)


def lowest(levels: Iterable[Level]) -> Level:
    """The most careful of several levels: a plan is as free as its least free part."""
    return min(levels, key=RANK.__getitem__, default="observe")


def tuples_of(payload: dict[str, Any]) -> list[Tuple]:
    found = []
    for item in payload.get("tuples") or []:
        if isinstance(item, list | tuple) and len(item) == 3:
            found.append((str(item[0]), str(item[1]), str(item[2])))
    return found


def _named(payload: dict[str, Any]) -> Tuple:
    return (str(payload.get("alert")), str(payload.get("action")), str(payload.get("target")))


def demotions(events: Iterable[Event]) -> dict[Tuple, str]:
    """The tuples that are demoted now, each with the reason."""
    demoted: dict[Tuple, str] = {}
    for event in events:
        if event.kind == "autonomy.demoted":
            demoted[_named(event.payload)] = str(event.payload.get("reason") or "")
        elif event.kind == "autonomy.reinstated":
            demoted.pop(_named(event.payload), None)
    return demoted


def killed(events: Iterable[Event]) -> bool:
    """Whether the kill switch is engaged, from `killswitch.*` events in order."""
    engaged = False
    for event in events:
        if event.kind == "killswitch.engaged":
            engaged = True
        elif event.kind == "killswitch.released":
            engaged = False
    return engaged


@dataclass
class Record:
    alert: str
    action: str
    target: str
    policy: Level = "observe"  # what the rules file says
    demoted: str | None = None  # why it is held at `approve`, if it is
    runs: int = 0
    verified: int = 0
    failed: int = 0
    undone: int = 0
    wrong: int = 0
    streak: int = 0  # verified runs since the last bad sign, inside the window
    last_at: datetime | None = None
    proposal: str | None = None  # the policy line this record has earned

    @property
    def effective(self) -> Level:
        if self.demoted is not None and RANK[self.policy] > RANK["approve"]:
            return "approve"
        return self.policy


def policy_line(alert: str, action: str, target: str, level: Level = "auto") -> str:
    """One entry for `actions.policies`, as YAML on one line."""
    pattern = re.escape(alert).replace('"', '\\"')
    return f'- {{ alert: "{pattern}", action: {action}, target: "{target}", level: {level} }}'


def matrix(events: Iterable[Event], actions: Actions, now: datetime) -> list[Record]:
    """Every tuple that has a history, with its level, its record and what it has earned."""
    since = now - timedelta(days=actions.autonomy.window_days)
    records: dict[Tuple, Record] = {}

    def record(key: Tuple) -> Record:
        if key not in records:
            records[key] = Record(*key, policy=actions.level_for(*key))
        return records[key]

    history = sorted(events, key=lambda event: event.id)
    for event in history:
        payload = event.payload
        recent = event.recorded_at >= since
        if event.kind == "plan.completed":
            good = payload.get("outcome") == "verified"
            for key in tuples_of(payload):
                entry = record(key)
                entry.runs += 1
                entry.last_at = event.recorded_at
                entry.verified += int(good)
                entry.failed += int(not good)
                entry.streak = entry.streak + 1 if good and recent else 0
        elif event.kind == "action.undone":
            for key in tuples_of(payload):
                entry = record(key)
                entry.undone += 1
                entry.streak = 0
        elif (
            event.kind == "feedback.recorded"
            and payload.get("about") == "action"
            and payload.get("vote") == "down"
        ):
            for key in tuples_of(payload):
                entry = record(key)
                entry.wrong += 1
                entry.streak = 0
    for key, reason in demotions(history).items():
        record(key).demoted = reason
    for entry in records.values():
        earned = entry.streak >= actions.autonomy.promote_after
        if earned and entry.policy == "approve" and entry.demoted is None:
            entry.proposal = policy_line(entry.alert, entry.action, entry.target)
    return sorted(records.values(), key=lambda r: (r.alert, r.action, r.target))


# --- for the postmortem -----------------------------------------------------------------------


def level_in_force(events: Sequence[Event]) -> str:
    """The autonomy level the incident's last plan was handled at; `observe` without a plan."""
    for event in reversed(events):
        if event.kind == "plan.proposed":
            return str(event.payload.get("level") or "observe")
    return "observe"


def review(events: Sequence[Event], suggested: int) -> list[str]:
    """The lines of a postmortem's automation review that are about actions."""
    proposed = [e.payload for e in events if e.kind == "plan.proposed"]
    done = [e.payload for e in events if e.kind == "plan.completed"]
    if not proposed:
        return [
            "- Actions: none taken by Paeon (no plan reached the executor)."
            + (f" It suggested {suggested} steps." if suggested else ""),
            "- Could this be handled at a higher autonomy level next time?",
        ]
    last = proposed[-1]
    steps = ", ".join(str(step) for step in last.get("steps") or [])
    lines = [f"- Plan proposed at level `{last.get('level')}`: {steps}."]
    kinds = {e.kind for e in events}
    if "approval.granted" in kinds:
        lines.append("- Approved by a person (Go).")
    elif "approval.declined" in kinds:
        lines.append("- Declined by a person (No-go). Why?")
    elif "approval.expired" in kinds:
        lines.append("- The approval expired without an answer.")
    if done:
        outcome = done[-1].get("outcome")
        rolled = any(e.kind == "action.rolled_back" for e in events)
        lines.append(
            f"- Executed: {outcome}"
            + (", rolled back" if rolled else "")
            + ("; undone on request" if "action.undone" in kinds else "")
            + "."
        )
    else:
        lines.append("- Nothing was executed.")
    lines.append(
        "- A tuple demoted by this incident stays at `approve` until you reinstate it."
        if "autonomy.demoted" in kinds
        else "- Could this be handled at a higher autonomy level next time?"
    )
    return lines
