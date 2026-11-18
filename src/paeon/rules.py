"""The rules file: correlation, maintenance windows, model aliases, triage and the digest.

One YAML file, meant to live in git next to the deployment (`PAEON_CONFIG=/path/paeon.yaml`).
Everything has a default, so the file is optional: without it Paeon correlates with the default
windows and calls no model. Secrets never go in the file; it names the environment variable that
holds them. Unknown keys are refused, so a typo is an error at start instead of a silent default.
"""

from __future__ import annotations

import fnmatch
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

from paeon.schema import Alert, Severity


class RulesError(ValueError):
    pass


class _Rule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _regex(value: str) -> str:
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"not a regular expression: {exc}") from exc
    return value


def _timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone {value!r}") from exc
    return value


def _inside(value: str) -> str:
    if ".." in value.split("/") or "//" in value:
        raise ValueError("the path must stay inside the repository")
    return value


Regex = Annotated[str, AfterValidator(_regex)]
Timezone = Annotated[str, AfterValidator(_timezone)]


class Matcher(_Rule):
    """Matches an alert when every field given matches. An empty matcher matches everything."""

    source: str | None = None
    title: Regex | None = Field(default=None, description="Regular expression, full match.")
    severity: Severity | None = None
    labels: dict[str, str] = Field(default_factory=dict, description="Exact label values.")

    def matches(self, alert: Alert) -> bool:
        if self.source is not None and alert.source != self.source:
            return False
        if self.title is not None and re.fullmatch(self.title, alert.title) is None:
            return False
        if self.severity is not None and alert.severity is not self.severity:
            return False
        return all(alert.labels.get(key) == value for key, value in self.labels.items())


class Flap(_Rule):
    threshold: int = Field(default=3, ge=2, description="Firings within the window.")
    window_minutes: float = Field(default=60, gt=0)


class HostRule(_Rule):
    """Gives a host to alerts whose labels carry none (OpenObserve alerts, for example)."""

    match: Matcher
    host: str = Field(min_length=1)


class Correlation(_Rule):
    window_minutes: float = Field(
        default=30,
        ge=0,
        description="The same alert firing again this soon after its incident closed reopens it.",
    )
    host_window_minutes: float = Field(
        default=5,
        ge=0,
        description="An alert on the same host this soon after an incident opened joins it.",
    )
    host_labels: tuple[str, ...] = ("host", "instance", "hostname")
    hosts: tuple[HostRule, ...] = ()
    flap: Flap = Field(default_factory=Flap)

    def host_of(self, alert: Alert) -> str | None:
        for label in self.host_labels:
            value = alert.labels.get(label)
            if value:
                return value
        for rule in self.hosts:
            if rule.match.matches(alert):
                return rule.host
        return None


