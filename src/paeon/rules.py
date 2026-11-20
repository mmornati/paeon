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
from typing import Annotated, Any, ClassVar, Literal, Self
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

    source: str | None = Field(
        default=None,
        description="The alert's source, exactly: `uptime-kuma`, `alertmanager`, `openobserve`.",
    )
    title: Regex | None = Field(default=None, description="Regular expression, full match.")
    severity: Severity | None = Field(
        default=None,
        description="The alert's severity, exactly: `info`, `warning`, `critical` or `unknown`.",
    )
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
    window_minutes: float = Field(
        default=60, gt=0, description="The window in which the firings are counted."
    )


class HostRule(_Rule):
    """Gives a host to alerts whose labels carry none (OpenObserve alerts, for example)."""

    match: Matcher = Field(description="The alerts this rule gives a host to.")
    host: str = Field(min_length=1, description="The host they are given.")


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
    host_labels: tuple[str, ...] = Field(
        default=("host", "instance", "hostname"),
        description="Labels that name the host, first match wins.",
    )
    hosts: tuple[HostRule, ...] = Field(
        default=(), description="Rules that give a host to alerts whose labels carry none."
    )
    flap: Flap = Field(default_factory=Flap, description="When an incident is flagged as flapping.")

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

    name: str = Field(min_length=1, description="A name of yours, shown on the incident.")
    days: tuple[Day, ...] = Field(
        default=_DAYS, description="The days it opens on. Default: every day."
    )
    start: time = Field(description="The local time it opens, `HH:MM`.")
    duration_minutes: float = Field(
        gt=0, le=24 * 60, description="How long it stays open. It may run past midnight."
    )
    timezone: Timezone = Field(default="UTC", description="The timezone of `start`.")
    match: Matcher = Field(
        default_factory=Matcher, description="The alerts it covers. Empty: all of them."
    )

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

    input_per_mtok: float = Field(
        default=0,
        ge=0,
        description="Price of a million input tokens. 0: this model's calls cost nothing and"
        " no budget stops them.",
    )
    output_per_mtok: float = Field(default=0, ge=0, description="Price of a million output tokens.")
    cached_input_per_mtok: float | None = Field(
        default=None, ge=0, description="Price of input read from the cache; input price if unset."
    )


class ModelAlias(_Rule):
    backend: Literal["anthropic", "openai"] = Field(
        description="The API the model is called through: Anthropic's Messages API, or the"
        " OpenAI chat completions format that most gateways and local servers"
        " speak."
    )
    model: str = Field(min_length=1, description="The model's name, as that API expects it.")
    base_url: str | None = Field(
        default=None, description="Defaults to the provider's own API. Use it for a gateway."
    )
    api_key_env: str | None = Field(
        default=None, description="Name of the environment variable (or NAME_FILE) with the key."
    )
    max_tokens: int = Field(
        default=2000, ge=1, description="Output ceiling; on recent models it includes thinking."
    )
    timeout_seconds: float = Field(default=60, gt=0, description="How long one call may take.")
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = Field(
        default=None, description="Anthropic backend only: `output_config.effort`."
    )
    price: Price = Field(
        default_factory=Price, description="What a call costs. Budgets are counted from it."
    )


class Triage(_Rule):
    enabled: bool = Field(default=False, description="Switches the stage on.")
    model: str = Field(default="triage", description="An alias from `models`.")
    confidence_threshold: float = Field(
        default=0.7,
        ge=0,
        le=1,
        description="A verdict under this confidence is not trusted: the incident is treated"
        " as real.",
    )
    settle_seconds: float = Field(
        default=60, ge=0, description="Wait this long so that related alerts join the incident."
    )
    max_per_hour: int = Field(default=20, ge=1, description="Storm guard.")
    daily_budget: float | None = Field(
        default=None,
        ge=0,
        description="What triage may spend in a day, in the currency of `price`. Spent:"
        " incidents are treated as real, with no call.",
    )
    history_days: int = Field(
        default=30,
        ge=1,
        description="How far back the alert's own history goes in what the model is given.",
    )
    skip: tuple[Matcher, ...] = Field(
        default=(), description="Page-level alerts: never triaged, always treated as real."
    )


class Ntfy(_Rule):
    url: str = Field(
        min_length=1, description="The ntfy server, for example https://ntfy.example.org."
    )
    topic: str = Field(min_length=1, description="The topic messages are published to.")
    token_env: str | None = Field(
        default=None,
        description="Environment variable (or NAME_FILE) with an access token. Leave it out"
        " for an open topic.",
    )


class NtfyChannel(Ntfy):
    """A topic on an ntfy server. The only service whose buttons can act in one tap."""

    type: Literal["ntfy"]

    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        """The secrets this channel reads: key, environment variable, whether it is required."""
        return {} if self.token_env is None else {"token": (self.token_env, False)}


class _Channel(_Rule):
    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        """The secrets this channel reads: key, environment variable, whether it is required."""
        return {}


class TelegramChannel(_Channel):
    """A chat with a Telegram bot. Buttons are links under the message."""

    type: Literal["telegram"]
    chat_id: str = Field(min_length=1, description="The chat, group or channel the bot posts to.")
    token_env: str = Field(min_length=1, description="The bot's token, from BotFather.")
    api_url: str = Field(
        default="https://api.telegram.org",
        pattern=r"^https?://[^\s]+$",
        description="Change it only for a Bot API server of your own.",
    )

    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        return {"token": (self.token_env, True)}


