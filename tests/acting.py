"""A fake Docker and an executor around it, shared by the tests of stages 3 and 4."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2 as httpx

from paeon.catalog import Proposal, Step
from paeon.executor import DockerActuator, Executor, ExecutorStore
from paeon.links import Signer
from paeon.rules import Rules, parse_rules
from support import recording

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
        # name -> compose service and {network: aliases}, for the alias action
        self.networks: dict[str, tuple[str | None, dict[str, list[str]]]] = {}
        self.sticky_alias = True  # False: reconnecting does not bring the alias back

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/containers/json":
            return httpx.Response(
                200, json=[{"Id": ID[name], "Names": [f"/{name}"]} for name in self.states]
            )
        if path.startswith("/networks/"):
            _, _, network, verb = path.split("/")
            body = json.loads(request.content)
            name = next(n for n, i in ID.items() if i == body["Container"])
            self.posts.append(f"{verb} {name} {network}")
            if self.status >= 400:
                return httpx.Response(self.status)
            aliases = self.networks[name][1]
            net = network.removeprefix("net")
            if verb == "disconnect":
                aliases[net] = []
            elif self.sticky_alias:
                aliases[net] = list(body["EndpointConfig"]["Aliases"])
            return httpx.Response(200)
        _, _, container_id, verb = path.split("/")
        name = next(n for n, i in ID.items() if i == container_id)
        state = self.states[name]
        if request.method == "GET":
            service, nets = self.networks.get(name, (None, {}))
            labels = {"com.docker.compose.service": service} if service else {}
            return httpx.Response(
                200,
                json={
                    "State": state,
                    "RestartCount": 0,
                    "Config": {"Labels": labels},
                    "NetworkSettings": {
                        "Networks": {
                            "bridge": {"NetworkID": "b0", "Aliases": None},
                            **{
                                net: {"NetworkID": f"net{net}", "Aliases": aliases or None}
                                for net, aliases in nets.items()
                            },
                        }
                    },
                },
            )
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
    params = more.pop("params", {})
    return Proposal(
        incident_id=incident,
        title="Worker is down",
        origin=more.pop("origin", "investigation"),
        alerts=more.pop("alerts", (alert,)),
        confidence=confidence,
        summary="It stalled.",
        steps=tuple(
            Step(action=a, target=t, params=params if a == "config_change" else {})
            for a, t in steps or [("restart_container", "worker")]
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
        coolify_actuator: Any = None,
        repository_actuator: Any = None,
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
            coolify=coolify_actuator,
            repository=repository_actuator,
            notifiers=recording(self.rules, "actions", self.sent),
            kill_env=kill_env,
            sleep=self.slept.append,
            http_ok=lambda url: self.healthy_url,
            spawn=lambda job: job(),
        )

    def fail(self, error: str) -> None:
        """Make every notification fail from now on."""
        for notifier in self.executor._notifiers:
            notifier.fail = error  # type: ignore[attr-defined]

    def close(self) -> None:
        self.store.close()
        self.docker.close()

    def kinds(self) -> list[str]:
        return [event.kind for event in self.store.events()]

    def last(self, kind: str) -> dict[str, Any]:
        return next(self.store.events(kind=kind, newest_first=True, limit=1)).payload

    def link(self, label: str, message: int | None = None) -> str:
        """The token behind a button: of one notification, or of the latest that has it."""
        found = self.sent[::-1] if message is None else [self.sent[message]]
        button = next(a for sent in found for a in sent["actions"] if a["label"] == label)
        assert button["action"] == "http"
        assert button["method"] == "POST"
        return str(button["url"]).removeprefix("https://act.example.org/act/")

    def titles(self) -> list[str]:
        return [str(sent["title"]) for sent in self.sent]
