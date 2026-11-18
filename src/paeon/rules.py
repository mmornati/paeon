"""The rules file: correlation, maintenance windows, model aliases, triage and the digest.

One YAML file, meant to live in git next to the deployment (`PAEON_CONFIG=/path/paeon.yaml`).
Everything has a default, so the file is optional: without it Paeon correlates with the default
windows and calls no model. Secrets never go in the file; it names the environment variable that
holds them. Unknown keys are refused, so a typo is an error at start instead of a silent default.
"""

from __future__ import annotations

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


class OpenObserveTools(_Rule):
    """What the investigator may read in OpenObserve (with the PAEON_OPENOBSERVE_* account)."""

    streams: tuple[str, ...] = Field(
        default=(), description="Log streams that may be searched. Nothing else is readable."
    )
    metrics: bool = Field(default=False, description="Allow PromQL range queries.")
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


class InvestigationTools(_Rule):
    openobserve: OpenObserveTools | None = None
    docker: DockerTools | None = None
    knowledge: KnowledgeTools = Field(default_factory=KnowledgeTools)


TriageVerdict = Literal["noise", "transient", "known_issue", "real"]


class Investigation(_Rule):
    enabled: bool = False
    model: str = Field(default="investigate", description="An alias from `models`.")
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


class Postmortems(_Rule):
    enabled: bool = False
    dir: str = Field(default="postmortems", min_length=1)
    model: str = Field(default="postmortem", description="An alias from `models`.")


class Rules(_Rule):
    correlation: Correlation = Field(default_factory=Correlation)
    maintenance: tuple[MaintenanceWindow, ...] = ()
    models: dict[str, ModelAlias] = Field(default_factory=dict)
    triage: Triage = Field(default_factory=Triage)
    digest: Digest = Field(default_factory=Digest)
    investigation: Investigation = Field(default_factory=Investigation)
    postmortems: Postmortems = Field(default_factory=Postmortems)

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