class DiscordChannel(_Channel):
    """A channel's webhook. Buttons are link buttons under the message."""

    type: Literal["discord"]
    webhook_env: str = Field(
        min_length=1, description="The webhook URL. It is a secret: whoever has it can post."
    )
    username: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        description="The name the message is posted under. Default: the webhook's own.",
    )

    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        return {"webhook": (self.webhook_env, True)}


class SlackChannel(_Channel):
    """An incoming webhook. Buttons are links in the text: a Slack button, even one that only
    opens a link, expects an app that answers the click."""

    type: Literal["slack"]
    webhook_env: str = Field(
        min_length=1, description="The webhook URL. It is a secret: whoever has it can post."
    )

    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        return {"webhook": (self.webhook_env, True)}


class EmailChannel(_Channel):
    """Mail through an SMTP server. Buttons are links in the body."""

    type: Literal["email"]
    host: str = Field(min_length=1, description="The SMTP server.")
    port: int = Field(
        default=587, ge=1, le=65535, description="Its port: 587 with `starttls`, 465 with `tls`."
    )
    security: Literal["starttls", "tls", "none"] = Field(
        default="starttls",
        description="`starttls` (port 587), `tls` from the first byte (port 465), or `none`"
        " for a relay on your own network.",
    )
    username: str | None = Field(
        default=None,
        min_length=1,
        description="The login, if the server asks for one. Goes with `password_env`.",
    )
    password_env: str | None = Field(
        default=None,
        min_length=1,
        description="Environment variable (or NAME_FILE) with the password.",
    )
    sender: str = Field(
        min_length=3,
        pattern=r"^[^\s@<>]+@[^\s@<>]+$",
        description="The From address, a bare address.",
    )
    to: tuple[str, ...] = Field(
        min_length=1, description="The addresses the mail is sent to, one or more."
    )

    @model_validator(mode="after")
    def _addresses(self) -> EmailChannel:
        for address in self.to:
            if not re.fullmatch(r"[^\s@<>,]+@[^\s@<>,]+", address):
                raise ValueError(f"to: {address!r} is not an address")
        if (self.username is None) != (self.password_env is None):
            raise ValueError("username and password_env go together")
        return self

    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        return {} if self.password_env is None else {"password": (self.password_env, True)}


class WebhookChannel(_Channel):
    """A POST of the message as JSON, for what Paeon has no adapter for (n8n, Node-RED, a
    script of yours). Buttons are a list of labels and links in the body."""

    type: Literal["webhook"]
    url: str | None = Field(
        default=None, pattern=r"^https?://[^\s]+$", description="Where the message is posted."
    )
    url_env: str | None = Field(
        default=None, min_length=1, description="Instead of `url`, when the URL holds a secret."
    )
    authorization_env: str | None = Field(
        default=None, min_length=1, description="The value of the Authorization header."
    )

    @model_validator(mode="after")
    def _one_url(self) -> WebhookChannel:
        if (self.url is None) == (self.url_env is None):
            raise ValueError("give `url` or `url_env`, one of the two")
        return self

    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        found: dict[str, tuple[str, bool]] = {}
        if self.url_env is not None:
            found["url"] = (self.url_env, True)
        if self.authorization_env is not None:
            found["authorization"] = (self.authorization_env, True)
        return found


class GotifyChannel(_Channel):
    """An application of a Gotify server. Links are lines of the message."""

    type: Literal["gotify"]
    url: str = Field(pattern=r"^https?://[^\s]+$", description="The server, without `/message`.")
    token_env: str = Field(min_length=1, description="The application's token.")

    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        return {"token": (self.token_env, True)}


class PushoverChannel(_Channel):
    """Pushover. Links are in the message; the page a tap opens is its one extra URL."""

    type: Literal["pushover"]
    token_env: str = Field(min_length=1, description="The application's API token.")
    user_env: str = Field(min_length=1, description="The user or group key.")
    device: str | None = Field(
        default=None,
        min_length=1,
        max_length=25,
        description="Limit delivery to one of your devices, by its name.",
    )

    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        return {"token": (self.token_env, True), "user": (self.user_env, True)}


class TeamsChannel(_Channel):
    """Microsoft Teams, through a Workflows webhook. Buttons open the link."""

    type: Literal["teams"]
    webhook_env: str = Field(
        min_length=1, description="The workflow's URL. It is a secret: whoever has it can post."
    )

    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        return {"webhook": (self.webhook_env, True)}


class MatrixChannel(_Channel):
    """A room on a Matrix homeserver. Links are in the message."""

    type: Literal["matrix"]
    homeserver: str = Field(
        pattern=r"^https?://[^\s]+$",
        description="The homeserver's base URL, for example https://matrix.example.org.",
    )
    room_id: str = Field(pattern=r"^![^\s]+$", description="The room's id (`!abc:example.org`).")
    token_env: str = Field(min_length=1, description="An access token of the account that posts.")

    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        return {"token": (self.token_env, True)}


class AppriseChannel(_Channel):
    """Any service Apprise knows, for what Paeon has no adapter of its own. Needs the optional
    install (`paeon[apprise]`). Links are lines of the message."""

    type: Literal["apprise"]
    urls_env: str = Field(
        min_length=1,
        description="One or more Apprise URLs, separated by spaces or new lines. They hold the"
        " services' secrets.",
    )

    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        return {"urls": (self.urls_env, True)}