Day = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAYS: tuple[Day, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class MaintenanceWindow(_Rule):
    """A weekly window in which alerts are expected (an updater run, a backup)."""

    name: str = Field(min_length=1)
    days: tuple[Day, ...] = _DAYS
    start: time
    duration_minutes: float = Field(gt=0, le=24 * 60)
    timezone: Timezone = "UTC"
    match: Matcher = Field(default_factory=Matcher)

    def covers(self, moment: datetime) -> bool:
        local = moment.astimezone(ZoneInfo(self.timezone))
        # A window that started yesterday can still be running after midnight.
        for back in (0, 1):
            day = local.date() - timedelta(days=back)
            if _DAYS[day.weekday()] not in self.days:
                continue
            begin = datetime.combine(day, self.start, tzinfo=local.tzinfo)
            if begin <= local < begin + timedelta(minutes=self.duration_minutes):
                return True
        return False


class Price(_Rule):
    """Price per million tokens at call time, in the currency of your budget."""

    input_per_mtok: float = Field(default=0, ge=0)
    output_per_mtok: float = Field(default=0, ge=0)
    cached_input_per_mtok: float | None = Field(
        default=None, ge=0, description="Price of input read from the cache; input price if unset."
    )


class ModelAlias(_Rule):
    backend: Literal["anthropic", "openai"]
    model: str = Field(min_length=1)
    base_url: str | None = Field(
        default=None, description="Defaults to the provider's own API. Use it for a gateway."
    )
    api_key_env: str | None = Field(
        default=None, description="Name of the environment variable (or NAME_FILE) with the key."
    )
    max_tokens: int = Field(
        default=2000, ge=1, description="Output ceiling; on recent models it includes thinking."
    )
    timeout_seconds: float = Field(default=60, gt=0)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = Field(
        default=None, description="Anthropic backend only: `output_config.effort`."
    )
    price: Price = Field(default_factory=Price)


class Triage(_Rule):
    enabled: bool = False
    model: str = Field(default="triage", description="An alias from `models`.")
    confidence_threshold: float = Field(default=0.7, ge=0, le=1)
    settle_seconds: float = Field(
        default=60, ge=0, description="Wait this long so that related alerts join the incident."
    )
    max_per_hour: int = Field(default=20, ge=1, description="Storm guard.")
    daily_budget: float | None = Field(default=None, ge=0)
    history_days: int = Field(default=30, ge=1)
    skip: tuple[Matcher, ...] = Field(
        default=(), description="Page-level alerts: never triaged, always treated as real."
    )


class Ntfy(_Rule):
    url: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    token_env: str | None = None


class Digest(_Rule):
    hour: int = Field(default=8, ge=0, le=23)
    timezone: Timezone = "UTC"
    ntfy: Ntfy | None = None
    feedback: int = Field(
        default=5,
        ge=0,
        le=20,
        description="After the digest, one short message with right / wrong buttons for each"
        " of this many verdicts, those triage would have demoted first. Needs the executor,"
        " which signs the buttons. 0: none.",
    )


class OpenObserveTools(_Rule):
    """What the investigator may read in OpenObserve (with the PAEON_OPENOBSERVE_* account)."""

    streams: tuple[str, ...] = Field(
        default=(), description="Log streams that may be searched. Nothing else is readable."
    )
    metrics: tuple[str, ...] = Field(
        default=(),
        description="Metric name patterns (fnmatch) that PromQL queries may read. Empty: none.",
    )
    max_rows: int = Field(default=100, ge=1, le=1000)


class DockerTools(_Rule):
    """What the investigator may read from Docker, through a socket proxy limited to GET."""

    url: str = Field(min_length=1, description="For example http://dockerproxy:2375.")
    logs: tuple[str, ...] = Field(
        default=(), description="Container name patterns whose logs may be read (fnmatch)."
    )
    labels: tuple[str, ...] = Field(
        default=(), description="Label keys that `inspect` may return. The others are left out."
    )
    mount_sources: tuple[str, ...] = Field(
        default=(), description="Host path prefixes that `inspect` may show as mount sources."
    )
    max_log_lines: int = Field(default=200, ge=1, le=2000)


class KnowledgeTools(_Rule):
    paths: tuple[str, ...] = Field(
        default=(), description="Directories of markdown: runbooks, notes, a README."
    )


class UptimeKumaTools(_Rule):
    """The current state of every monitor, read from uptime-kuma's `/metrics` endpoint."""

    url: str = Field(min_length=1, description="For example http://uptime-kuma:3001.")
    api_key_env: str | None = Field(
        default=None, description="Environment variable (or NAME_FILE) with an API key."
    )


class GitTools(_Rule):
    """Local clones whose history may be read (the deployment repository, for example)."""

    repos: dict[str, str] = Field(
        default_factory=dict, description="Name the model uses, to the path of a working copy."
    )
    max_commits: int = Field(default=30, ge=1, le=200)


_NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"


class SshHost(_Rule):
    target: str = Field(pattern=r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+$", description="user@host.")
    port: int = Field(default=22, ge=1, le=65535)
    commands: dict[str, str] = Field(
        min_length=1,
        description="Name the model uses, to the command line you wrote. The model chooses a"
        " name; it never writes a command or an argument.",
    )


class SshTools(_Rule):
    """Other hosts, through commands you list. Pair it with a forced command on the far side."""

    hosts: dict[str, SshHost] = Field(default_factory=dict)
    identity_file: str = Field(min_length=1)
    known_hosts_file: str = Field(
        min_length=1, description="Host keys are checked strictly against this file."
    )
    timeout_seconds: float = Field(default=20, gt=0, le=120)

    @model_validator(mode="after")
    def _names(self) -> SshTools:
        for host, entry in self.hosts.items():
            for name in (host, *entry.commands):
                if re.fullmatch(_NAME, name) is None:
                    raise ValueError(f"{name!r} is not a plain name")
        return self


class InvestigationTools(_Rule):
    openobserve: OpenObserveTools | None = None
    docker: DockerTools | None = None
    knowledge: KnowledgeTools = Field(default_factory=KnowledgeTools)
    uptime_kuma: UptimeKumaTools | None = None
    git: GitTools | None = None
    ssh: SshTools | None = None


TriageVerdict = Literal["noise", "transient", "known_issue", "real"]


class Investigation(_Rule):
    enabled: bool = False
    model: str = Field(default="investigate", description="An alias from `models`.")
    fallback_model: str | None = Field(
        default=None,
        description="An alias tried once, from the start, when the first model fails or"
        " declines. The tool budget is shared between the two.",
    )
    verdicts: tuple[TriageVerdict, ...] = Field(
        default=("real", "known_issue"),
        description="Triage verdicts that are investigated. Without triage, every incident is.",
    )
    settle_seconds: float = Field(default=60, ge=0)
    max_tool_calls: int = Field(default=25, ge=1, le=200)
    max_seconds: float = Field(default=600, gt=0)
    max_result_chars: int = Field(default=12000, ge=500)
    max_per_hour: int = Field(default=6, ge=1, description="Storm guard.")
    budget_per_incident: float | None = Field(default=None, ge=0)
    daily_budget: float | None = Field(
        default=None, ge=0, description="Counts every model call of the day, all stages."
    )
    tools: InvestigationTools = Field(default_factory=InvestigationTools)
    ntfy: Ntfy | None = Field(default=None, description="Where the analysis is sent.")


class PostmortemGit(_Rule):
    """Commit each draft in the working copy that holds `postmortems.dir`."""

    commit: bool = True
    push: bool = Field(
        default=False, description="Push after the commit, to the branch's own upstream."
    )
    author_name: str = Field(default="Paeon", min_length=1, max_length=100)
    author_email: str = Field(default="paeon@localhost", min_length=3, max_length=200)
    identity_file: str | None = Field(
        default=None, description="A deploy key for the push, if the remote is reached over SSH."
    )
    known_hosts_file: str | None = None


class Postmortems(_Rule):
    enabled: bool = False
    dir: str = Field(default="postmortems", min_length=1)
    git: PostmortemGit | None = None
    model: str = Field(default="postmortem", description="An alias from `models`.")
    daily_budget: float | None = Field(
        default=None,
        ge=0,
        description="Counts every model call of the day, all stages. Spent: the draft is"
        " written without the model's prose.",
    )


class Ui(_Rule):
    """The console under `/ui`. It has no login of its own: the reverse proxy protects it."""

    enabled: bool = False
    require_header: str | None = Field(
        default=None,
        min_length=1,
        description="A header your proxy sets after authentication (for example"
        " X-authentik-username). A request without it is refused: a second net against a route"
        " that bypasses the proxy, not a login.",
    )
    public_url: str | None = Field(
        default=None,
        pattern=r"^https?://[^\s]+$",
        description="Where you reach the console (https://paeon.example.org). Notifications"
        " link to the incident page there.",
    )


class Telemetry(_Rule):
    """Traces of Paeon's own work, over OTLP/HTTP. Facts about each step, never its content."""

    otlp_endpoint: str = Field(
        pattern=r"^https?://[^\s]+$",
        description="The OTLP/HTTP base, without /v1/traces. OpenObserve:"
        " http://openobserve:5080/api/default.",
    )
    authorization_env: str | None = Field(
        default=None,
        description="Environment variable (or NAME_FILE) with the whole Authorization header,"
        " for example `Basic <base64 of user:password>`.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict, description="Other headers, none of them secret (stream-name)."
    )
    service_name: str = Field(default="paeon", min_length=1, max_length=100)
    interval_seconds: float = Field(default=30, ge=5, le=3600)


Level = Literal["observe", "suggest", "approve", "auto"]
LEVELS: tuple[Level, ...] = ("observe", "suggest", "approve", "auto")


class Verify(_Rule):
    """How the executor decides that an action worked. Written by you, never by a model."""

    wait_seconds: float = Field(default=15, ge=0, le=600)
    timeout_seconds: float = Field(default=120, gt=0, le=3600)
    http: str | None = Field(
        default=None,
        pattern=r"^https?://[^\s]+$",
        description="A URL that must answer below 400 (the service's health endpoint).",
    )


class CatalogEntry(_Rule):
    """One action of the closed set, switched on for the containers you name."""

    targets: tuple[str, ...] = Field(
        min_length=1, description="Container name patterns (fnmatch) it may be applied to."
    )
    deny: tuple[str, ...] = Field(
        default=(), description="Patterns refused even when a target pattern matches."
    )
    verify: Verify = Field(default_factory=Verify)

    def allows(self, target: str) -> bool:
        if any(fnmatch.fnmatchcase(target, pattern) for pattern in self.deny):
            return False
        return any(fnmatch.fnmatchcase(target, pattern) for pattern in self.targets)


class CoolifyResource(_Rule):
    uuid: str = Field(pattern=r"^[A-Za-z0-9]{6,64}$")
    kind: Literal["application", "service"] = "application"


class Coolify(_Rule):
    """Executor only: the Coolify resources `coolify_restart` may restart, by a name of yours."""

    url: str = Field(pattern=r"^https?://[^\s]+$", description="For example http://coolify:8000.")
    token_env: str = Field(min_length=1, description="An API token that can deploy.")
    resources: dict[str, CoolifyResource] = Field(default_factory=dict)


class RepoFile(_Rule):
    """One file of the deployment repository in which `config_change` may change values."""

    path: Annotated[str, AfterValidator(_inside)] = Field(
        pattern=r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,199}\.ya?ml$",
        description="Relative to the repository. YAML only.",
    )
    keys: tuple[str, ...] = Field(
        min_length=1,
        description="Dotted key patterns (fnmatch) that may be changed, for example"
        " services.*.mem_limit. The key must already exist and hold a single value.",
    )
    values: Regex = Field(
        default=r"[A-Za-z0-9][A-Za-z0-9_.:/@+=-]{0,99}",
        description="What a new value must look like, full match.",
    )

    def allows(self, key: str, value: str) -> bool:
        # Segment by segment: `services.*.mem_limit` is one name deep, and its `*` must not
        # swallow dots and reach `services.x.labels.mem_limit`.
        parts = key.split(".")

        def matches(pattern: str) -> bool:
            wanted = pattern.split(".")
            return len(wanted) == len(parts) and all(
                fnmatch.fnmatchcase(part, segment)
                for part, segment in zip(parts, wanted, strict=True)
            )

        return any(matches(pattern) for pattern in self.keys) and (
            re.fullmatch(self.values, value) is not None
        )


class GitHub(_Rule):
    repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", description="owner/name.")
    token_env: str = Field(
        min_length=1, description="A token that can open pull requests on that repository only."
    )
    api_url: str = Field(default="https://api.github.com", pattern=r"^https://[^\s]+$")


class Repository(_Rule):
    """Executor only: a clone of the deployment repository that belongs to Paeon.

    Paeon checks out branches in it and never merges anything: a change is a branch pushed
    with the key you name and, with `github`, a pull request. Merging it is your approval.
    """

    path: str = Field(min_length=1, description="A working copy nothing else uses.")
    remote: str = Field(default="origin", pattern=r"^[A-Za-z0-9_.-]+$")
    base: str = Field(default="main", pattern=r"^[A-Za-z0-9_./-]+$")
    files: dict[str, RepoFile] = Field(default_factory=dict)
    github: GitHub | None = None
    identity_file: str | None = None
    known_hosts_file: str | None = None
    author_name: str = Field(default="Paeon", min_length=1, max_length=100)
    author_email: str = Field(default="paeon@localhost", min_length=3, max_length=200)


class Policy(_Rule):
    """The autonomy level of one (alert class, action, target). The first match wins."""

    alert: Regex = Field(default=".*", description="Alert title, regular expression, full match.")
    action: str = Field(default="*", description="A catalog action, or * for all.")
    target: str = Field(default="*", description="Container name pattern (fnmatch).")
    level: Level


class Autonomy(_Rule):
    promote_after: int = Field(
        default=5, ge=1, description="Approved and verified runs before `auto` is proposed."
    )
    window_days: int = Field(default=90, ge=1)
    min_confidence: float = Field(
        default=0.8, ge=0, le=1, description="Under it, a plan never runs without approval."
    )


class RunbookStep(_Rule):
    action: str = Field(min_length=1)
    target: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class Runbook(_Rule):
    """A known signature and its fix: a hit skips both models."""

    name: str = Field(min_length=1, max_length=100)
    match: Matcher
    steps: tuple[RunbookStep, ...] = Field(min_length=1, max_length=10)
    mode: Literal["escalate", "sequence"] = Field(
        default="escalate",
        description="escalate: stop at the first step that fixes it. sequence: run them all.",
    )


class Actions(_Rule):
    """Stage 3 and 4: the catalog, who may approve what, and where the executor is.

    The same file is read by the core and by the executor (`paeon executor`). The executor
    trusts only its own copy: mount it read-only there, from git.
    """

    enabled: bool = False
    executor_url: str = Field(
        default="http://127.0.0.1:8081", description="Where the core reaches the executor."
    )
    public_url: str | None = Field(
        default=None,
        pattern=r"^https?://[^\s]+$",
        description="Where your phone reaches the executor's /act path. Without it there are"
        " no buttons, and nothing above `suggest` can happen.",
    )
    docker_url: str | None = Field(
        default=None,
        description="Executor only: a Docker socket proxy that allows start, stop and restart.",
    )
    docker_read_url: str | None = Field(
        default=None,
        description="Executor only: where it lists and inspects containers, when the proxy"
        " above allows nothing but the actions. Defaults to `docker_url`.",
    )
    lock_dir: str = Field(
        default="locks", description="One lock file per container, shared with other repairers."
    )
    approval_minutes: float = Field(default=30, gt=0, le=24 * 60)
    settle_seconds: float = Field(
        default=30, ge=0, description="A runbook waits this long: the alert may clear by itself."
    )
    max_per_hour: int = Field(default=6, ge=1, description="Plans executed per hour, at most.")
    default_level: Level = "suggest"
    ntfy: Ntfy | None = Field(
        default=None, description="The topic for approvals. Give it a token of its own."
    )
    catalog: dict[str, CatalogEntry] = Field(default_factory=dict)
    policies: tuple[Policy, ...] = ()
    autonomy: Autonomy = Field(default_factory=Autonomy)
    runbooks: tuple[Runbook, ...] = ()
    coolify: Coolify | None = None
    repository: Repository | None = None

    def refusal(self, action: str, target: str, params: dict[str, str]) -> str | None:
        """Why this catalog does not allow a step, or None. The one check both processes run:
        the core before it accepts a model's plan, the executor before it considers one."""
        from paeon.catalog import ACTIONS, PARAMS

        kind = ACTIONS.get(action)
        entry = self.catalog.get(action)
        if kind is None or not kind.proposable or entry is None:
            return f"{action} is not in the catalog"
        if not entry.allows(target):
            return f"{action} is not allowed on {target}"
        if set(params) != set(PARAMS.get(action, ())):
            return f"{action} takes {list(PARAMS.get(action, ())) or 'no parameter'}"
        if kind.backend == "coolify" and (
            self.coolify is None or target not in self.coolify.resources
        ):
            return f"{target} is not a Coolify resource of the rules file"
        if kind.backend == "repository":
            file = None if self.repository is None else self.repository.files.get(target)
            if file is None:
                return f"{target} is not a file of actions.repository"
            if not file.allows(params["key"], params["value"]):
                return f"{params['key']} = {params['value']} is not a change {target} allows"
        return None

    def level_for(self, alert: str, action: str, target: str) -> Level:
        for policy in self.policies:
            if (
                re.fullmatch(policy.alert, alert) is not None
                and policy.action in ("*", action)
                and fnmatch.fnmatchcase(target, policy.target)
            ):
                return policy.level
        return self.default_level

    def runbook_for(self, alert: Alert) -> Runbook | None:
        if not self.enabled:
            return None
        return next((book for book in self.runbooks if book.match.matches(alert)), None)


class Rules(_Rule):
    correlation: Correlation = Field(default_factory=Correlation)
    maintenance: tuple[MaintenanceWindow, ...] = ()
    models: dict[str, ModelAlias] = Field(default_factory=dict)
    triage: Triage = Field(default_factory=Triage)
    digest: Digest = Field(default_factory=Digest)
    investigation: Investigation = Field(default_factory=Investigation)
    postmortems: Postmortems = Field(default_factory=Postmortems)
    ui: Ui = Field(default_factory=Ui)
    actions: Actions = Field(default_factory=Actions)
    telemetry: Telemetry | None = None

    @model_validator(mode="after")
    def _actions_known(self) -> Rules:
        from paeon.catalog import ACTIONS

        known = sorted(name for name, kind in ACTIONS.items() if kind.proposable)
        for name in self.actions.catalog:
            if name not in known:
                raise ValueError(f"actions.catalog: {name!r} is not an action; known: {known}")
        for policy in self.actions.policies:
            if policy.action != "*" and policy.action not in known:
                raise ValueError(f"actions.policies: {policy.action!r} is not an action")
        for book in self.actions.runbooks:
            for step in book.steps:
                entry = self.actions.catalog.get(step.action)
                if entry is None or not entry.allows(step.target):
                    raise ValueError(
                        f"runbook {book.name!r}: {step.action} on {step.target} is not"
                        " allowed by actions.catalog"
                    )
                if ACTIONS[step.action].tier != 1:
                    raise ValueError(
                        f"runbook {book.name!r}: {step.action} is not something a runbook runs"
                    )
        fallback = self.investigation.fallback_model
        if self.investigation.enabled and fallback is not None and fallback not in self.models:
            raise ValueError(
                f"investigation.fallback_model {fallback!r} is not defined under `models`"
            )
        return self

    @model_validator(mode="after")
    def _models_exist(self) -> Rules:
        for name, stage in (
            ("triage", self.triage),
            ("investigation", self.investigation),
            ("postmortems", self.postmortems),
        ):
            if stage.enabled and stage.model not in self.models:
                raise ValueError(f"{name}.model {stage.model!r} is not defined under `models`")
        return self

    def maintenance_for(self, alert: Alert) -> str | None:
        for window in self.maintenance:
            if window.match.matches(alert) and window.covers(alert.observed_at):
                return window.name
        return None


def parse_rules(document: Any) -> Rules:
    if document is None:
        return Rules()
    if not isinstance(document, dict):
        raise RulesError("the rules file must be a YAML mapping")
    try:
        return Rules.model_validate(document)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise RulesError(f"invalid rules: {problems}") from exc


def load_rules(path: Path) -> Rules:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulesError(f"rules file cannot be read: {exc}") from exc
    try:
        return parse_rules(yaml.safe_load(text))
    except yaml.YAMLError as exc:
        raise RulesError(f"rules file is not valid YAML: {exc}") from exc
