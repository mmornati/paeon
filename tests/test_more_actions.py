"""Actions beyond a container's state: its network alias, a Coolify resource, and a change to
configuration that is only ever proposed, as a pull request."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from acting import Bench, FakeDocker, proposal
from paeon import proc
from paeon.actuators import ActuatorError, CoolifyActuator, RepositoryActuator, edit_yaml_scalar
from paeon.config import ConfigError, load_executor
from paeon.rules import Coolify, Repository, RulesError, parse_rules
from support import T0

AUTO = [{"level": "auto"}]

# --- the network alias ------------------------------------------------------------------------

ALIAS: dict[str, Any] = {
    "catalog": {"reattach_network_alias": {"targets": ["worker"], "verify": {"wait_seconds": 0}}},
    "runbooks": [],
}


def lost_alias(**networks: list[str]) -> FakeDocker:
    docker = FakeDocker(worker={})
    docker.networks["worker"] = ("worker", networks or {"apps": ["abc123"], "db": ["worker"]})
    return docker


def test_a_lost_alias_is_given_back_on_the_networks_that_miss_it(tmp_path: Path) -> None:
    docker = lost_alias()
    bench = Bench(tmp_path, docker=docker, policies=AUTO, **ALIAS)
    bench.executor.propose(proposal(("reattach_network_alias", "worker")), T0)
    # Only the network that lost it is touched, and what it had is kept.
    assert docker.posts == ["disconnect worker netapps", "connect worker netapps"]
    assert docker.networks["worker"][1] == {"apps": ["abc123", "worker"], "db": ["worker"]}
    assert bench.last("plan.completed")["outcome"] == "verified"
    assert "Reattach the network alias of a container (worker)" in bench.sent[0]["message"]
    assert [a["label"] for a in bench.sent[0]["actions"]] == ["This was wrong", "Stop automation"]


@pytest.mark.parametrize(
    ("docker", "reason"),
    [
        (lost_alias(apps=["worker"]), "it already answers to worker on every network"),
        (FakeDocker(worker={}), "it is not a compose service: there is no alias to give back"),
    ],
)
def test_the_alias_action_checks_there_is_something_to_fix(
    tmp_path: Path, docker: FakeDocker, reason: str
) -> None:
    bench = Bench(tmp_path, docker=docker, policies=AUTO, **ALIAS)
    bench.executor.propose(proposal(("reattach_network_alias", "worker")), T0)
    assert bench.last("action.failed")["reason"] == reason
    assert docker.posts == []


def test_an_alias_that_does_not_come_back_or_a_refusal_fails_the_step(tmp_path: Path) -> None:
    docker = lost_alias()
    docker.sticky_alias = False
    bench = Bench(tmp_path / "a", docker=docker, policies=AUTO, **ALIAS)
    bench.executor.propose(proposal(("reattach_network_alias", "worker")), T0)
    assert bench.last("action.failed")["reason"] == "the alias is still missing after reconnecting"

    docker = lost_alias()
    docker.status = 403  # the proxy does not allow networks
    bench = Bench(tmp_path / "b", docker=docker, policies=AUTO, **ALIAS)
    bench.executor.propose(proposal(("reattach_network_alias", "worker")), T0)
    assert bench.last("action.failed")["reason"] == "docker: docker answered HTTP 403 to disconnect"


# --- Coolify ----------------------------------------------------------------------------------

COOLIFY: dict[str, Any] = {
    "url": "http://coolify:8000",
    "token_env": "COOLIFY_TOKEN",
    "resources": {
        "media": {"uuid": "abc123def", "kind": "service"},
        "api": {"uuid": "zzz999yyy"},
    },
}
WITH_COOLIFY: dict[str, Any] = {
    "catalog": {
        "coolify_restart": {"targets": ["media", "api", "ghost"], "verify": {"wait_seconds": 0}}
    },
    "coolify": COOLIFY,
    "runbooks": [],
}


class FakeCoolify:
    def __init__(self, *statuses: Any) -> None:
        self.statuses = list(statuses)
        self.seen: list[str] = []
        self.fail: int | None = None
        self.http = httpx.Client(
            base_url="http://coolify:8000",
            headers={"Authorization": "Bearer coolify-token"},
            transport=httpx.MockTransport(self.handle),
        )
        self.actuator = CoolifyActuator(self.http, Coolify.model_validate(COOLIFY))

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer coolify-token"
        self.seen.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(self.fail or 200, json={"message": "Restart request queued."})
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if isinstance(status, Exception):
            raise status
        return httpx.Response(200, json={"uuid": "x"} if status is None else {"status": status})


def test_a_coolify_resource_is_restarted_through_its_api_and_verified(tmp_path: Path) -> None:
    coolify = FakeCoolify("exited:unhealthy", "restarting", "running:healthy")
    bench = Bench(tmp_path, coolify_actuator=coolify.actuator, policies=AUTO, **WITH_COOLIFY)
    try:
        assert bench.executor.propose(proposal(("coolify_restart", "media")), T0)["level"] == "auto"
    finally:
        coolify.http.close()
    assert coolify.seen[:2] == [
        "GET /api/v1/services/abc123def",
        "POST /api/v1/services/abc123def/restart",
    ]
    assert bench.last("plan.completed")["outcome"] == "verified"
    assert bench.docker.posts == []  # Coolify did it, not the Docker proxy
    assert "Restart a Coolify resource, same version (media)" in bench.sent[0]["message"]


def test_coolify_failures_fail_the_step_without_the_token_or_the_url(tmp_path: Path) -> None:
    for position, (coolify, reason) in enumerate(
        (
            (FakeCoolify("running:unhealthy"), "coolify says running:unhealthy"),
            (FakeCoolify(httpx.ConnectError("http://coolify:8000 Bearer x")), None),
            (FakeCoolify("running:healthy"), "coolify answered HTTP 500"),
            (FakeCoolify(None), ""),  # no status given: the accepted restart is all there is
        )
    ):
        if position == 2:
            coolify.fail = 500
        bench = Bench(
            tmp_path / str(position),
            coolify_actuator=coolify.actuator,
            policies=AUTO,
            **WITH_COOLIFY,
        )
        try:
            bench.executor.propose(proposal(("coolify_restart", "api")), T0)
        finally:
            coolify.http.close()
        if reason == "":
            assert bench.last("plan.completed")["outcome"] == "verified"
            continue
        failed = bench.last("action.failed")["reason"]
        assert failed == (reason or "coolify is unreachable: ConnectError")
        assert "Bearer" not in failed
    assert coolify.seen[0] == "GET /api/v1/applications/zzz999yyy"


def test_a_resource_the_rules_do_not_name_is_refused_and_no_endpoint_means_suggest(
    tmp_path: Path,
) -> None:
    bench = Bench(tmp_path, policies=AUTO, **WITH_COOLIFY)
    assert bench.executor.propose(proposal(("coolify_restart", "ghost")), T0) == {
        "status": "refused",
        "reason": "ghost is not a Coolify resource of the rules file",
    }
    answer = bench.executor.propose(proposal(("coolify_restart", "media"), incident=2), T0)
    assert answer["level"] == "suggest"
    assert bench.last("plan.proposed")["held_back_by"] == ["the executor has no Coolify endpoint"]


# --- a value in a YAML file -------------------------------------------------------------------

COMPOSE = """\
# The media stack. Keep the limits in step with the host's memory.
services:
  worker:
    image: example/worker:2.1   # pinned
    mem_limit: 256m
    environment:
      - MODE=prod
    deploy:
      resources:
        limits: { memory: "256M", cpus: '0.5' }
  db:
    mem_limit: 1g