# One entry of `notifiers`, told apart by its `type`.
Channel = Annotated[
    NtfyChannel
    | TelegramChannel
    | DiscordChannel
    | SlackChannel
    | EmailChannel
    | WebhookChannel
    | GotifyChannel
    | PushoverChannel
    | TeamsChannel
    | MatrixChannel
    | AppriseChannel,
    Field(discriminator="type"),
]
_CHANNEL_NAME = r"^[a-z0-9][a-z0-9_-]{0,31}$"
_NOTIFY = Field(
    default=(),
    description="Names from `notifiers`: every channel listed gets the message.",
)


class Digest(_Rule):
    hour: int = Field(
        default=8,
        ge=0,
        le=23,
        description="The hour, in `timezone`, from which the day's digest is sent.",
    )
    timezone: Timezone = Field(
        default="UTC", description="The timezone of `hour`, and of the times the console shows."
    )
    ntfy: Ntfy | None = Field(default=None, description="Short for one ntfy channel.")
    notify: tuple[str, ...] = _NOTIFY
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
    max_rows: int = Field(
        default=100, ge=1, le=1000, description="Rows one log search may return, at most."
    )


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
    max_log_lines: int = Field(
        default=200, ge=1, le=2000, description="Log lines one call may return, at most."
    )


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
    max_commits: int = Field(
        default=30, ge=1, le=200, description="Commits one call may return, at most."
    )


_NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"


