from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx2 as httpx
import pytest

from paeon import digest as digests
from paeon.correlation import Correlator
from paeon.notify import Button, Message, NotifyError
from paeon.notify.ntfy import NtfyNotifier
from paeon.recorder import Recorder
from paeon.rules import NtfyChannel, Rules
from paeon.schema import Status
from paeon.store import Store
from support import T0, alert

DAY = {"since": T0 - timedelta(hours=1), "until": T0 + timedelta(hours=23)}


def filled(store: Store) -> None:
    rec = Recorder(store, Correlator(store, Rules()))
    for name in ("noisy", "broken", "paged", "untriaged"):
        rec.observe(alert(Status.FIRING, 0, name, title=name))
    rec.observe(alert(Status.RESOLVED, 3, "noisy", title="noisy"))
    store.append(
        "triage.verdict",
        {
            "verdict": "noise",
            "effective_verdict": "noise",
            "confidence": 0.93,
            "reason": "Fired 14 times in 30 days, always recovered.",
            "fail_open": None,
        },
        incident_id=1,
    )
    store.append(
        "triage.verdict",
        {"verdict": None, "effective_verdict": "real", "fail_open": "model_error"},
        incident_id=2,
    )
    store.append(
        "triage.skipped", {"skip": "page_level", "effective_verdict": "real"}, incident_id=3
    )
    store.append("feedback.recorded", {"vote": "down"}, incident_id=1)
    store.append("feedback.recorded", {"vote": "up"}, incident_id=1)  # the last answer counts
    store.append("model.call", {"cost": 0.0015})
    store.append("model.call", {"cost": 0.0005, "error": "HTTP 529"})


def test_digest_lists_incidents_with_verdict_and_feedback(store: Store) -> None:
    filled(store)
    digest = digests.build(store, **DAY)
    assert [
        (line.incident.title, line.effective_verdict, line.feedback) for line in digest.lines
    ] == [
        ("noisy", "noise", "up"),
        ("broken", "real", None),
        ("paged", "real", None),
        ("untriaged", None, None),
    ]
    assert (digest.model_calls, digest.cost) == (2, 0.002)
    assert [line.demoted for line in digest.lines] == [True, False, False, False]
    assert digest.as_dict()["incidents"][0]["incident"]["id"] == 1

    text = digests.render(digest)
    assert "4 incidents, 1 that triage would have demoted" in text
    assert "#1 noisy [resolved] → noise 0.93 👍" in text
    assert "    Fired 14 times in 30 days, always recovered." in text
    assert "#2 broken [open] → real (fail open: model_error)" in text
    assert "#3 paged [open] → real (fail open: page_level)" in text
    assert "#4 untriaged [open] → not triaged" in text
    assert text.index("Would have been demoted:") < text.index("Kept:")


def test_digest_outside_the_period_is_empty(store: Store) -> None:
    filled(store)
    later = digests.build(store, since=T0 + timedelta(days=2), until=T0 + timedelta(days=3))
    assert digests.render(later) == "No incident in the period."


def ntfy(handler: Any, token: str | None = None) -> NtfyNotifier:
    config = NtfyChannel(type="ntfy", url="https://ntfy.example.org", topic="incidents")
    return NtfyNotifier("phone", config, token, transport=httpx.MockTransport(handler))


def test_publish_to_ntfy() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "x"})

    buttons = (Button("Go", "https://x/act/1", one_tap=True), Button("No-go", "https://x/act/2"))
    ntfy(handler, "not-a-real-token").send(Message(title="t", text="m", buttons=buttons))
    [request] = seen
    assert request.headers["authorization"] == "Bearer not-a-real-token"
    body: dict[str, Any] = json.loads(request.content)
    assert (body["topic"], body["title"], body["message"]) == ("incidents", "t", "m")
    assert [(a["action"], a["label"], a.get("method")) for a in body["actions"]] == [
        ("http", "Go", "POST"),  # one tap: the phone sends the link itself
        ("view", "No-go", None),  # the phone opens the page
    ]


def test_ntfy_failures_do_not_carry_the_token_or_the_url() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    message = Message(title="t", text="m")
    with pytest.raises(NotifyError, match="HTTP 403"):
        ntfy(refused).send(message)
    with pytest.raises(NotifyError, match="ConnectError") as caught:
        ntfy(down, "tok").send(message)
    assert "ntfy.example.org" not in str(caught.value)