"""


def test_one_value_changes_and_nothing_else() -> None:
    edited = edit_yaml_scalar(COMPOSE, "services.worker.mem_limit", "512m")
    assert edited == COMPOSE.replace("mem_limit: 256m", "mem_limit: 512m")
    assert "# pinned" in edited
    # Quotes are kept as the author wrote them, and a list is reached by position.
    assert 'memory: "1G"' in edit_yaml_scalar(
        COMPOSE, "services.worker.deploy.resources.limits.memory", "1G"
    )
    assert "cpus: '1.5'" in edit_yaml_scalar(
        COMPOSE, "services.worker.deploy.resources.limits.cpus", "1.5"
    )
    assert "- MODE=debug" in edit_yaml_scalar(
        COMPOSE, "services.worker.environment.0", "MODE=debug"
    )


@pytest.mark.parametrize(
    ("text", "key", "value", "message"),
    [
        (COMPOSE, "services.worker.cpu_limit", "1", "has no single key"),
        (COMPOSE, "services.worker", "x", "does not hold a single value"),
        (COMPOSE, "services.worker.environment.7", "x", "has no key"),
        (COMPOSE, "services.worker.mem_limit.unit", "x", "has no key"),
        (COMPOSE, "services.worker.mem_limit", "512m\nprivileged: true", "plain single value"),
        (COMPOSE, "services.worker.mem_limit", "512m # x", "plain single value"),
        (COMPOSE, "services.worker.mem_limit", "{a: b}", "plain single value"),
        ("a: [unclosed", "a", "x", "not valid YAML"),
        ("", "a", "x", "empty"),
        ("a: |\n  text\n", "a", "x", "does not hold a single value"),
        # An anchored value is shared: changing it would change the other place too.
        ("a: &v 1\nb: *v\n", "a", "2", "the edit would"),
        ("a: 1\na: 2\n", "a", "3", "has no single key"),
    ],
)
def test_an_edit_that_is_not_one_plain_value_is_refused(
    text: str, key: str, value: str, message: str
) -> None:
    with pytest.raises(ActuatorError, match=message):
        edit_yaml_scalar(text, key, value)


# --- the deployment repository ----------------------------------------------------------------


def git(repo: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}
    code, output = proc.run(["git", "-C", str(repo), *args], env={**proc.GIT_ENV, **env})
    assert code == 0, output
    return output


class Forge:
    """A bare repository standing for the remote, a clone that is Paeon's, and a fake GitHub."""

    def __init__(self, tmp_path: Path, *, github: bool = True) -> None:
        self.remote = tmp_path / "remote.git"
        seed = tmp_path / "seed"
        seed.mkdir(parents=True)
        git(seed, "init", "--quiet", "--initial-branch=main")
        (seed / "compose.yaml").write_text(COMPOSE)
        git(seed, "add", ".")
        git(seed, "commit", "--quiet", "-m", "compose")
        proc.run(["git", "clone", "--quiet", "--bare", str(seed), str(self.remote)])
        self.clone = tmp_path / "clone"
        proc.run(["git", "clone", "--quiet", str(self.remote), str(self.clone)])
        self.requests: list[dict[str, Any]] = []
        self.status = 201
        document: dict[str, Any] = {
            "path": str(self.clone),
            "files": {"compose": {"path": "compose.yaml", "keys": ["services.*.mem_limit"]}},
        }
        if github:
            document["github"] = {"repo": "example/deployment", "token_env": "GH"}
        self.config = Repository.model_validate(document)
        self.http = httpx.Client(
            base_url="https://api.github.com", transport=httpx.MockTransport(self.handle)
        )
        self.actuator = RepositoryActuator(
            self.config, token="gh-token", http=self.http if github else None
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        import json

        assert request.url.path == "/repos/example/deployment/pulls"
        assert request.headers["authorization"] == "Bearer gh-token"
        self.requests.append(json.loads(request.content))
        return httpx.Response(
            self.status, json={"html_url": "https://github.com/example/deployment/pull/7"}
        )

    def rules(self) -> dict[str, Any]:
        return {
            "catalog": {
                "config_change": {"targets": ["compose"]},
                "restart_container": {"targets": ["worker"], "verify": {"wait_seconds": 0}},
            },
            "repository": self.config.model_dump(mode="json"),
            "policies": [{"action": "config_change", "level": "auto"}, {"level": "approve"}],
            "runbooks": [],
        }


CHANGE = {"key": "services.worker.mem_limit", "value": "512m"}


def test_a_change_becomes_a_pull_request_and_nothing_is_applied(tmp_path: Path) -> None:
    forge = Forge(tmp_path)
    bench = Bench(tmp_path / "bench", repository_actuator=forge.actuator, **forge.rules())
    try:
        answer = bench.executor.propose(proposal(("config_change", "compose"), params=CHANGE), T0)
    finally:
        forge.http.close()
    # `auto` in the policy still only proposes: merging is the approval.
    assert answer["level"] == "approve"
    assert (
        "a configuration change is only ever proposed"
        in bench.last("plan.proposed")["held_back_by"]
    )
    assert bench.docker.posts == []
    done = bench.last("change.proposed")
    assert done["url"] == "https://github.com/example/deployment/pull/7"
    assert done["files"] == ["compose.yaml"]
    assert done["steps"] == ["config_change(compose: services.worker.mem_limit = 512m)"]
    branch = done["branch"]
    assert branch.startswith("paeon/incident-1-")

    # The remote's main is untouched; the branch has exactly the one line changed.
    assert "mem_limit: 256m" in git(forge.remote, "show", "main:compose.yaml")
    diff = git(forge.remote, "diff", "main", branch)
    assert "-    mem_limit: 256m" in diff
    assert "+    mem_limit: 512m" in diff
    assert diff.count("\n+ ") + diff.count("\n- ") == 2
    assert "Paeon|Incident 1: Worker is down" in git(
        forge.remote, "log", "-1", "--format=%an|%s", branch
    )
    (pull,) = forge.requests
    assert (pull["head"], pull["base"]) == (branch, "main")
    assert "Merging this is the approval" in pull["body"]
    sent = bench.sent[0]
    assert sent["title"] == "Incident #1: a change waits for your review"
    assert sent["click"] == "https://github.com/example/deployment/pull/7"
    assert sent["actions"] == []  # nothing to approve here: it is done on the forge
    assert "approval.requested" not in bench.kinds()
    assert "plan.completed" not in bench.kinds()  # not a run: it earns and loses no autonomy


def test_a_change_and_a_plan_of_actions_do_not_cancel_each_other(tmp_path: Path) -> None:
    forge = Forge(tmp_path, github=False)
    bench = Bench(tmp_path / "bench", repository_actuator=forge.actuator, **forge.rules())
    bench.executor.propose(proposal(), T0)  # waits for Go
    bench.executor.propose(proposal(("config_change", "compose"), params=CHANGE), T0)
    assert "plan.cancelled" not in bench.kinds()
    done = bench.last("change.proposed")
    assert done["url"] is None  # no forge API: the branch is pushed, and the message says so
    assert f"Branch {done['branch']} is pushed." in bench.sent[-1]["message"]
    assert bench.executor.act(bench.link("Go", 0), T0).ok
    assert bench.docker.posts == ["restart worker"]
    # A second, different change starts from the remote's main again, not from the first.
    other = {"key": "services.db.mem_limit", "value": "2g"}
    bench.executor.propose(proposal(("config_change", "compose"), params=other, incident=2), T0)
    second = bench.last("change.proposed")["branch"]
    diff = git(forge.remote, "diff", "main", second)
    assert "+    mem_limit: 2g" in diff
    assert "512m" not in diff


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({"key": "services.worker.image", "value": "evil/image"}, "is not a change compose allows"),
        ({"key": "services.worker.mem_limit", "value": "1g; rm"}, "is not a change compose allows"),
        ({"key": "services.worker.mem_limit"}, "config_change takes ['key', 'value']"),
        ({**CHANGE, "path": "/etc/passwd"}, "config_change takes ['key', 'value']"),
    ],
)
def test_a_change_outside_what_the_file_allows_is_refused(
    tmp_path: Path, params: dict[str, str], reason: str
) -> None:
    forge = Forge(tmp_path)
    forge.http.close()
    bench = Bench(tmp_path / "bench", repository_actuator=forge.actuator, **forge.rules())
    answer = bench.executor.propose(proposal(("config_change", "compose"), params=params), T0)
    assert answer["status"] == "refused"
    assert reason in answer["reason"]
    assert git(forge.remote, "branch", "--list", "paeon/*") == ""