class SshHost(_Rule):
    target: str = Field(pattern=r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+$", description="user@host.")
    port: int = Field(default=22, ge=1, le=65535, description="The SSH port.")
    commands: dict[str, str] = Field(
        min_length=1,
        description="Name the model uses, to the command line you wrote. The model chooses a"
        " name; it never writes a command or an argument.",
    )


class SshTools(_Rule):
    """Other hosts, through commands you list. Pair it with a forced command on the far side."""

    hosts: dict[str, SshHost] = Field(
        default_factory=dict,
        description="Name the model uses, to the host and the commands it may run there.",
    )
    identity_file: str = Field(min_length=1, description="The private key Paeon connects with.")
    known_hosts_file: str = Field(
        min_length=1, description="Host keys are checked strictly against this file."
    )
    timeout_seconds: float = Field(
        default=20, gt=0, le=120, description="How long one command may run."
    )

    @model_validator(mode="after")
    def _names(self) -> SshTools:
        for host, entry in self.hosts.items():
            for name in (host, *entry.commands):
                if re.fullmatch(_NAME, name) is None:
                    raise ValueError(f"{name!r} is not a plain name")
        return self


class InvestigationTools(_Rule):
    openobserve: OpenObserveTools | None = Field(
        default=None, description="Log search and PromQL in OpenObserve."
    )
    docker: DockerTools | None = Field(
        default=None, description="Container list, inspect and logs."
    )
    knowledge: KnowledgeTools = Field(
        default_factory=KnowledgeTools, description="Full-text search in your runbooks and notes."
    )
    uptime_kuma: UptimeKumaTools | None = Field(
        default=None, description="The state of every monitor."
    )
    git: GitTools | None = Field(default=None, description="Recent commits of working copies.")
    ssh: SshTools | None = Field(default=None, description="Prepared commands on other hosts.")


TriageVerdict = Literal["noise", "transient", "known_issue", "real"]


class Investigation(_Rule):
    enabled: bool = Field(default=False, description="Switches the stage on.")
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
    settle_seconds: float = Field(
        default=60,
        ge=0,
        description="Wait this long after the incident opened, so that related alerts join it.",
    )
    max_tool_calls: int = Field(
        default=25, ge=1, le=200, description="Tool calls one investigation may make."
    )
    max_seconds: float = Field(
        default=600, gt=0, description="How long one investigation may last."
    )
    max_result_chars: int = Field(
        default=12000,
        ge=500,
        description="A tool result longer than this is cut before the model reads it.",
    )
    max_per_hour: int = Field(default=6, ge=1, description="Storm guard.")
    budget_per_incident: float | None = Field(
        default=None,
        ge=0,
        description="What one investigation may spend, in the currency of `price`.",
    )
    daily_budget: float | None = Field(
        default=None, ge=0, description="Counts every model call of the day, all stages."
    )
    tools: InvestigationTools = Field(
        default_factory=InvestigationTools,
        description="What the investigator may read. Each tool is off until it is configured.",
    )
    ntfy: Ntfy | None = Field(
        default=None, description="Where the analysis is sent. Short for one ntfy channel."
    )
    notify: tuple[str, ...] = _NOTIFY


class PostmortemGit(_Rule):
    """Commit each draft in the working copy that holds `postmortems.dir`."""

    commit: bool = Field(default=True, description="Commit each draft.")
    push: bool = Field(
        default=False, description="Push after the commit, to the branch's own upstream."
    )
    author_name: str = Field(
        default="Paeon", min_length=1, max_length=100, description="The commit's author."
    )
    author_email: str = Field(
        default="paeon@localhost",
        min_length=3,
        max_length=200,
        description="The commit's author address.",
    )
    identity_file: str | None = Field(
        default=None, description="A deploy key for the push, if the remote is reached over SSH."
    )
    known_hosts_file: str | None = Field(
        default=None, description="Host keys for the push over SSH, checked strictly."
    )


class Postmortems(_Rule):
    enabled: bool = Field(default=False, description="Switches the stage on.")
    dir: str = Field(
        default="postmortems", min_length=1, description="The directory drafts are written to."
    )
    git: PostmortemGit | None = Field(
        default=None,
        description="Commit and push each draft. The directory must be inside a git working copy.",
    )
    model: str = Field(default="postmortem", description="An alias from `models`.")
    daily_budget: float | None = Field(
        default=None,
        ge=0,
        description="Counts every model call of the day, all stages. Spent: the draft is"
        " written without the model's prose.",
    )


class ProxySecret(_Rule):
    """A header only your proxy can send: its value is a secret the proxy adds to each request."""

    header: str = Field(
        default="X-Paeon-Proxy",
        min_length=1,
        pattern=r"^[A-Za-z0-9-]+$",
        description="The header your proxy adds.",
    )
    value_env: str = Field(
        default="PAEON_UI_PROXY_SECRET",
        min_length=1,
        description="Name of the environment variable (or NAME_FILE) with the value, 24"
        " characters or more.",
    )


class Roles(_Rule):
    """Who may do what in the console, from the groups your proxy names for each request.

    Someone in no listed group can read, and nothing else."""

    header: str = Field(
        default="X-authentik-groups",
        min_length=1,
        pattern=r"^[A-Za-z0-9-]+$",
        description="The header your proxy fills with the groups of whoever is signed in.",
    )
    separator: str = Field(
        default="|",
        min_length=1,
        max_length=1,
        description="What separates two groups in that header: `|` for Authentik, `,` for"
        " Authelia's Remote-Groups.",
    )
    admin: tuple[str, ...] = Field(
        default=(),
        description="Groups that may change what the console can change (`ui.settings`), and"
        " do what an operator does.",
    )
    operator: tuple[str, ...] = Field(
        default=(),
        description="Groups that may say whether triage was right and stop automation.",
    )


class Ui(_Rule):
    """The console under `/ui`. Your reverse proxy protects it, or it asks for a password
    itself (`auth: local`)."""

    enabled: bool = Field(default=False, description="Serves the console under `/ui`.")
    auth: Literal["proxy", "local"] = Field(
        default="proxy",
        description="`proxy`: the console has no login, and a reverse proxy that authenticates"
        " stands in front of it. `local`: the console asks for one password itself"
        " (PAEON_UI_ADMIN_PASSWORD), for a deployment with no such proxy; whoever signs in is"
        " the admin. Needs PAEON_SECRET_KEY.",
    )
    settings: bool = Field(
        default=False,
        description="Lets an admin add, change, test and remove notification channels and"
        " sources in the console. They are kept in the database, next to those of this file,"
        " which the console never changes. Needs PAEON_SECRET_KEY, and `auth: local` or"
        " `roles`. Off: what was added in the console is ignored, and this file is all there"
        " is.",
    )
    roles: Roles | None = Field(
        default=None,
        description="With `auth: proxy`: the groups that are admins and operators. Needs"
        " `proxy_secret`, since a header anyone can send proves nothing. Without `roles`,"
        " everyone the proxy lets in is an operator and nobody is an admin.",
    )
    require_header: str | None = Field(
        default=None,
        min_length=1,
        description="A header your proxy sets after authentication (for example"
        " X-authentik-username). A request without it is refused: a second net against a route"
        " that bypasses the proxy, not a login: whoever reaches the port can send the header.",
    )
    proxy_secret: ProxySecret | None = Field(
        default=None,
        description="A header and a secret value your proxy adds (in Traefik, a `headers`"
        " middleware with `customRequestHeaders`). A request without that exact value is"
        " refused: what reaches Paeon's port without passing the proxy cannot open the console.",
    )
    public_url: str | None = Field(
        default=None,
        pattern=r"^https?://[^\s]+$",
        description="Where you reach the console (https://paeon.example.org). Notifications"
        " link to the incident page there.",
    )

    @model_validator(mode="after")
    def _who_may_change(self) -> Ui:
        if self.roles is not None and self.auth == "local":
            raise ValueError("roles are read from a proxy: they do not go with `auth: local`")
        if self.roles is not None and self.proxy_secret is None:
            raise ValueError(
                "roles needs `proxy_secret`: without it anyone who reaches the port can"
                " name their own groups"
            )
        if self.settings and self.auth == "proxy" and (self.roles is None or not self.roles.admin):
            raise ValueError(
                "settings needs an admin: set `auth: local`, or list the admin groups under `roles`"
            )
        return self


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
    service_name: str = Field(
        default="paeon",
        min_length=1,
        max_length=100,
        description="The `service.name` the traces carry.",
    )
    interval_seconds: float = Field(
        default=30, ge=5, le=3600, description="How often finished spans are sent."
    )


Level = Literal["observe", "suggest", "approve", "auto"]
LEVELS: tuple[Level, ...] = ("observe", "suggest", "approve", "auto")


class Verify(_Rule):
    """How the executor decides that an action worked. Written by you, never by a model."""

    wait_seconds: float = Field(
        default=15,
        ge=0,
        le=600,
        description="Wait this long after the action before the first check.",
    )
    timeout_seconds: float = Field(
        default=120,
        gt=0,
        le=3600,
        description="How long the checks may go on before the action is said to have failed.",
    )
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
    verify: Verify = Field(
        default_factory=Verify, description="How the executor decides that the action worked."
    )

    def allows(self, target: str) -> bool:
        if any(fnmatch.fnmatchcase(target, pattern) for pattern in self.deny):
            return False
        return any(fnmatch.fnmatchcase(target, pattern) for pattern in self.targets)


class CoolifyResource(_Rule):
    uuid: str = Field(pattern=r"^[A-Za-z0-9]{6,64}$", description="The resource's UUID in Coolify.")
    kind: Literal["application", "service"] = Field(
        default="application", description="What it is in Coolify."
    )


class Coolify(_Rule):
    """Executor only: the Coolify resources `coolify_restart` may restart, by a name of yours."""

    url: str = Field(pattern=r"^https?://[^\s]+$", description="For example http://coolify:8000.")
    token_env: str = Field(min_length=1, description="An API token that can deploy.")
    resources: dict[str, CoolifyResource] = Field(
        default_factory=dict,
        description="Name of yours, to the Coolify resource it stands for. A plan names the"
        " resource by this name.",
    )


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
    api_url: str = Field(
        default="https://api.github.com",
        pattern=r"^https://[^\s]+$",
        description="Change it only for GitHub Enterprise.",
    )


class Repository(_Rule):
    """Executor only: a clone of the deployment repository that belongs to Paeon.

    Paeon checks out branches in it and never merges anything: a change is a branch pushed
    with the key you name and, with `github`, a pull request. Merging it is your approval.
    """

    path: str = Field(min_length=1, description="A working copy nothing else uses.")
    remote: str = Field(
        default="origin",
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="The remote branches are fetched from and pushed to.",
    )
    base: str = Field(
        default="main",
        pattern=r"^[A-Za-z0-9_./-]+$",
        description="The branch every change starts from.",
    )
    files: dict[str, RepoFile] = Field(
        default_factory=dict, description="Name of yours, to a file in which values may be changed."
    )
    github: GitHub | None = Field(
        default=None,
        description="Open a pull request for each change. Without it the branch is only pushed.",
    )
    identity_file: str | None = Field(
        default=None, description="A deploy key for fetch and push over SSH."
    )
    known_hosts_file: str | None = Field(
        default=None, description="Host keys for SSH, checked strictly."
    )
    author_name: str = Field(
        default="Paeon", min_length=1, max_length=100, description="The commit's author."
    )
    author_email: str = Field(
        default="paeon@localhost",
        min_length=3,
        max_length=200,
        description="The commit's author address.",
    )


class Policy(_Rule):
    """The autonomy level of one (alert class, action, target). The first match wins."""

    alert: Regex = Field(default=".*", description="Alert title, regular expression, full match.")
    action: str = Field(default="*", description="A catalog action, or * for all.")
    target: str = Field(default="*", description="Container name pattern (fnmatch).")
    level: Level = Field(
        description="`observe`: record only. `suggest`: say what would be done. `approve`:"
        " ask, and run on your Go. `auto`: run, and tell you after."
    )


class Autonomy(_Rule):
    promote_after: int = Field(
        default=5, ge=1, description="Approved and verified runs before `auto` is proposed."
    )
    window_days: int = Field(
        default=90, ge=1, description="The period in which those runs are counted."
    )
    min_confidence: float = Field(
        default=0.8, ge=0, le=1, description="Under it, a plan never runs without approval."
    )


class RunbookStep(_Rule):
    action: str = Field(min_length=1, description="A catalog action.")
    target: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
        description="The container, or Coolify resource, it is applied to.",
    )


