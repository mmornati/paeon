"""Maintenance holds: incidents opened in a window wait for it to end, then are re-checked."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from paeon.correlation import Correlator
from paeon.maintenance import Holds, held
from paeon.operations import Answer, Checker, Incidents
from paeon.recorder import Recorder
from paeon.rules import Rules, parse_rules
from paeon.schema import IncidentStatus, Status, TriageState
from paeon.store import Store
from paeon.triage import Triager
from support import T0, alert
from test_triage import Scripted, answer

FIRING, RESOLVED = Status.FIRING, Status.RESOLVED
# T0 is a Wednesday, 08:00 UTC: inside the window, which closes at 08:30; released at 08:40.
WINDOW = {"name": "weekly updates", "days": ["wed"], "start": "07:30", "duration_minutes": 60}


def rules(**window: Any) -> Rules:
    return parse_rules(
        {
            "models": {"triage": {"backend": "openai", "model": "auto"}},
            "triage": {"enabled": True},
            "maintenance": [{**WINDOW, **window}],
        }
    )


class Bench:
    def __init__(self, store: Store, config: Rules, answer_now: Answer | None = None) -> None:
        self.store = store
        self.model = Scripted(answer("real", 0.9), answer("real", 0.9))
        self.recorder = Recorder(store, Correlator(store, config))
        self.triager = Triager(store, config, self.model)
        self.asked: list[str] = []

        def checker(source: str) -> Checker | None:
            if answer_now is None:
                return None

            def check(fingerprint: str, title: str) -> tuple[Answer, str]:
                self.asked.append(title)
                return answer_now, f"the source says {answer_now}"

            return check

        clock = lambda: T0 + timedelta(minutes=40)  # noqa: E731
        self.holds = Holds(store, config, Incidents(store, self.recorder, checker, clock))


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def released(store: Store) -> dict[str, Any]:
    [event] = list(store.events(kind="maintenance.released"))
    return event.payload


def test_a_blip_during_the_window_is_held_then_closed_as_expected(store: Store) -> None:
    bench = Bench(store, rules())
    bench.recorder.observe(alert(FIRING, 0))
    bench.recorder.observe(alert(RESOLVED, 1))
    [incident] = store.incidents()
    assert incident.maintenance == "weekly updates"
    assert held(store, rules(), incident)

    assert bench.triager.run_pending(at(30)) == 0  # held: no model call
    assert bench.holds.run_pending(at(39)) == 0  # the grace is not over
    assert bench.holds.run_pending(at(40)) == 1
    assert released(store) == {
        "window": "weekly updates",
        "outcome": "expected",
        "rechecked": [],
        "still_firing": [],
    }
    [incident] = store.incidents()
    assert incident.triage is TriageState.SKIPPED
    skipped = [e.payload for e in store.events(kind="triage.skipped")]
    assert skipped == [{"skip": "maintenance", "effective_verdict": "noise"}]
    assert bench.triager.run_pending(at(41)) == 0
    assert not bench.model.calls
    assert bench.holds.run_pending(at(50)) == 0  # released once


def test_an_alert_still_firing_after_the_window_goes_on_as_usual(store: Store) -> None:
    bench = Bench(store, rules(), answer_now="firing")
    bench.recorder.observe(alert(FIRING, 0, title="authentik-db"))
    assert bench.holds.run_pending(at(40)) == 1
    payload = released(store)
    assert payload["outcome"] == "still_firing"
    assert payload["still_firing"] == ["authentik-db"]
    assert payload["rechecked"][0]["answer"] == "firing"
    assert bench.asked == ["authentik-db"]

    assert bench.triager.run_pending(at(40)) == 1
    [(_, user)] = bench.model.calls
    after = json.loads(user)["incident"]["after_maintenance"]
    assert (after["window"], after["outcome"], after["still_firing"]) == (
        "weekly updates",
        "still_firing",
        ["authentik-db"],
    )


def test_a_recovery_the_source_never_sent_is_found_by_the_recheck(store: Store) -> None:
    # uptime-kuma does not notify PENDING -> UP: the incident would stay open for good.
    bench = Bench(store, rules(), answer_now="resolved")
    bench.recorder.observe(alert(FIRING, 0, title="authentik-worker"))
    assert bench.holds.run_pending(at(40)) == 1
    assert released(store)["outcome"] == "expected"
    [incident] = store.incidents()
    assert incident.status is IncidentStatus.RESOLVED
    assert incident.triage is TriageState.SKIPPED
    assert bench.triager.run_pending(at(41)) == 0


def test_a_window_without_hold_only_flags_the_incident(store: Store) -> None:
    config = rules(hold=False)
    bench = Bench(store, config)
    bench.recorder.observe(alert(FIRING, 0))
    [incident] = store.incidents()
    assert incident.maintenance == "weekly updates"
    assert not held(store, config, incident)
    assert bench.triager.run_pending(at(5)) == 1
    assert bench.holds.run_pending(at(40)) == 0


def test_a_window_removed_from_the_rules_holds_nothing_any_more(store: Store) -> None:
    Bench(store, rules()).recorder.observe(alert(FIRING, 0))
    [incident] = store.incidents()
    without = parse_rules({"models": {"triage": {"backend": "openai", "model": "auto"}}})
    assert not held(store, without, incident)


def test_the_release_waits_for_the_end_of_the_window_that_opened_it() -> None:
    [window] = rules(start="23:30", days=["tue"], grace_minutes=5).maintenance
    tuesday_night = datetime(2026, 11, 18, 0, 10, tzinfo=UTC)  # Wednesday 00:10, past midnight
    assert window.closes_at(tuesday_night) == datetime(2026, 11, 18, 0, 30, tzinfo=UTC)
    assert window.closes_at(datetime(2026, 11, 18, 0, 30, tzinfo=UTC)) is None
    assert (window.hold, window.grace_minutes) == (True, 5)


def test_an_alert_time_far_ahead_holds_no_longer_than_one_window(store: Store) -> None:
    bench = Bench(store, rules())
    ahead = alert(FIRING, 7 * 24 * 60)  # next Wednesday 08:00, in the window again
    bench.recorder.observe(ahead)
    [incident] = store.incidents()
    assert incident.maintenance == "weekly updates"
    seen = next(iter(store.events(kind="incident.opened"))).recorded_at
    assert bench.holds.release_at(incident) == seen + timedelta(minutes=70)