def test_changes_are_refused_mixed_with_actions_from_a_runbook_or_without_a_repository(
    tmp_path: Path,
) -> None:
    forge = Forge(tmp_path)
    forge.http.close()
    rules = forge.rules()
    bench = Bench(tmp_path / "a", repository_actuator=forge.actuator, **rules)
    mixed = proposal(("config_change", "compose"), ("restart_container", "worker"), params=CHANGE)
    assert "cannot be one plan" in bench.executor.propose(mixed, T0)["reason"]
    book = proposal(("config_change", "compose"), params=CHANGE, origin="runbook", runbook="x")
    assert bench.executor.propose(book, T0)["reason"] == "a runbook does not change configuration"
    stray = proposal(("restart_container", "worker")).model_dump(mode="json")
    stray["steps"][0]["params"] = {"key": "a"}  # an action that takes none is given one
    refused = bench.executor.propose(type(mixed).model_validate(stray), T0)
    assert refused["reason"] == "restart_container takes no parameter"
    handless = Bench(tmp_path / "b", **rules)
    answer = handless.executor.propose(proposal(("config_change", "compose"), params=CHANGE), T0)
    assert answer["reason"] == "the executor has no deployment repository"


def test_a_failed_change_is_recorded_and_the_kill_switch_stops_it(tmp_path: Path) -> None:
    forge = Forge(tmp_path)
    forge.status = 422
    bench = Bench(tmp_path / "a", repository_actuator=forge.actuator, **forge.rules())
    try:
        bench.executor.propose(proposal(("config_change", "compose"), params=CHANGE), T0)
        failed = bench.last("change.failed")["reason"]
        assert "is pushed, but the pull request was refused (HTTP 422)" in failed
        assert bench.store.proposals(proposal_id=1)[0].status == "failed"

        # The key is allowed by the rules but is not in the file: nothing is pushed.
        missing = {"key": "services.cache.mem_limit", "value": "1g"}
        bench.executor.propose(
            proposal(("config_change", "compose"), params=missing, incident=2), T0
        )
        assert "has no single key" in bench.last("change.failed")["reason"]
    finally:
        forge.http.close()

    stopped = Bench(tmp_path / "b", repository_actuator=forge.actuator, **forge.rules())
    stopped.executor._spawn = lambda job: None
    stopped.executor.propose(proposal(("config_change", "compose"), params=CHANGE, incident=3), T0)
    stopped.store.append("killswitch.engaged", {"via": "cli"})
    stopped.executor.open_change(1)
    assert stopped.store.proposals(proposal_id=1)[0].status == "cancelled"
    assert "change.proposed" not in stopped.kinds()

    def no_git(argv: Any, **kwargs: Any) -> tuple[int, str]:
        raise FileNotFoundError("git")

    broken = RepositoryActuator(forge.config, run=no_git)
    with pytest.raises(ActuatorError, match="git fetch could not be run: FileNotFoundError"):
        broken.open(1, proposal(("config_change", "compose"), params=CHANGE), [])


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (
            {"repository": {"path": "/r", "files": {"c": {"path": "../x.yaml", "keys": ["a"]}}}},
            "path",
        ),
        (
            {"repository": {"path": "/r", "files": {"c": {"path": "/etc/x.yaml", "keys": ["a"]}}}},
            "path",
        ),
        (
            {
                "repository": {
                    "path": "/r",
                    "files": {"c": {"path": "a/../../x.yml", "keys": ["a"]}},
                }
            },
            "inside",
        ),
        ({"repository": {"path": "/r", "files": {"c": {"path": "x.json", "keys": ["a"]}}}}, "path"),
        ({"repository": {"path": "/r", "files": {"c": {"path": "x.yaml", "keys": []}}}}, "keys"),
        ({"repository": {"path": "/r", "github": {"repo": "nope", "token_env": "T"}}}, "repo"),
        (
            {"coolify": {"url": "http://c", "token_env": "T", "resources": {"a": {"uuid": "x/y"}}}},
            "uuid",
        ),
        (
            {
                "catalog": {"config_change": {"targets": ["c"]}},
                "runbooks": [
                    {
                        "name": "x",
                        "match": {},
                        "steps": [{"action": "config_change", "target": "c"}],
                    }
                ],
            },
            "not something a runbook runs",
        ),
    ],
)
def test_the_rules_refuse_unsafe_repositories_and_resources(
    document: dict[str, Any], message: str
) -> None:
    with pytest.raises(RulesError, match=message):
        parse_rules({"actions": document})