class Runbook(_Rule):
    """A known signature and its fix: a hit skips both models."""

    name: str = Field(
        min_length=1, max_length=100, description="A name of yours, recorded with the plan."
    )
    match: Matcher = Field(description="The alert this runbook answers.")
    steps: tuple[RunbookStep, ...] = Field(
        min_length=1,
        max_length=10,
        description="The actions, in order. Each must be allowed by the catalog.",
    )
    mode: Literal["escalate", "sequence"] = Field(
        default="escalate",
        description="escalate: stop at the first step that fixes it. sequence: run them all.",
    )


class Actions(_Rule):
    """Stage 3 and 4: the catalog, who may approve what, and where the executor is.

    The same file is read by the core and by the executor (`paeon executor`). The executor
    trusts only its own copy: mount it read-only there, from git.
    """

    enabled: bool = Field(
        default=False,
        description="Switches plans on. Without it nothing is proposed to the executor.",
    )
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
    approval_minutes: float = Field(
        default=30,
        gt=0,
        le=24 * 60,
        description="How long a Go link works. After it the plan expires and nothing runs.",
    )
    console_approval: bool = Field(
        default=False,
        description="Lets an operator of the console say Go or No-go to a plan that waits for"
        " one. Read by the executor from its own copy of this file, never the core's: with"
        " it on, whoever holds the core's executor token can approve a plan that is already"
        " waiting (still within the catalog and the policies). Off: Go is a signed link only.",
    )
    buttons: Literal["one_tap", "page"] = Field(
        default="one_tap",
        description="What a button in a notification does. `one_tap`: the phone sends the"
        " link in the background, and the only answer is the next notification. `page`: it"
        " opens the executor's page, which says what the link does, asks to confirm, and"
        " then shows what happened. Two taps, and an answer that needs no notification.",
    )
    settle_seconds: float = Field(
        default=30, ge=0, description="A runbook waits this long: the alert may clear by itself."
    )
    max_per_hour: int = Field(default=6, ge=1, description="Plans executed per hour, at most.")
    default_level: Level = Field(
        default="suggest", description="The level of a plan no policy matches."
    )
    ntfy: Ntfy | None = Field(
        default=None, description="The topic for approvals. Give it a token of its own."
    )
    notify: tuple[str, ...] = Field(
        default=(),
        description="Names from `notifiers`: where approvals and results are sent. Every"
        " channel listed receives the Go link: list only what you alone read.",
    )
    catalog: dict[str, CatalogEntry] = Field(
        default_factory=dict,
        description="The actions that exist here, each for the targets you name. An action"
        " that is not listed cannot be proposed.",
    )
    policies: tuple[Policy, ...] = Field(
        default=(),
        description="Who decides: the level of each (alert, action, target). The first match wins.",
    )
    autonomy: Autonomy = Field(
        default_factory=Autonomy, description="When `auto` is earned, and when it is not trusted."
    )
    runbooks: tuple[Runbook, ...] = Field(
        default=(), description="Known alerts and their fix, run without a model."
    )
    coolify: Coolify | None = Field(
        default=None, description="Executor only: the Coolify instance `coolify_restart` uses."
    )
    repository: Repository | None = Field(
        default=None,
        description="Executor only: the deployment repository `config_change` opens pull"
        " requests on.",
    )

    @property
    def one_tap(self) -> bool:
        """Whether a button may send its link in the background, where the service can."""
        return self.buttons == "one_tap"

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
        return self.policy_for(alert, action, target)[0]

    def policy_for(self, alert: str, action: str, target: str) -> tuple[Level, Policy | None]:
        """The level for a step, and the policy line that gave it (None: the default)."""
        for policy in self.policies:
            if (
                re.fullmatch(policy.alert, alert) is not None
                and policy.action in ("*", action)
                and fnmatch.fnmatchcase(target, policy.target)
            ):
                return policy.level, policy
        return self.default_level, None

    def runbook_for(self, alert: Alert) -> Runbook | None:
        if not self.enabled:
            return None
        return next((book for book in self.runbooks if book.match.matches(alert)), None)


