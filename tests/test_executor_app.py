"""The executor's service: a token for the core, a link for the phone, nothing for anyone else."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acting import ACTIONS, Bench, proposal
from paeon.config import ConfigError, ExecutorSettings, load_executor
from paeon.executor import ExecutorStore
from paeon.executor_app import build_executor, create_executor_app

TOKEN = "e" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def app(bench: Bench) -> TestClient:
    settings = ExecutorSettings(
        db_path=Path("unused"),
        host="127.0.0.1",
        port=8081,
        token=TOKEN,
        rules=bench.rules,
        signing_key=None,
        ntfy_token=None,
        kill_switch=False,
    )
    return TestClient(create_executor_app(settings, bench.executor))


def test_the_core_needs_its_token(tmp_path: Path) -> None:
    with app(Bench(tmp_path)) as client:
        body = proposal().model_dump(mode="json")
        assert client.get("/healthz").json() == {"status": "ok"}
        for method, path in (
            ("POST", "/proposals"),
            ("GET", "/journal"),
            ("GET", "/state"),
            ("POST", "/kill-switch"),
            ("POST", "/links/vote"),
            ("POST", "/proposals/1/cancel"),
        ):
            assert client.request(method, path, json=body).status_code == 401
            wrong = {"Authorization": "Bearer nope"}
            assert client.request(method, path, json=body, headers=wrong).status_code == 401
        assert client.get("/openapi.json").status_code == 404


def test_propose_approve_by_link_and_mirror(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    with app(bench) as client:
        answer = client.post("/proposals", json=proposal().model_dump(mode="json"), headers=AUTH)
        assert answer.json() == {"status": "pending", "proposal": 1, "level": "approve"}
        assert client.get("/state", headers=AUTH).json()["pending"][0]["incident"] == 1
        # A plan with an unknown field, or a target that is not a name, never gets in.
        bad = {**proposal().model_dump(mode="json"), "steps": [{"action": "x", "target": "a b"}]}
        assert client.post("/proposals", json=bad, headers=AUTH).status_code == 422

        go = bench.link("Go")
        page = client.get(f"/act/{go}")  # a preview fetch, a crawler, a mis-tap
        assert page.status_code == 200
        assert "Run the plan of incident #1: restart_container(worker)." in page.text
        assert '<form method="post">' in page.text
        assert "default-src 'none'" in page.headers["content-security-policy"]
        assert bench.docker.posts == []

        done = client.post(f"/act/{go}")  # the button: no token, the link is the proof
        assert done.status_code == 200
        assert "Approved." in done.text
        assert bench.docker.posts == ["restart worker"]
        assert client.post(f"/act/{go}").status_code == 410
        assert client.get("/act/forged.link").status_code == 410
        assert "This link is not valid." in client.post("/act/forged.link").text

        journal = client.get("/journal", headers=AUTH).json()["events"]
        assert "plan.completed" in [entry["kind"] for entry in journal]
        after = journal[-1]["id"]
        assert client.get("/journal", params={"after": after}, headers=AUTH).json() == {
            "events": [],
            "head": after,
        }


def test_cancel_votes_and_the_kill_switch(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    with app(bench) as client:
        client.post("/proposals", json=proposal().model_dump(mode="json"), headers=AUTH)
        cancelled = client.post("/proposals/1/cancel", json={"reason": "resolved"}, headers=AUTH)
        assert cancelled.json() == {"cancelled": True}
        links = client.post(
            "/links/vote", json={"incident_id": 1, "about": "root_cause"}, headers=AUTH
        ).json()
        assert sorted(links) == ["down", "up"]
        refused = client.post("/links/vote", json={"incident_id": 1, "about": "x"}, headers=AUTH)
        assert refused.status_code == 422
        assert client.post("/kill-switch", headers=AUTH).json() == {"kill_switch": True}
        assert bench.executor.killed()
        # The core can stop automation. Nothing it can call resumes it.
        assert client.post("/resume", headers=AUTH).status_code == 404


def env(tmp_path: Path, **more: str) -> dict[str, str]:
    rules = tmp_path / "paeon.yaml"
    rules.write_text(
        "actions:\n  enabled: true\n  docker_url: http://dockerproxy-act:2375\n"
        "  docker_read_url: http://dockerproxy:2375\n"
        "  ntfy: {url: 'https://ntfy.example.org', topic: incidents, token_env: NTFY}\n"
    )
    return {
        "PAEON_EXECUTOR_TOKEN": TOKEN,
        "PAEON_CONFIG": str(rules),
        "PAEON_EXECUTOR_DB": str(tmp_path / "executor.sqlite"),
        **more,
    }


def test_executor_settings(tmp_path: Path) -> None:
    key_file = tmp_path / "key"
    key_file.write_text("k" * 40 + "\n")
    settings = load_executor(
        env(tmp_path, PAEON_SIGNING_KEY_FILE=str(key_file), NTFY="tk", PAEON_KILL_SWITCH="on")
    )
    assert settings.signing_key == "k" * 40
    assert (settings.port, settings.host) == (8081, "127.0.0.1")
    assert settings.ntfy_token == "tk"
    assert settings.kill_switch
    store = ExecutorStore(settings.db_path)
    try:
        built = build_executor(settings, store)
        assert built.killed()
        assert built.state(__import__("support").T0)["can_act"] is True
    finally:
        built._docker.close()  # type: ignore[union-attr]
        store.close()
    assert load_executor(env(tmp_path)).signing_key is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"PAEON_EXECUTOR_TOKEN": "short"}, "PAEON_EXECUTOR_TOKEN"),
        ({"PAEON_CONFIG": ""}, "PAEON_CONFIG is required"),
        ({"PAEON_CONFIG": "/nonexistent/paeon.yaml"}, "cannot be read"),
        ({"PAEON_SIGNING_KEY": "short"}, "32 characters"),
        ({"PAEON_SIGNING_KEY": TOKEN}, "must differ"),
    ],
)
def test_executor_settings_fail_closed(
    tmp_path: Path, change: dict[str, str], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        load_executor({**env(tmp_path), **change})


def test_the_example_catalog_is_the_one_in_the_tests() -> None:
    assert sorted(ACTIONS["catalog"]) == [
        "restart_container",
        "start_container",
        "unpause_container",
    ]


def test_the_access_log_keeps_no_link_and_no_poll() -> None:
    import logging

    from paeon.executor_app import _QuietAccess

    def line(method: str, path: str, status: int) -> logging.LogRecord:
        return logging.LogRecord(
            "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
            ("10.0.0.9:1", method, path, "1.1", status), None,
        )  # fmt: skip

    keep = _QuietAccess()
    tap = line("POST", "/act/eyJleHAiOjE3OTUwMTIyODQsImsiOiJnbyJ9.c2lnbmF0dXJl", 200)
    assert keep.filter(tap)
    assert tap.getMessage() == '10.0.0.9:1 - "POST /act/[link] HTTP/1.1" 200'
    assert not keep.filter(line("GET", "/journal?after=12", 200))
    assert not keep.filter(line("GET", "/healthz", 200))
    assert keep.filter(line("GET", "/journal?after=12", 401))  # a failure is worth a line
    assert keep.filter(line("POST", "/proposals", 200))
    odd = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "started", None, None)
    assert keep.filter(odd)
    assert (
        sum(isinstance(f, _QuietAccess) for f in logging.getLogger("uvicorn.access").filters) <= 1
    )