def test_the_executor_needs_the_tokens_its_rules_name(tmp_path: Path) -> None:
    rules = tmp_path / "paeon.yaml"
    rules.write_text(
        "actions:\n  enabled: true\n"
        "  coolify: {url: 'http://coolify:8000', token_env: CT}\n"
        "  repository: {path: /repo, github: {repo: example/deployment, token_env: GT}}\n"
    )
    env = {"PAEON_EXECUTOR_TOKEN": "e" * 32, "PAEON_CONFIG": str(rules)}
    with pytest.raises(ConfigError, match="CT"):
        load_executor(env)
    with pytest.raises(ConfigError, match="GT"):
        load_executor({**env, "CT": "c-token"})
    settings = load_executor({**env, "CT": "c-token", "GT": "g-token"})
    assert (settings.coolify_token, settings.github_token) == ("c-token", "g-token")
    from paeon.executor import ExecutorStore
    from paeon.executor_app import build_executor

    store = ExecutorStore(tmp_path / "e.sqlite")
    try:
        built = build_executor(settings, store)
        state = built.state(T0)
        assert (state["can_act"], state["can_change"]) == (True, True)
    finally:
        built._coolify.close()  # type: ignore[union-attr]
        built._repository.close()  # type: ignore[union-attr]
        store.close()