# --- sources --------------------------------------------------------------------------------

# No `user:password@`: a secret goes in the environment, and a client would send it as it is.
_URL = r"^https?://[^\s/@]+(/[^\s]*)?$"
_PLAIN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"


class _Source(_Rule):
    """One monitoring service: where alerts come from, and what the investigator may read."""

    user: str | None = Field(
        default=None,
        min_length=1,
        description="User for basic authentication, with `password_env`.",
    )
    password_env: str | None = Field(
        default=None, min_length=1, description="Variable with the password of `user`."
    )
    token_env: str | None = Field(
        default=None,
        min_length=1,
        description="Variable with a token, sent as `Authorization: Bearer`.",
    )

    @model_validator(mode="after")
    def _one_way_in(self) -> Self:
        if (self.user is None) != (self.password_env is None):
            raise ValueError("`user` and `password_env` go together")
        if self.user is not None and self.token_env is not None:
            raise ValueError("`user` and `token_env` are two ways to authenticate: keep one")
        return self

    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        """The secrets this source reads: key, environment variable, whether it is required."""
        found: dict[str, tuple[str, bool]] = {}
        if self.password_env is not None:
            found["password"] = (self.password_env, True)
        if self.token_env is not None:
            found["token"] = (self.token_env, True)
        return found


_HEADERS: Any = Field(
    default_factory=dict,
    description="Headers added to every request, for a tenant: `X-Scope-OrgID` on Mimir and"
    " Loki, `THANOS-TENANT`, `AccountID` on VictoriaLogs. Not for secrets.",
)
_SERVER = "The server, as Paeon reaches it. Without it, the source only receives alerts."


def _plain_names(names: Any, what: str) -> None:
    for name in names:
        if re.fullmatch(_PLAIN, name) is None:
            raise ValueError(f"{what}: {name!r} is not a plain name")


class PrometheusSource(_Source):
    """A Prometheus HTTP API, read with PromQL: Prometheus itself, VictoriaMetrics, Mimir or
    Thanos. Its alerts arrive through Alertmanager."""

    type: Literal["prometheus"]
    url: str = Field(
        pattern=_URL,
        description="The server, with its path prefix if it has one: `http://prometheus:9090`,"
        " `http://mimir:8080/prometheus`.",
    )
    headers: dict[str, str] = _HEADERS
    metrics: tuple[str, ...] = Field(
        default=(),
        description="Metric name patterns (fnmatch) that PromQL queries may read. Empty: none.",
    )
    rules: bool = Field(
        default=True, description="Offer the alerting rules, with their query and their state."
    )
    targets: bool = Field(
        default=True, description="Offer the health of the scrape targets, without their URLs."
    )


class AlertmanagerSource(_Source):
    """An Alertmanager: its webhook brings alerts in, and its API says what is silenced."""

    type: Literal["alertmanager"]
    url: str | None = Field(default=None, pattern=_URL, description=_SERVER)


class GrafanaSource(_Source):
    """Grafana's alerting: a webhook contact point brings alerts in, and a service account
    reads the annotations and the alert rules."""

    type: Literal["grafana"]
    url: str | None = Field(default=None, pattern=_URL, description=_SERVER)
    annotations: bool = Field(
        default=True, description="Offer the annotations: alert state changes and deploy marks."
    )
    rules: bool = Field(
        default=True,
        description="Offer the alert rules. The service account needs the right to read them.",
    )


class LokiSource(_Source):
    """Logs in Grafana Loki. The model chooses a stream by its name and gives words to look
    for; it never writes a selector."""

    type: Literal["loki"]
    url: str = Field(pattern=_URL, description="The server, for example `http://loki:3100`.")
    headers: dict[str, str] = _HEADERS
    streams: dict[str, str] = Field(
        default_factory=dict,
        description="Name the model uses, to the LogQL stream selector you wrote, for example"
        ' `{container="api"}`. Nothing else is readable.',
    )
    max_lines: int = Field(default=100, ge=1, le=1000, description="Most lines one search returns.")

    @model_validator(mode="after")
    def _selectors(self) -> LokiSource:
        _plain_names(self.streams, "streams")
        for name, selector in self.streams.items():
            if re.fullmatch(r"\{.+\}", selector.strip()) is None:
                raise ValueError(f"streams.{name}: a selector is written between braces")
        return self


