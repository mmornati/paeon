"""A fake Docker and an executor around it, shared by the tests of stages 3 and 4."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx2 as httpx

from paeon.catalog import Proposal, Step
from paeon.executor import DockerActuator, Executor, ExecutorStore
from paeon.links import Signer
from paeon.rules import Rules, parse_rules

KEY = "s" * 40
OPEN: list[Bench] = []  # closed after each test by the fixture in conftest
ID = {"worker": "a" * 64, "vpn": "b" * 64, "db": "c" * 64, "traefik": "d" * 64}


class FakeDocker:
    """Containers by name. `broken` names never come back up after an action."""

    def __init__(self, **states: dict[str, Any]) -> None:
        self.states = {
            name: {"Running": True, "Paused": False, "Restarting": False, **state}
            for name, state in states.items()
        }
        self.broken: set[str] = set()
        self.posts: list[str] = []
        self.status = 204

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/containers/json":
            return httpx.Response(
                200, json=[{"Id": ID[name], "Names": [f"/{name}"]} for name in self.states]
            )
        _, _, container_id, verb = path.split("/")
        name = next(n for n, i in ID.items() if i == container_id)
        state = self.states[name]
        if request.method == "GET":
            return httpx.Response(200, json={"State": state, "RestartCount": 0})
        self.posts.append(f"{verb} {name}")
        if self.status >= 400:
            return httpx.Response(self.status)
        if verb in ("restart", "start", "unpause"):
            state["Running"] = name not in self.broken
            state["Paused"] = False
        elif verb == "stop":
            state["Running"] = False
        elif verb == "pause":
            state["Paused"] = True
        return httpx.Response(204)

    def actuator(self) -> DockerActuator:
        self.http = httpx.Client(
            base_url="http://dockerproxy", transport=httpx.MockTransport(self.handle)
        )
        return DockerActuator(self.http)

    def close(self) -> None:
        if hasattr(self, "http"):
            self.http.close()


ACTIONS: dict[str, Any] = {
    "enabled": True,
    "public_url": "https://act.example.org",
    "ntfy": {"url": "https://ntfy.example.org", "topic": "incidents"},
    "catalog": {
        "restart_container": {
            "targets": ["worker", "vpn", "media-*"],
            "deny": ["media-db"],
            "verify": {"wait_seconds": 0, "timeout_seconds": 10},
        },
        "start_container": {"targets": ["worker"], "verify": {"wait_seconds": 0}},
        "unpause_container": {"targets": ["worker"], "verify": {"wait_seconds": 0}},
    },
    "policies": [
        {"alert": "worker_stalled", "action": "restart_container", "level": "auto"},
        {"alert": "worker_.*", "level": "approve"},
        {"alert": "watched", "level": "observe"},
    ],
    "runbooks": [
        {
            "name": "worker-stalled",
            "match": {"title": "worker_stalled"},
            "steps": [
                {"action": "restart_container", "target": "worker"},
                {"action": "restart_container", "target": "vpn"},
            ],
        }
    ],
}


def rules(tmp_path: Path, **actions: Any) -> Rules:
    document = {
        "actions": {**ACTIONS, "lock_dir": str(tmp_path / "locks"), **actions},
        "ui": {"public_url": "https://paeon.example.org"},
    }
    return parse_rules(document)


def proposal(
    *steps: tuple[str, str],
    alert: str = "worker_down",
    incident: int = 1,
    confidence: float = 0.9,
    **more: Any,
) -> Proposal:
    return Proposal(
        incident_id=incident,
        title="Worker is down",
        origin=more.pop("origin", "investigation"),
        alerts=more.pop("alerts", (alert,)),
        confidence=confidence,
        summary="It stalled.",
        steps=tuple(
            Step(action=a, target=t) for a, t in steps or [("restart_container", "worker")]
        ),
        **more,
    )


class Bench:
    """An executor with everything around it faked, and what it sent to the phone."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        docker: FakeDocker | None = None,
        signer: bool = True,
        with_docker: bool = True,
        kill_env: bool = False,
        **actions: Any,
    ) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.store = ExecutorStore(tmp_path / "executor.sqlite")
        OPEN.append(self)
        self.docker = docker or FakeDocker(worker={}, vpn={}, db={})
        self.sent: list[dict[str, Any]] = []
        self.slept: list[float] = []
        self.healthy_url = True
        self.rules = rules(tmp_path, **actions)
        self.executor = Executor(
            self.store,
            self.rules,
            signer=Signer(KEY) if signer else None,
            docker=self.docker.actuator() if with_docker else None,
            ntfy_token="ntfy-token",
            kill_env=kill_env,
            publish=self.publish,
            sleep=self.slept.append,
            http_ok=lambda url: self.healthy_url,
            spawn=lambda job: job(),
        )

    def publish(self, ntfy: Any, token: str | None, **message: Any) -> None:
        self.sent.append({"topic": ntfy.topic, **message})

    def close(self) -> None:
        self.store.close()
        self.docker.close()

    def kinds(self) -> list[str]:
        return [event.kind for event in self.store.events()]

    def last(self, kind: str) -> dict[str, Any]:
        return next(self.store.events(kind=kind, newest_first=True, limit=1)).payload

    def link(self, label: str, message: int = -1) -> str:
        """The token behind a button of a notification."""
        button = next(a for a in self.sent[message]["actions"] if a["label"] == label)
        assert button["action"] == "http"
        assert button["method"] == "POST"
        return str(button["url"]).removeprefix("https://act.example.org/act/")
