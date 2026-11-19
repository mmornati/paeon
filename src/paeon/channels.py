"""Channels added in the console, next to those of the rules file.

The rules file stays what it was: read once, never written. What an admin adds in the console
is kept in the database (`console_channels`), its secrets sealed (`paeon.vault`), and laid over
the file each time it changes: the stages read their channels from a roster that is swapped in
place, so nothing restarts.

Three limits hold it in:

- A name the file defines belongs to the file. The console cannot take it, and a stored channel
  whose name the file later claims is set aside, not sent to.
- A console channel carries the digest and the analysis, never an approval. Approvals are sent
  by the executor, from its own copy of the rules file, next to the key that signs them.
- Nothing here is read unless `ui.settings` is on.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx2 as httpx
from pydantic import TypeAdapter, ValidationError

from paeon import notify
from paeon.notify import Delivery, Message, Notifier, Roster
from paeon.rules import _CHANNEL_NAME, Channel, Rules, RulesError, parse_rules
from paeon.store import ConsoleChannel, Store
from paeon.vault import Vault, VaultError

log = logging.getLogger(__name__)

# What a console channel may carry, and what the console calls it.
STAGES: dict[str, str] = {"digest": "digest", "investigation": "analysis"}
# What stands in a `*_env` key of a console channel: its secret is in the vault, not the
# environment.
STORED = "(kept by the console)"

# The settings that decide where a channel's secret is sent, and how. When one changes, the
# stored secrets are not reused: whoever moves a channel must know its secrets, or the edit
# would be a way to have a secret one never saw delivered to an address of one's choice.
DESTINATION = frozenset({"url", "api_url", "homeserver", "host", "port", "security"})

Kind = Literal["text", "url", "number", "select", "list", "lines"]


@dataclass(frozen=True)
class FieldSpec:
    """One input of a service's form. For a secret, `key` is the secret's own name (`token`
    for the model's `token_env`)."""

    key: str
    label: str
    help: str = ""
    secret: bool = False
    required: bool = True
    kind: Kind = "text"
    options: tuple[str, ...] = ()
    placeholder: str = ""
    default: str = ""

    @property
    def model_key(self) -> str:
        return f"{self.key}_env" if self.secret else self.key


@dataclass(frozen=True)
class Service:
    type: str
    label: str
    summary: str  # what it is, and how a link or a button reaches you there
    fields: tuple[FieldSpec, ...]

    def field(self, key: str) -> FieldSpec | None:
        return next((one for one in self.fields if one.key == key), None)


_POSTABLE = "Whoever has this address can post to it, so it is kept as a secret."

SERVICES: dict[str, Service] = {
    service.type: service
    for service in (
        Service(
            "ntfy",
            "ntfy",
            "A topic on an ntfy server. Push notifications on your phone, with buttons.",
            (
                FieldSpec("url", "Server", kind="url", placeholder="https://ntfy.sh"),
                FieldSpec("topic", "Topic", placeholder="paeon"),
                FieldSpec(
                    "token",
                    "Access token",
                    "Leave it empty for a topic anyone may publish to.",
                    secret=True,
                    required=False,
                ),
            ),
        ),
        Service(
            "telegram",
            "Telegram",
            "A chat with a bot of yours. Links appear as buttons under the message.",
            (
                FieldSpec("token", "Bot token", "The token BotFather gave the bot.", secret=True),
                FieldSpec(
                    "chat_id",
                    "Chat id",
                    "The chat, group or channel the bot posts to.",
                    placeholder="-1001234567890",
                ),
                FieldSpec(
                    "api_url",
                    "Bot API server",
                    "Change it only for a Bot API server of your own.",
                    required=False,
                    kind="url",
                    default="https://api.telegram.org",
                ),
            ),
        ),
        Service(
            "discord",
            "Discord",
            "A channel's webhook. Links appear as buttons under the message.",
            (
                FieldSpec("webhook", "Webhook URL", _POSTABLE, secret=True, kind="url"),
                FieldSpec(
                    "username",
                    "Post as",
                    "The name shown on the message. Empty: the webhook's own.",
                    required=False,
                ),
            ),
        ),
        Service(
            "slack",
            "Slack",
            "An incoming webhook. Links are part of the text.",
            (FieldSpec("webhook", "Webhook URL", _POSTABLE, secret=True, kind="url"),),
        ),
        Service(
            "teams",
            "Microsoft Teams",
            "A Workflows webhook. Buttons open the link.",
            (FieldSpec("webhook", "Workflow URL", _POSTABLE, secret=True, kind="url"),),
        ),
        Service(
            "email",
            "E-mail",
            "Mail through an SMTP server. Links are in the body.",
            (
                FieldSpec("host", "SMTP server", placeholder="smtp.example.org"),
                FieldSpec(
                    "port", "Port", "587 with STARTTLS, 465 with TLS.", kind="number", default="587"
                ),
                FieldSpec(
                    "security",
                    "Encryption",
                    "Use none only for a relay on your own network.",
                    kind="select",
                    options=("starttls", "tls", "none"),
                    default="starttls",
                ),
                FieldSpec(
                    "username",
                    "Login",
                    "If the server asks for one. It goes with the password.",
                    required=False,
                ),
                FieldSpec("password", "Password", secret=True, required=False),
                FieldSpec("sender", "From", "A bare address.", placeholder="paeon@example.org"),
                FieldSpec(
                    "to",
                    "To",
                    "One address or several, separated by commas.",
                    kind="list",
                    placeholder="you@example.org",
                ),
            ),
        ),
        Service(
            "gotify",
            "Gotify",
            "An application on a Gotify server. Links are lines of the message.",
            (
                FieldSpec(
                    "url",
                    "Server",
                    "Without /message at the end.",
                    kind="url",
                    placeholder="https://gotify.example.org",
                ),
                FieldSpec("token", "Application token", secret=True),
            ),
        ),
        Service(
            "pushover",
            "Pushover",
            "Push notifications through Pushover. Links are in the message.",
            (
                FieldSpec("token", "Application token", secret=True),
                FieldSpec("user", "User or group key", secret=True),
                FieldSpec(
                    "device",
                    "Device",
                    "Limits delivery to one of your devices, by its name.",
                    required=False,
                ),
            ),
        ),
        Service(
            "matrix",
            "Matrix",
            "A room on a Matrix homeserver. Links are in the message.",
            (
                FieldSpec(
                    "homeserver", "Homeserver", kind="url", placeholder="https://matrix.example.org"
                ),
                FieldSpec("room_id", "Room id", placeholder="!abc123:example.org"),
                FieldSpec("token", "Access token", "Of the account that posts.", secret=True),
            ),
        ),
        Service(
            "webhook",
            "Webhook",
            "A POST of the message as JSON, for n8n, Node-RED or a script of yours.",
            (
                FieldSpec(
                    "url",
                    "URL",
                    "It often holds a key, so it is kept as a secret.",
                    secret=True,
                    kind="url",
                ),
                FieldSpec(
                    "authorization",
                    "Authorization header",
                    "The whole value, for example Bearer followed by a token.",
                    secret=True,
                    required=False,
                ),
            ),
        ),
        Service(
            "apprise",
            "Apprise",
            "Any service Apprise knows. Needs the optional install, paeon[apprise].",
            (
                FieldSpec(
                    "urls",
                    "Apprise URLs",
                    "One per line. They hold the services' secrets.",
                    secret=True,
                    kind="lines",
                ),
            ),
        ),
    )
}

_ADAPTER: TypeAdapter[Channel] = TypeAdapter(Channel)


class ChannelError(ValueError):
    """A channel cannot be saved. `problems` says why, by field; `""` is the form as a whole."""

    def __init__(self, problems: Mapping[str, str]) -> None:
        super().__init__("; ".join(f"{key or 'channel'}: {text}" for key, text in problems.items()))
        self.problems = dict(problems)


def read_form(
    service: Service, form: Mapping[str, str]
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """What a submitted form says: the plain settings, the secrets typed, and what is wrong.

    An input left empty is a setting left out; a secret left empty is a secret left as it is."""
    config: dict[str, Any] = {}
    secrets: dict[str, str] = {}
    problems: dict[str, str] = {}
    for spec in service.fields:
        raw = form.get(spec.key, "").strip()
        if raw and spec.kind == "url" and not re.fullmatch(r"https?://\S+", raw):
            problems[spec.key] = f"{spec.label} is an address that starts with http:// or https://."
        elif spec.secret:
            if raw:
                secrets[spec.key] = raw
        elif not raw:
            if spec.required and not spec.default:
                problems[spec.key] = f"{spec.label} is needed."
        elif spec.kind == "number":
            if raw.isdecimal():
                config[spec.key] = int(raw)
            else:
                problems[spec.key] = f"{spec.label} is a number."
        elif spec.kind == "list":
            config[spec.key] = [part for part in re.split(r"[,\s]+", raw) if part]
        else:
            config[spec.key] = raw
    return config, secrets, problems


@dataclass(frozen=True)
class Shown:
    """A console channel as the pages show it. Never a secret: only which ones are kept."""

    name: str
    service: Service
    config: dict[str, Any]
    stages: tuple[str, ...]
    kept: frozenset[str]  # the keys of its stored secrets
    broken: str | None  # why it is set aside, when it is
    updated_at: datetime


class Channels:
    def __init__(
        self,
        store: Store,
        rules: Rules,
        vault: Vault,
        *,
        base: Mapping[str, Iterable[Notifier]],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """`base`: the channels the rules file gives each stage, already built."""
        self._store = store
        self._base_rules = rules
        self._vault = vault
        self._base = {stage: tuple(base.get(stage, ())) for stage in STAGES}
        self._transport = transport
        self._lock = threading.Lock()
        self.rosters = {stage: Roster(self._base[stage]) for stage in STAGES}
        self._rules = rules
        self._built: dict[str, Notifier] = {}
        self._broken: dict[str, str] = {}
        self.apply()

    @property
    def rules(self) -> Rules:
        """The rules file with the console's channels laid over it."""
        return self._rules

    def shown(self) -> list[Shown]:
        return [
            Shown(
                name=row.name,
                service=SERVICES[row.type],
                config=row.config,
                stages=row.stages,
                kept=frozenset(row.secrets),
                broken=self._broken.get(row.name),
                updated_at=row.updated_at,
            )
            for row in self._store.console_channels()
            if row.type in SERVICES
        ]

    def find(self, name: str) -> Shown | None:
        return next((one for one in self.shown() if one.name == name), None)

    def from_file(self, name: str) -> bool:
        return name in self._base_rules.notifiers or name == Rules.LEGACY

    # --- changes ------------------------------------------------------------------------------

    def save(
        self,
        name: str,
        service: Service,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
        *,
        stages: Sequence[str],
        forget: Sequence[str] = (),
        creating: bool,
        by: str,
        now: datetime | None = None,
    ) -> None:
        """Add a channel, or change one. `secrets` are the ones typed now; one that is not
        typed stays as it is, unless `forget` names it. Raises ChannelError."""
        problems: dict[str, str] = {}
        with self._lock:
            rows = {row.name: row for row in self._store.console_channels()}
            before = rows.get(name)
            if creating:
                if name == Rules.LEGACY:
                    problems["name"] = (
                        f"The name {name!r} is kept for a stage's own ntfy block in the rules"
                        " file. Choose another."
                    )
                elif not re.fullmatch(_CHANNEL_NAME, name):
                    problems["name"] = (
                        "Use lower-case letters, digits, - and _, 32 at most, starting with a"
                        " letter or a digit."
                    )
                elif name in self._base_rules.notifiers:
                    problems["name"] = "The rules file already defines a channel of this name."
                elif before is not None:
                    problems["name"] = "A channel of this name already exists."
            elif before is None or before.type != service.type:
                raise ChannelError({"": "This channel no longer exists."})
            sealed = dict(before.secrets) if before is not None and not creating else {}
            for key in forget:
                spec = service.field(key)
                if spec is not None and spec.secret and not spec.required:
                    sealed.pop(key, None)
            moved = (
                before is not None
                and not creating
                and any(before.config.get(key) != config.get(key) for key in DESTINATION)
            )
            for key in sealed if moved else ():
                spec = service.field(key)
                if key not in secrets and spec is not None:
                    # No label before a colon here: the page's redaction reads "token: x"
                    # as a secret and masks what follows.
                    problems[key] = (
                        "Type it again. Where it is sent changed, and a stored secret is"
                        " never sent to a new address."
                    )
            for key, value in secrets.items():
                sealed[key] = self._vault.seal(value)
            for spec in service.fields:
                if spec.secret and spec.required and spec.key not in sealed:
                    problems[spec.key] = f"{spec.label} is needed."
            if service.type == "email" and ("username" in config) != ("password" in sealed):
                missing = "username" if "password" in sealed else "password"
                problems[missing] = "Login and password go together: give both, or neither."
            chosen = tuple(stage for stage in STAGES if stage in stages)
            row = ConsoleChannel(
                name=name,
                type=service.type,
                config=dict(config),
                stages=chosen,
                secrets=sealed,
                updated_at=now or datetime.now(UTC),
            )
            if not problems:
                try:
                    _model(row)
                except ValidationError as exc:
                    problems = _explain(exc, service)
            if problems:
                raise ChannelError(problems)
            self._store.save_console_channel(row)
            self._store.append(
                "console.channel.saved",
                {
                    "name": name,
                    "channel": service.type,
                    "carries": [STAGES[stage] for stage in chosen],
                    "secrets": sorted(sealed),
                    "created": creating,
                    "by": by,
                },
                now=now,
            )
            self._apply()

    def remove(self, name: str, *, by: str, now: datetime | None = None) -> bool:
        with self._lock:
            if not self._store.delete_console_channel(name):
                return False
            self._store.append("console.channel.removed", {"name": name, "by": by}, now=now)
            self._apply()
        return True

    def test(self, name: str, *, click: str | None = None) -> Delivery | None:
        """Send one message to a channel and record what became of it. None: no such channel,
        or one that is set aside."""
        notifier = self._built.get(name)
        if notifier is None:
            for stage in STAGES:
                notifier = next((one for one in self._base[stage] if one.name == name), notifier)
        if notifier is None:
            return None
        message = Message(
            title="Paeon test",
            text="This is a test from the Paeon console. This channel works.",
            priority=2,
            tags=("white_check_mark",),
            click=click,
        )
        delivery = notify.deliver([notifier], message)[0]
        kind, payload = delivery.record("test")
        self._store.append(kind, payload)
        return delivery

    def can_test(self, name: str) -> bool:
        return name in self._built or any(
            one.name == name for stage in STAGES for one in self._base[stage]
        )

    # --- laying the console's channels over the file ------------------------------------------

    def apply(self) -> None:
        with self._lock:
            self._apply()

    def _apply(self) -> None:
        built: dict[str, Notifier] = {}
        broken: dict[str, str] = {}
        models: dict[str, Any] = {}
        routes: dict[str, list[str]] = {stage: [] for stage in STAGES}
        for row in self._store.console_channels():
            try:
                if self.from_file(row.name):
                    raise ChannelError({"": "the rules file now defines a channel of this name"})
                model = _model(row)
                secrets = {key: self._vault.open(value) for key, value in row.secrets.items()}
                built[row.name] = notify.build(row.name, model, secrets, transport=self._transport)
            except ChannelError as exc:
                broken[row.name] = exc.problems.get("", str(exc))
            except VaultError:
                broken[row.name] = "its secrets cannot be read with this PAEON_SECRET_KEY"
            except ValidationError:
                broken[row.name] = "its settings are no longer valid for this version of Paeon"
            except Exception as exc:  # a missing optional install, a bug in one adapter
                log.exception("console channel %s cannot be built", row.name)
                broken[row.name] = f"it cannot be set up: {type(exc).__name__}"
            else:
                models[row.name] = model.model_dump(mode="json", exclude_unset=True)
                for stage in row.stages:
                    if stage in routes:
                        routes[stage].append(row.name)
        for name, why in broken.items():
            if self._broken.get(name) != why:
                log.warning("console channel %s is set aside: %s", name, why)
        document = self._base_rules.model_dump(mode="json", exclude_unset=True)
        document["notifiers"] = {**(document.get("notifiers") or {}), **models}
        for stage, names in routes.items():
            if names:
                section = dict(document.get(stage) or {})
                section["notify"] = [*(section.get("notify") or ()), *names]
                document[stage] = section
        try:
            merged = parse_rules(document)
        except RulesError:  # each channel was checked alone: this is a bug, not a setting
            log.exception("console channels cannot be laid over the rules file")
            return
        self._rules = merged
        self._built = built
        self._broken = broken
        for stage in STAGES:
            self.rosters[stage].put([*self._base[stage], *(built[name] for name in routes[stage])])


def _model(row: ConsoleChannel) -> Any:
    """The row as the channel the rules file would hold. Raises ValidationError."""
    document: dict[str, Any] = {"type": row.type, **row.config}
    for key in row.secrets:
        document[f"{key}_env"] = STORED
    return _ADAPTER.validate_python(document)


def _explain(exc: ValidationError, service: Service) -> dict[str, str]:
    """A validation error, by the form's fields."""
    problems: dict[str, str] = {}
    for error in exc.errors():
        key = next((str(part) for part in reversed(error["loc"]) if isinstance(part, str)), "")
        key = key.removesuffix("_env")
        spec = service.field(key)
        text = str(error["msg"]).removeprefix("Value error, ").replace("_env", "")
        text = text.replace("`", "").rstrip(".")
        if spec is None:
            problems.setdefault("", text[:1].upper() + text[1:] + ".")
        else:
            problems.setdefault(spec.key, f"{spec.label}: {text}.")
    return problems