class VictoriaLogsSource(_Source):
    """Logs in VictoriaLogs. The model chooses a stream by its name and gives words to look
    for; it never writes a filter."""

    type: Literal["victorialogs"]
    url: str = Field(pattern=_URL, description="The server, for example `http://vlogs:9428`.")
    headers: dict[str, str] = _HEADERS
    streams: dict[str, str] = Field(
        default_factory=dict,
        description="Name the model uses, to the LogsQL filter you wrote, for example"
        ' `{app="api"}`. Nothing else is readable.',
    )
    max_lines: int = Field(default=100, ge=1, le=1000, description="Most lines one search returns.")

    @model_validator(mode="after")
    def _filters(self) -> VictoriaLogsSource:
        _plain_names(self.streams, "streams")
        for name, query in self.streams.items():
            if not query.strip() or "|" in query:
                raise ValueError(f"streams.{name}: one filter, without a pipe")
        return self


class OpenObserveSource(_Source):
    """An OpenObserve organization, in the rules file instead of the `PAEON_OPENOBSERVE_*`
    variables: its alerts are polled, and its logs and metrics are read."""

    type: Literal["openobserve"]
    url: str = Field(pattern=_URL, description="The server, for example `http://openobserve:5080`.")
    org: str = Field(default="default", min_length=1, description="The organization.")
    alerts: bool = Field(
        default=True, description="Poll the `triggers` stream for alert evaluations."
    )
    poll_seconds: float = Field(default=60, ge=5, description="How often the alerts are polled.")
    stale_seconds: float = Field(
        default=900, ge=60, description="Without data for this long, the source is reported stale."
    )
    streams: tuple[str, ...] = Field(
        default=(), description="Log streams that may be searched. Nothing else is readable."
    )
    metrics: tuple[str, ...] = Field(
        default=(),
        description="Metric name patterns (fnmatch) that PromQL queries may read. Empty: none.",
    )
    max_rows: int = Field(default=100, ge=1, le=1000, description="Most rows one search returns.")

    @model_validator(mode="after")
    def _account(self) -> OpenObserveSource:
        if self.user is None:
            raise ValueError("`user` and `password_env` are required: OpenObserve has no open API")
        return self


class UptimeKumaSource(_Source):
    """uptime-kuma: its Webhook notification brings alerts in, and `/metrics` says the state
    of every monitor."""

    type: Literal["uptime_kuma"]
    url: str | None = Field(default=None, pattern=_URL, description=_SERVER)
    api_key_env: str | None = Field(
        default=None, min_length=1, description="Variable with an API key, for `/metrics`."
    )

    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        found = super().secret_envs()
        if self.api_key_env is not None:
            found["api_key"] = (self.api_key_env, True)
        return found


class GatusSource(_Source):
    """Gatus: a custom alert brings state changes in, and its API says how each endpoint is."""

    type: Literal["gatus"]
    url: str | None = Field(default=None, pattern=_URL, description=_SERVER)


class HealthchecksSource(_Source):
    """Healthchecks (healthchecks.io or your own): a webhook integration brings the checks that
    go down and up, and a read-only API key reads their state and history."""

    type: Literal["healthchecks"]
    url: str | None = Field(
        default=None, pattern=_URL, description=_SERVER + " `https://healthchecks.io` if hosted."
    )
    api_key_env: str | None = Field(
        default=None, min_length=1, description="Variable with the project's read-only API key."
    )

    def secret_envs(self) -> dict[str, tuple[str, bool]]:
        found = super().secret_envs()
        if self.api_key_env is not None:
            found["api_key"] = (self.api_key_env, True)
        return found


class NetdataSource(_Source):
    """A Netdata agent: its custom notification brings alert transitions in, and its API reads
    the active alerts and the charts you list."""

    type: Literal["netdata"]
    url: str | None = Field(default=None, pattern=_URL, description=_SERVER)
    charts: tuple[str, ...] = Field(
        default=(),
        description="Chart id patterns (fnmatch) whose data may be read, for example"
        " `system.*`, `disk_space.*`. Empty: none.",
    )


class ZabbixSource(_Source):
    """Zabbix: a webhook media type brings problems and recoveries in, and an API token of a
    read-only user reads problems, items and their history."""

    type: Literal["zabbix"]
    url: str | None = Field(
        default=None,
        pattern=_URL,
        description=_SERVER + " The frontend's address, without `api_jsonrpc.php`.",
    )
    hosts: tuple[str, ...] = Field(
        default=(),
        description="Host name patterns (fnmatch) whose items and history may be read."
        " Empty: none. Problems are listed for every host the token can see.",
    )
    max_values: int = Field(
        default=200, ge=1, le=2000, description="Most history values one request returns."
    )


class BeszelSource(_Source):
    """A Beszel hub, polled: the alerts that are triggered and the systems that are down. Its
    notifications carry a sentence only, so nothing is pushed."""

    type: Literal["beszel"]
    url: str = Field(pattern=_URL, description="The hub, for example `http://beszel:8090`.")
    alerts: bool = Field(
        default=True, description="Poll the triggered alerts and the status of the systems."
    )
    poll_seconds: float = Field(default=60, ge=10, description="How often the hub is polled.")
    stale_seconds: float = Field(
        default=900, ge=60, description="Without an answer for this long, the source is stale."
    )

    @model_validator(mode="after")
    def _account(self) -> BeszelSource:
        if self.user is None:
            raise ValueError("`user` (the account's e-mail) and `password_env` are required")
        if self.token_env is not None:
            raise ValueError("Beszel signs in with `user` and `password_env`, not with a token")
        return self


