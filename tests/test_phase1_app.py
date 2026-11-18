"""Phase 1 over HTTP and the CLI: change calendar, incidents, feedback, digest, shadow triage."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from paeon import digest as digests
from paeon.app import create_app
from paeon.cli import main
from paeon.config import Settings, load
from paeon.correlation import Correlator
from paeon.models import ModelReply
from paeon.recorder import Recorder
from paeon.rules import parse_rules
from paeon.schema import Status
from paeon.store import Store
from support import alert

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Noise:
    def complete(self, *, system: str, user: str) -> ModelReply:
        text = json.dumps({"verdict": "noise", "confidence": 0.95, "reason": "it always recovers"})
        return ModelReply(text=text, model="m-1", input_tokens=10, output_tokens=5)


def settings(**rules: Any) -> Settings:
    base = load({"PAEON_INGEST_TOKEN": TOKEN})
    return Settings(
        db_path=base.db_path,
        host=base.host,
        port=base.port,
        ingest_token=TOKEN,
        openobserve=None,
        heartbeat_url=None,
        heartbeat_seconds=60,
        rules=parse_rules(rules),
        ntfy_token="not-a-real-token",
    )


@pytest.fixture
def client(store: Store) -> Iterator[TestClient]:
    with TestClient(create_app(settings(), store)) as test_client:
        yield test_client


def kuma(monitor: int, status: int, time: str, host: str = "server-1") -> dict[str, Any]:
    return {
        "heartbeat": {"monitorID": monitor, "status": status, "time": time, "msg": "timeout"},
        "monitor": {"id": monitor, "name": f"monitor {monitor}", "type": "port", "hostname": host},
    }


def test_new_endpoints_require_the_token(client: TestClient) -> None:
    assert client.post("/ingest/change", json={"kind": "deploy", "title": "x"}).status_code == 401
    assert client.get("/incidents").status_code == 401
    assert client.get("/incidents/1").status_code == 401
    assert client.post("/incidents/1/feedback", json={"vote": "up"}).status_code == 401
    assert client.get("/digest").status_code == 401


def test_webhook_alerts_become_one_incident_with_its_timeline(client: TestClient) -> None:
    for monitor, time in ((1, "2026-11-18 08:00:00"), (2, "2026-11-18 08:01:10")):
        client.post("/ingest/uptime-kuma", json=kuma(monitor, 0, time), headers=AUTH)
    [incident] = client.get("/incidents", headers=AUTH).json()
    assert incident["host"] == "server-1"
    assert [a["title"] for a in incident["alerts"]] == ["monitor 1", "monitor 2"]
    assert client.get("/incidents?status=resolved", headers=AUTH).json() == []

    detail = client.get("/incidents/1", headers=AUTH).json()
    assert [e["kind"] for e in detail["events"]] == [
        "incident.opened",
        "alert.firing",
        "incident.correlated",
        "alert.firing",
    ]
    assert client.get("/incidents/99", headers=AUTH).status_code == 404


def test_change_calendar(client: TestClient, store: Store) -> None:
    body = {"kind": "update", "title": "weekly updater", "host": "server-1"}
    assert client.post("/ingest/change", json=body, headers=AUTH).status_code == 200
    explicit = {**body, "started_at": "2026-11-18T04:00:00+01:00"}
    client.post("/ingest/change", json=explicit, headers=AUTH)
    first, second = (e.payload for e in store.events(kind="change.recorded"))
    assert first["title"] == "weekly updater"
    assert datetime.fromisoformat(first["started_at"]).tzinfo is not None  # defaulted to now
    assert second["started_at"] == "2026-11-18T04:00:00+01:00"
    bad = client.post("/ingest/change", json={"kind": "update"}, headers=AUTH)
    assert bad.status_code == 422


def test_feedback_and_digest(client: TestClient, store: Store) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    client.post("/ingest/uptime-kuma", json=kuma(1, 0, now), headers=AUTH)
    vote = {"vote": "down", "note": "it was a real outage"}
    assert client.post("/incidents/1/feedback", json=vote, headers=AUTH).status_code == 200
    assert client.post("/incidents/9/feedback", json=vote, headers=AUTH).status_code == 404
    assert (
        client.post("/incidents/1/feedback", json={"vote": "meh"}, headers=AUTH).status_code == 422
    )
    [feedback] = list(store.events(kind="feedback.recorded"))
    assert (feedback.incident_id, feedback.payload["vote"]) == (1, "down")

    digest = client.get("/digest?hours=1", headers=AUTH).json()
    assert digest["incidents"][0]["feedback"] == "down"
    assert "#1 monitor 1" in digest["text"]


def test_triage_runs_in_shadow_mode_from_the_app(store: Store) -> None:
    config = settings(
        models={"triage": {"backend": "openai", "model": "auto"}},
        triage={"enabled": True, "settle_seconds": 0},
    )
    app = create_app(config, store, triage_client=Noise())
    with TestClient(app) as client:
        client.post("/ingest/uptime-kuma", json=kuma(1, 0, "2026-11-18 08:00:00"), headers=AUTH)
        assert app.state.triager.run_pending(datetime.now(UTC)) == 1
        [line] = client.get("/digest?hours=744", headers=AUTH).json()["incidents"]
    assert (line["verdict"], line["effective_verdict"]) == ("noise", "noise")
    # Shadow mode: the alert was recorded as it came, and nothing was sent anywhere.
    assert [e.kind for e in store.events() if e.kind.startswith("digest.")] == []


def test_without_triage_no_model_client_is_built(store: Store) -> None:
    assert create_app(settings(), store).state.triager is None


def test_a_real_client_is_built_from_the_alias(store: Store) -> None:
    config = settings(
        models={"triage": {"backend": "anthropic", "model": "claude-haiku-5-5"}},
        triage={"enabled": True},
    )
    app = create_app(config, store)
    with TestClient(app):  # starts and stops the loops, closes the client
        assert app.state.triager is not None


def test_digest_is_sent_once_a_day_after_the_hour(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str | None, str]] = []

    def fake_publish(ntfy: Any, token: str | None, *, title: str, message: str) -> None:
        sent.append((token, message))

    monkeypatch.setattr(digests, "publish_ntfy", fake_publish)
    config = settings(
        digest={"hour": 8, "ntfy": {"url": "https://ntfy.example.org", "topic": "incidents"}}
    )
    send = create_app(config, store).state.send_digest
    rec = Recorder(store, Correlator(store, config.rules))
    day = datetime(2026, 11, 18, tzinfo=UTC)
    assert not send(day + timedelta(hours=7, minutes=59))
    rec.observe(alert(Status.FIRING, 0, title="monitor 1"))  # 08:00 on the 18th
    assert send(day + timedelta(hours=8, minutes=2))
    assert not send(day + timedelta(hours=9))  # already sent today
    rec.observe(alert(Status.FIRING, 23 * 60, "b"))
    assert send(day + timedelta(days=1, hours=8, minutes=30))
    assert not send(day + timedelta(days=3, hours=9))  # a quiet day sends nothing
    assert [token for token, _ in sent] == ["not-a-real-token"] * 2
    assert "#1 monitor 1" in sent[0][1]
    assert [e.payload["date"] for e in store.events(kind="digest.sent")] == [
        "2026-11-18",
        "2026-11-19",
    ]


def test_a_failed_digest_is_retried(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("ntfy answered HTTP 503")

    monkeypatch.setattr(digests, "publish_ntfy", failing)
    config = settings(digest={"ntfy": {"url": "https://ntfy.example.org", "topic": "incidents"}})
    Recorder(store, Correlator(store, config.rules)).observe(alert(Status.FIRING, 0))
    with pytest.raises(RuntimeError):
        create_app(config, store).state.send_digest(datetime(2026, 11, 18, 9, tzinfo=UTC))
    assert store.cursor("digest") is None


def test_cli_incidents_digest_and_feedback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "cli.sqlite"
    store = Store(db)
    with TestClient(create_app(settings(), store)) as client:
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        client.post("/ingest/uptime-kuma", json=kuma(1, 0, now), headers=AUTH)
    store.close()

    assert main(["incidents", "--db", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "monitor 1"
    assert main(["feedback", "1", "up", "--note", "right", "--db", str(db)]) == 0
    assert main(["feedback", "7", "up", "--db", str(db)]) == 1
    assert "no incident 7" in capsys.readouterr().err
    assert main(["digest", "--db", str(db), "--hours", "1"]) == 0
    assert "#1 monitor 1 [open] → not triaged 👍" in capsys.readouterr().out
