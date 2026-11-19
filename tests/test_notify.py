"""Channels: named once, listed by each stage, and a failure of one is that one's alone."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from acting import Bench, proposal
from paeon import notify
from paeon.config import load, load_executor
from paeon.notify import Delivery, Message, NotifyError
from paeon.notify.ntfy import NtfyNotifier
from paeon.rules import RulesError, parse_rules
from support import T0, Recording

PHONE = {"type": "ntfy", "url": "https://ntfy.example.org", "topic": "act", "token_env": "T_PHONE"}
MESSAGE = Message(title="t", text="m")


def test_a_stage_sends_to_its_ntfy_block_and_to_the_channels_it_lists() -> None:
    rules = parse_rules(
        {
            "notifiers": {"phone": PHONE, "spare": {**PHONE, "topic": "spare"}},
            "digest": {"ntfy": {"url": "https://ntfy.example.org", "topic": "old"}},
            "investigation": {"notify": ["phone", "spare"]},
        }
    )
    assert [name for name, _ in rules.channels("digest")] == ["ntfy"]
    assert [name for name, _ in rules.channels("investigation")] == ["phone", "spare"]
    assert rules.channels("actions") == []

    built = notify.for_stage(rules, "investigation", secrets={"phone": {"token": "tk"}})
    assert [(n.name, n.kind, n.where) for n in built] == [
        ("phone", "ntfy", "act"),
        ("spare", "ntfy", "spare"),
    ]
    assert isinstance(built[0], NtfyNotifier)
    assert (built[0]._token, built[1]._token) == ("tk", None)  # type: ignore[attr-defined]
    (old,) = notify.for_stage(rules, "digest", secrets={}, legacy_token="legacy")
    assert (old.name, old.where, old._token) == ("ntfy", "old", "legacy")  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "document",
    [
        {"digest": {"notify": ["phone"]}},  # not defined
        {"notifiers": {"phone": PHONE}, "actions": {"notify": ["phone", "phone"]}},  # twice
        {"notifiers": {"Phone": PHONE}},  # not a name
        {"notifiers": {"ntfy": PHONE}},  # the name of a stage's own block
        {"notifiers": {"phone": {**PHONE, "type": "pigeon"}}},  # not a service
        {"notifiers": {"phone": {**PHONE, "password": "x"}}},  # not a key
    ],
)
def test_channels_that_cannot_be_are_refused(document: dict[str, Any]) -> None:
    with pytest.raises(RulesError):
        parse_rules(document)


def test_one_channel_failing_does_not_keep_the_others_from_sending() -> None:
    sent: list[dict[str, Any]] = []
    broken, fine = Recording(sent, name="broken"), Recording(sent, name="fine", where="chat")
    broken.fail = "ntfy answered HTTP 503"

    class Buggy(Recording):
        def send(self, message: Message) -> None:
            raise KeyError("https://secret.example.org")

    done = notify.deliver([broken, Buggy(sent, name="buggy"), fine], MESSAGE)
    assert [d.ok for d in done] == [False, False, True]
    assert len(sent) == 1
    assert done[0].record("approval") == (
        "notification.failed",
        {
            "channel": "ntfy",
            "name": "broken",
            "to": "incidents",
            "about": "approval",
            "error": "ntfy answered HTTP 503",
        },
    )
    assert done[1].error == "ntfy failed: KeyError"  # a bug's message may hold anything
    assert done[2].record("approval") == (
        "notification.sent",
        {"channel": "ntfy", "name": "fine", "to": "chat", "about": "approval"},
    )
    assert Delivery("a", "ntfy", "t").ok
    assert issubclass(NotifyError, RuntimeError)


def test_each_process_reads_only_the_secrets_of_its_own_channels(tmp_path: Path) -> None:
    path = tmp_path / "paeon.yaml"
    path.write_text(
        "notifiers:\n"
        "  phone: {type: ntfy, url: 'https://n.example.org', topic: act, token_env: T_PHONE}\n"
        "  team: {type: ntfy, url: 'https://n.example.org', topic: team, token_env: T_TEAM}\n"
        "  open: {type: ntfy, url: 'https://n.example.org', topic: open}\n"
        "digest: {notify: [team, open]}\n"
        "actions: {notify: [phone]}\n"
    )
    env = {
        "PAEON_INGEST_TOKEN": "t" * 32,
        "PAEON_EXECUTOR_TOKEN": "e" * 32,
        "PAEON_CONFIG": str(path),
        "T_PHONE": "phone-token",
        "T_TEAM": "team-token",
    }
    assert load(env).notifier_secrets == {"team": {"token": "team-token"}, "open": {}}
    assert load_executor(env).notifier_secrets == {"phone": {"token": "phone-token"}}
    # ntfy's token is optional (a topic may be open): without it the channel is still built.
    assert load({**env, "T_TEAM": ""}).notifier_secrets == {"team": {}, "open": {}}


def test_an_approval_goes_to_every_channel_and_the_first_tap_wins(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    second: list[dict[str, Any]] = []
    bench.executor._notifiers += (Recording(second, name="chat", where="family"),)
    bench.executor.propose(proposal(), T0)
    # The same single-use links on both: whoever taps first decides.
    assert bench.sent[0]["actions"] == second[0]["actions"]
    sent = [e.payload for e in bench.store.events(kind="notification.sent")]
    assert [(p["name"], p["to"], p["about"]) for p in sent] == [
        ("ntfy", "incidents", "approval"),
        ("chat", "family", "approval"),
    ]
    go = bench.link("Go")
    assert bench.executor.act(go, T0).ok
    again = bench.executor.act(go, T0)
    assert (again.ok, again.message) == (False, "this link was already used")

    # With one channel down the approval is still asked on the other, and both are journaled.
    other = Bench(tmp_path / "other")
    other.fail("ntfy answered HTTP 503")
    kept: list[dict[str, Any]] = []
    other.executor._notifiers += (Recording(kept, name="chat"),)
    other.executor.propose(proposal(), T0)
    assert [b["label"] for b in kept[0]["actions"]] == ["Go", "No-go"]
    assert other.last("notification.failed")["name"] == "ntfy"
    assert other.last("notification.sent")["name"] == "chat"