# One entry of `sources`, told apart by its `type`.
Source = Annotated[
    PrometheusSource
    | AlertmanagerSource
    | GrafanaSource
    | LokiSource
    | VictoriaLogsSource
    | OpenObserveSource
    | UptimeKumaSource
    | GatusSource
    | HealthchecksSource
    | NetdataSource
    | ZabbixSource
    | BeszelSource,
    Field(discriminator="type"),
]
# A name that is an address of its own (`/ingest/change`) or Paeon's own source.
_RESERVED_SOURCES = frozenset({"change", "paeon"})
# The names of the sources that need no entry: each keeps its type.
BUILT_IN_SOURCES = {
    "alertmanager": "alertmanager",
    "uptime-kuma": "uptime_kuma",
    "openobserve": "openobserve",
}


class Rules(_Rule):
    correlation: Correlation = Field(
        default_factory=Correlation, description="How alerts become incidents."
    )
    maintenance: tuple[MaintenanceWindow, ...] = Field(
        default=(), description="Weekly windows in which alerts are expected."
    )
    notifiers: dict[str, Channel] = Field(
        default_factory=dict,
        description="Named notification channels. A stage sends to those its `notify` lists.",
    )
    sources: dict[str, Source] = Field(
        default_factory=dict,
        description="Named monitoring services: where alerts come from, and what the"
        " investigator may read in each.",
    )
    models: dict[str, ModelAlias] = Field(
        default_factory=dict, description="Model aliases: a stage names an alias, never a model."
    )
    triage: Triage = Field(
        default_factory=Triage, description="Stage 1: a cheap first look at each incident."
    )
    digest: Digest = Field(default_factory=Digest, description="The daily summary.")
    investigation: Investigation = Field(
        default_factory=Investigation, description="Stage 2: a strong model with read-only tools."
    )
    postmortems: Postmortems = Field(
        default_factory=Postmortems, description="Drafts written once an incident stays resolved."
    )
    ui: Ui = Field(default_factory=Ui, description="The console.")
    actions: Actions = Field(
        default_factory=Actions, description="Stages 3 and 4: plans, approval, execution."
    )
    telemetry: Telemetry | None = Field(default=None, description="Traces of Paeon's own work.")

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

    def unpriced_budgets(self) -> list[str]:
        """The budgets that can never be reached: a budget is counted from prices, and the
        stage's model has none. Worth a warning at start, since nothing else would say it."""
        investigation = self.investigation
        stages: tuple[tuple[str, bool, tuple[str | None, ...], bool], ...] = (
            (
                "triage",
                self.triage.enabled,
                (self.triage.model,),
                self.triage.daily_budget is not None,
            ),
            (
                "investigation",
                investigation.enabled,
                (investigation.model, investigation.fallback_model),
                investigation.daily_budget is not None
                or investigation.budget_per_incident is not None,
            ),
            (
                "postmortems",
                self.postmortems.enabled,
                (self.postmortems.model,),
                self.postmortems.daily_budget is not None,
            ),
        )
        found: list[str] = []
        for name, enabled, aliases, budgeted in stages:
            for alias in aliases if enabled and budgeted else ():
                model = self.models.get(alias or "")
                if model is None:
                    continue
                if model.price.input_per_mtok == 0 and model.price.output_per_mtok == 0:
                    found.append(
                        f"{name} has a budget, and models.{alias}.price is not set: the budget"
                        " will never be reached"
                    )
        return found

    LEGACY: ClassVar[str] = "ntfy"  # the name of a stage's own `ntfy:` block

    @model_validator(mode="after")
    def _notifiers_exist(self) -> Rules:
        for name in self.notifiers:
            if not re.fullmatch(_CHANNEL_NAME, name) or name == self.LEGACY:
                raise ValueError(
                    f"notifiers: {name!r} is not a channel name (lower-case letters, digits,"
                    f" - and _, 32 at most, and not {self.LEGACY!r})"
                )
        for stage in ("digest", "investigation", "actions"):
            listed = getattr(self, stage).notify
            for name in listed:
                if name not in self.notifiers:
                    raise ValueError(f"{stage}.notify: {name!r} is not defined under `notifiers`")
            if len(set(listed)) != len(listed):
                raise ValueError(f"{stage}.notify names a channel twice")
        return self

    @model_validator(mode="after")
    def _sources_named(self) -> Rules:
        for name, source in self.sources.items():
            if not re.fullmatch(_CHANNEL_NAME, name) or name in _RESERVED_SOURCES:
                raise ValueError(
                    f"sources: {name!r} is not a source name (lower-case letters, digits,"
                    f" - and _, 32 at most, and not {sorted(_RESERVED_SOURCES)})"
                )
            if name.replace("-", "_") in {o.replace("-", "_") for o in self.sources if o != name}:
                raise ValueError(f"sources: {name!r} differs from another name only by - and _")
            kept = BUILT_IN_SOURCES.get(name)
            if kept is not None and source.type != kept:
                raise ValueError(f"sources: the name {name!r} is kept for `type: {kept}`")
        return self

    def channels(self, stage: str) -> list[tuple[str, Channel]]:
        """The channels a stage sends to, by name: its own `ntfy:` block first, as `ntfy`."""
        config = getattr(self, stage)
        found: list[tuple[str, Channel]] = []
        if config.ntfy is not None:
            found.append((self.LEGACY, NtfyChannel(type="ntfy", **config.ntfy.model_dump())))
        found += [(name, self.notifiers[name]) for name in config.notify]
        return found

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
