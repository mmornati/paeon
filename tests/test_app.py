from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from paeon.app import create_app
from paeon.cli import main
from paeon.config import ConfigError, load
from paeon.recorder import Recorder
from paeon.sources.openobserve import OpenObserveSource
from paeon.store import Store

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "paeon.sqlite")
    yield s
    s.close()


@pytest.fixture
def client(store: Store) -> Iterator[TestClient]:
    settings = load({"PAEON_INGEST_TOKEN": TOKEN})
    with TestClient(create_app(settings, store)) as test_client:
        yield test_client


def kuma(status: int) -> dict[str, Any]:
    return {
        "heartbeat": {"monitorID": 3, "status": status, "time": "2026-11-18 07:58:30", "msg": "x"},
        "monitor": {"id": 3, "name": "Traefik", "type": "http"},
        "msg": "m",
    }


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN}]
)
def test_ingest_requires_the_token(
    client: TestClient, store: Store, headers: dict[str, str]
) -> None:
    for path in ("/ingest/uptime-kuma", "/ingest/alertmanager"):
        assert client.post(path, json=kuma(0), headers=headers).status_code == 401
    assert list(store.events()) == []


def test_uptime_kuma_down_repeat_up(client: TestClient, store: Store) -> None:
    down = kuma(0)
    assert client.post("/ingest/uptime-kuma", json=down, headers=AUTH).json() == {
        "alerts": 1,
        "changes": 1,
    }
    repeat = kuma(0)
    repeat["heartbeat"]["time"] = "2026-11-18 08:03:30"
    assert client.post("/ingest/uptime-kuma", json=repeat, headers=AUTH).json()["changes"] == 0
    up = kuma(1)
    up["heartbeat"]["time"] = "2026-11-18 08:05:30"
    assert client.post("/ingest/uptime-kuma", json=up, headers=AUTH).json()["changes"] == 1

    kinds = [e.kind for e in store.events()]
    assert kinds.count("webhook.received") == 3
    assert [k for k in kinds if k.startswith("alert.")] == ["alert.firing", "alert.resolved"]


def test_uptime_kuma_test_message_is_kept_but_is_no_alert(client: TestClient, store: Store) -> None:
    body = {"heartbeat": None, "monitor": None, "msg": "Testing"}
    assert client.post("/ingest/uptime-kuma", json=body, headers=AUTH).json() == {
        "alerts": 0,
        "changes": 0,
    }
    assert [e.kind for e in store.events()] == ["webhook.received"]


def test_alertmanager_endpoint(client: TestClient, store: Store) -> None:
    body = {
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "A"},
                "startsAt": "2026-11-18T07:50:00Z",
                "fingerprint": "f1",
            }
        ]
    }
    assert client.post("/ingest/alertmanager", json=body, headers=AUTH).json() == {
        "alerts": 1,
        "changes": 1,
    }
    assert [e.kind for e in store.events(kind="alert.firing")] == ["alert.firing"]


def test_body_must_be_an_object(client: TestClient) -> None:
    assert client.post("/ingest/uptime-kuma", json=[1], headers=AUTH).status_code == 422


def test_config_requires_a_real_token() -> None:
    with pytest.raises(ConfigError, match="PAEON_INGEST_TOKEN"):
        load({})
    with pytest.raises(ConfigError, match="PAEON_INGEST_TOKEN"):
        load({"PAEON_INGEST_TOKEN": "short"})


def test_config_openobserve_and_secret_files(tmp_path: Path) -> None:
    secret = tmp_path / "pw"
    secret.write_text("hunter2-not-real\n")
    base = {"PAEON_INGEST_TOKEN": TOKEN, "PAEON_OPENOBSERVE_URL": "http://openobserve:5080"}
    with pytest.raises(ConfigError, match="PAEON_OPENOBSERVE_USER"):
        load(base)
    settings = load(
        {
            **base,
            "PAEON_OPENOBSERVE_USER": "reader@example.org",
            "PAEON_OPENOBSERVE_PASSWORD_FILE": str(secret),
            "PAEON_OPENOBSERVE_POLL_SECONDS": "30",
        }
    )
    assert settings.openobserve is not None
    assert settings.openobserve.password == "hunter2-not-real"
    assert settings.openobserve.org == "default"
    assert settings.openobserve.poll_seconds == 30
    with pytest.raises(ConfigError, match="cannot be read"):
        load({**base, "PAEON_OPENOBSERVE_USER": "u", "PAEON_OPENOBSERVE_PASSWORD_FILE": "/nope"})


@pytest.mark.parametrize("value", ["soon", "1"])
def test_config_rejects_bad_numbers(value: str) -> None:
    with pytest.raises(ConfigError, match="PAEON_HEARTBEAT_SECONDS"):
        load({"PAEON_INGEST_TOKEN": TOKEN, "PAEON_HEARTBEAT_SECONDS": value})


def test_cli_events_export(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "paeon.sqlite"
    store = Store(db)
    store.append("alert.firing", {"title": "x"}, source="s", fingerprint="f")
    store.append("webhook.received", {})
    store.close()

    assert main(["events", "--db", str(db), "--kind", "alert.firing"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert (event["kind"], event["source"], event["payload"]) == (
        "alert.firing",
        "s",
        {"title": "x"},
    )


def test_cli_serve_without_config_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PAEON_INGEST_TOKEN", raising=False)
    monkeypatch.delenv("PAEON_INGEST_TOKEN_FILE", raising=False)
    assert main(["serve"]) == 2
    assert "PAEON_INGEST_TOKEN" in capsys.readouterr().err


OO_ENV = {
    "PAEON_INGEST_TOKEN": TOKEN,
    "PAEON_OPENOBSERVE_URL": "http://openobserve:5080",
    "PAEON_OPENOBSERVE_USER": "reader",
    "PAEON_OPENOBSERVE_PASSWORD": "pw",
}


class OneFiring:
    def search(self, sql: str, start: int, end: int, size: int) -> list[dict[str, Any]]:
        return [{"_timestamp": end - 1_000_000, "key": "disk_full/abc", "status": "firing"}]


class Down:
    def search(self, sql: str, start: int, end: int, size: int) -> list[dict[str, Any]]:
        raise ConnectionError("openobserve is down")


def wait_for_events(store: Store, count: int) -> list[str]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        found = [f"{e.kind} {e.fingerprint}" for e in store.events() if e.kind.startswith("alert.")]
        if len(found) >= count:
            return found
        time.sleep(0.02)
    raise AssertionError("the poll loop recorded nothing")


def test_poll_loop_records_firings(store: Store) -> None:
    source = OpenObserveSource(OneFiring(), store, Recorder(store))
    with TestClient(create_app(load(OO_ENV), store, openobserve=source)):
        assert wait_for_events(store, 1) == ["alert.firing abc"]


def test_unreachable_source_is_recorded_as_stale(store: Store) -> None:
    source = OpenObserveSource(Down(), store, Recorder(store))
    with TestClient(create_app(load(OO_ENV), store, openobserve=source)):
        assert wait_for_events(store, 1) == ["alert.firing source_stale:openobserve"]
