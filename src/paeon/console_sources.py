"""Sources added in the console, next to those of the rules file.

The same arrangement as `paeon.channels`: the rules file is read once and never written; what
an admin adds is kept in the database (`console_sources`), its secrets sealed, and connected
while Paeon runs. A source that is added starts receiving at `/ingest/<name>`, is polled if its
service is polled, and offers its tools to the next investigation; one that is removed stops.

The limits:

- A name the file defines, or one of the webhooks Paeon always answers, belongs to the file.
- What a source lets an investigation read is its own allow-list, as in the file. Whoever adds
  one decides what a model provider may be shown: that is why only an admin can.
- Changing where a source is read from asks for its secrets again.
- Nothing here is read unless `ui.settings` is on.
"""

from __future__ import annotations

import logging
import re
import threading
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin

import httpx2 as httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from paeon import sources
from paeon.channels import STORED, ChannelError, FieldSpec, Kind, Service
from paeon.rules import (
    _CHANNEL_NAME,
    _RESERVED_SOURCES,
    BUILT_IN_SOURCES,
    Rules,
    RulesError,
    Source,
    parse_rules,
)
from paeon.sources import Connected, Polling, ReadError
from paeon.store import ConsoleChannel, Store
from paeon.vault import Vault, VaultError

if TYPE_CHECKING:
    from paeon.recorder import Recorder
    from paeon.tools import Tool

log = logging.getLogger(__name__)

# The settings that decide where a source's secret is sent.
DESTINATION = frozenset({"url"})

_LABELS = {
    "prometheus": "Prometheus",
    "alertmanager": "Alertmanager",
    "grafana": "Grafana",
    "loki": "Loki",
    "victorialogs": "VictoriaLogs",
    "openobserve": "OpenObserve",
    "uptime_kuma": "uptime-kuma",
    "gatus": "Gatus",
    "healthchecks": "Healthchecks",
    "netdata": "Netdata",
    "zabbix": "Zabbix",
    "beszel": "Beszel",
}
_FIELD_LABELS = {
    "url": "Server",
    "user": "User",
    "password": "Password",
    "token": "Token",
    "api_key": "API key",
    "org": "Organization",
    "headers": "Headers",
    "metrics": "Metrics an investigation may query",
    "streams": "Log streams an investigation may search",
    "charts": "Charts an investigation may read",
    "hosts": "Hosts an investigation may read",
    "rules": "Offer the alert rules",
    "targets": "Offer the scrape targets",
    "annotations": "Offer the annotations",
    "alerts": "Poll its alerts",
    "poll_seconds": "Poll every (seconds)",
    "stale_seconds": "Stale after (seconds)",
    "max_lines": "Most lines per search",
    "max_rows": "Most rows per search",
    "max_values": "Most values per request",
}
# Written so that the page's redaction, which masks what follows words such as
# "authorization" or "bearer", leaves them whole.
_SECRET_HELP = {
    "password": "The password of the user above.",
    "token": "Sent with every request, as a bearer. Use a read-only one.",
    "api_key": "A read-only key.",
}
_HELP = {"user": "If the service asks for a user name and a password."}
_ORDER = ("url", "user", "password", "token", "api_key")


def _kind(annotation: Any, key: str) -> tuple[Kind, bool]:
    """The input for a key of a source's model, and whether it may be left empty."""
    optional = False
    if get_origin(annotation) in (Union, types.UnionType):
        inner = [one for one in get_args(annotation) if one is not type(None)]
        optional = len(inner) < len(get_args(annotation))
        annotation = inner[0]
    if annotation is bool:
        return "bool", True
    if annotation in (int, float):
        return "number", True
    if get_origin(annotation) is tuple:
        return "list", True
    if get_origin(annotation) is dict:
        return "map", True
    return ("url" if key == "url" else "text"), optional


def _service(model: type[BaseModel]) -> Service:
    kind = str(get_args(model.model_fields["type"].annotation)[0])
    specs: list[FieldSpec] = []
    for name, info in model.model_fields.items():
        if name == "type":
            continue
        secret = name.endswith("_env")
        key = name.removesuffix("_env")
        input_kind: Kind
        input_kind, optional = ("text", True) if secret else _kind(info.annotation, key)
        default = info.default if not info.is_required() and info.default is not None else ""
        help_text = (
            _SECRET_HELP[key]
            if secret
            else _HELP.get(key, " ".join((info.description or "").split()))
        )
        specs.append(
            FieldSpec(
                key=key,
                label=_FIELD_LABELS.get(key, key.replace("_", " ").capitalize()),
                help=help_text.replace("`", "").replace("password_env", "a password"),
                secret=secret,
                required=info.is_required() and not optional,
                kind=input_kind,
                default="" if input_kind in ("list", "map") else _plain(default),
            )
        )
    specs.sort(key=lambda spec: _ORDER.index(spec.key) if spec.key in _ORDER else len(_ORDER))
    return Service(kind, _LABELS[kind], " ".join((model.__doc__ or "").split()), tuple(specs))


def _plain(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


SOURCE_SERVICES: dict[str, Service] = {
    service.type: service
    for service in (_service(model) for model in get_args(get_args(Source)[0]))
}
_ADAPTER: TypeAdapter[Source] = TypeAdapter(Source)


@dataclass(frozen=True)
class ShownSource:
    """A console source as the pages show it. Never a secret: only which ones are kept."""

    name: str
    service: Service
    config: dict[str, Any]
    kept: frozenset[str]
    broken: str | None
    updated_at: datetime


class Sources:
    def __init__(
        self,
        store: Store,
        rules: Rules,
        vault: Vault,
        *,
        connected: dict[str, Connected],
        recorder: Recorder | None = None,
        reserved: Sequence[str] = (),
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """`connected`: the sources the app already has, by name. It is the app's own mapping:
        the console's sources are put into it and taken out of it, so that `/ingest/<name>`
        sees them. `reserved`: other names the console may not take."""
        self._store = store
        self._rules = rules
        self._vault = vault
        self._connected = connected
        self._recorder = recorder
        self._transport = transport
        self._file = frozenset(connected) | frozenset(reserved) | frozenset(rules.sources)
        self._lock = threading.Lock()
        self._built: dict[str, tuple[datetime, Connected]] = {}
        self._broken: dict[str, str] = {}
        self._probes: dict[str, tuple[bool, str]] = {}
        self.changed: Callable[[], None] | None = None  # called after each change is applied
        self.apply()

    # --- what the app reads -------------------------------------------------------------------

    def tools(self) -> list[Tool]:
        """The tools of the console's sources, for the investigator."""
        return [tool for _, one in self._built.values() for tool in one.tools]

    def polling(self) -> dict[str, tuple[datetime, Polling]]:
        """The console's sources that are polled, each with the moment it was last saved: a
        poll loop is restarted when that moment changes."""
        return {
            name: (stamp, one.polling)
            for name, (stamp, one) in self._built.items()
            if one.polling is not None
        }

    def origin(self, name: str) -> str:
        """Where a source comes from: `console`, `file`, or `built in` for the webhooks Paeon
        answers with no entry anywhere."""
        if name in self._rules.sources:
            return "file"
        return "built in" if name in self._file else "console"

    def shown(self) -> list[ShownSource]:
        return [
            ShownSource(
                name=row.name,
                service=SOURCE_SERVICES[row.type],
                config=row.config,
                kept=frozenset(row.secrets),
                broken=self._broken.get(row.name),
                updated_at=row.updated_at,
            )
            for row in self._store.console_sources()
            if row.type in SOURCE_SERVICES
        ]

    def find(self, name: str) -> ShownSource | None:
        return next((one for one in self.shown() if one.name == name), None)

    def probed(self, name: str) -> tuple[bool, str] | None:
        """What the last test of this source answered, since Paeon started."""
        return self._probes.get(name)

    # --- changes ------------------------------------------------------------------------------

    def save(
        self,
        name: str,
        service: Service,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
        *,
        forget: Sequence[str] = (),
        creating: bool,
        by: str,
        now: datetime | None = None,
    ) -> None:
        """Add a source, or change one. Raises ChannelError, by field."""
        problems: dict[str, str] = {}
        with self._lock:
            rows = {row.name: row for row in self._store.console_sources()}
            before = rows.get(name)
            if creating:
                if not re.fullmatch(_CHANNEL_NAME, name):
                    problems["name"] = (
                        "Use lower-case letters, digits, - and _, 32 at most, starting with a"
                        " letter or a digit."
                    )
                elif name in _RESERVED_SOURCES or name in BUILT_IN_SOURCES:
                    problems["name"] = "This name is kept by Paeon. Choose another."
                elif name in self._file:
                    problems["name"] = "The rules file already defines a source of this name."
                elif before is not None:
                    problems["name"] = "A source of this name already exists."
                elif name.replace("-", "_") in {
                    other.replace("-", "_") for other in (*self._file, *rows)
                }:
                    problems["name"] = "Another source has this name, but for - and _."
            elif before is None or before.type != service.type:
                raise ChannelError({"": "This source no longer exists."})
            sealed = dict(before.secrets) if before is not None and not creating else {}
            for key in forget:
                sealed.pop(key, None)
            moved = (
                before is not None
                and not creating
                and any(before.config.get(key) != config.get(key) for key in DESTINATION)
            )
            for key in sealed if moved else ():
                if key not in secrets:
                    problems[key] = (
                        "Type it again. Where it is sent changed, and a stored secret is"
                        " never sent to a new address."
                    )
            for key, value in secrets.items():
                sealed[key] = self._vault.seal(value)
            row = ConsoleChannel(
                name=name,
                type=service.type,
                config=dict(config),
                stages=(),
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
            self._store.save_console_source(row)
            self._probes.pop(name, None)
            self._store.append(
                "console.source.saved",
                {
                    "name": name,
                    "service": service.type,
                    "secrets": sorted(sealed),
                    "created": creating,
                    "by": by,
                },
                now=now,
            )
            self._apply()

    def remove(self, name: str, *, by: str, now: datetime | None = None) -> bool:
        with self._lock:
            if not self._store.delete_console_source(name):
                return False
            self._probes.pop(name, None)
            self._store.append("console.source.removed", {"name": name, "by": by}, now=now)
            self._apply()
        return True

    def test(self, name: str) -> tuple[bool, str] | None:
        """Ask a source one question. None: no such source here, or nothing to ask it (a
        source with no server only receives)."""
        one = self._connected.get(name)
        if one is None or one.probe is None:
            return None
        try:
            answer = (True, one.probe())
        except ReadError as exc:
            answer = (False, str(exc))
        except Exception as exc:  # the message may quote a URL: keep its kind
            answer = (False, f"the source failed: {type(exc).__name__}")
        self._probes[name] = answer
        return answer

    def can_test(self, name: str) -> bool:
        one = self._connected.get(name)
        return one is not None and one.probe is not None

    # --- connecting ---------------------------------------------------------------------------

    def apply(self) -> None:
        with self._lock:
            self._apply()

    def _apply(self) -> None:
        built: dict[str, tuple[datetime, Connected]] = {}
        broken: dict[str, str] = {}
        models: dict[str, Any] = {}
        for row in self._store.console_sources():
            kept = self._built.get(row.name)
            try:
                if row.name in self._file:
                    raise ChannelError({"": "the rules file now defines a source of this name"})
                model = _model(row)
                if kept is not None and kept[0] == row.updated_at:
                    built[row.name] = kept  # unchanged: its client and its cursor stay
                else:
                    secrets = {key: self._vault.open(value) for key, value in row.secrets.items()}
                    built[row.name] = (
                        row.updated_at,
                        sources.build(
                            row.name,
                            model,
                            secrets,
                            store=self._store,
                            recorder=self._recorder,
                            transport=self._transport,
                        ),
                    )
            except ChannelError as exc:
                broken[row.name] = exc.problems.get("", str(exc))
            except VaultError:
                broken[row.name] = "its secrets cannot be read with this PAEON_SECRET_KEY"
            except ValidationError:
                broken[row.name] = "its settings are no longer valid for this version of Paeon"
            except Exception as exc:
                log.exception("console source %s cannot be connected", row.name)
                broken[row.name] = f"it cannot be connected: {type(exc).__name__}"
            else:
                models[row.name] = model.model_dump(mode="json", exclude_unset=True)
        document = self._rules.model_dump(mode="json", exclude_unset=True)
        document["sources"] = {**(document.get("sources") or {}), **models}
        try:
            parse_rules(document)
        except RulesError:  # each source was checked alone: this is a bug, not a setting
            log.exception("console sources cannot be laid over the rules file")
            for _, one in built.values():
                if all(one is not kept for _, kept in self._built.values()):
                    one.close()
            return
        for name, why in broken.items():
            if self._broken.get(name) != why:
                log.warning("console source %s is set aside: %s", name, why)
        for name, (_, old) in self._built.items():
            if name not in built or built[name][1] is not old:
                self._connected.pop(name, None)
                old.close()
        for name, (_, one) in built.items():
            self._connected[name] = one
        self._built = built
        self._broken = broken
        if self.changed is not None:
            self.changed()

    def close(self) -> None:
        with self._lock:
            for _, one in self._built.values():
                one.close()


def _model(row: ConsoleChannel) -> Any:
    """The row as the source the rules file would hold. Raises ValidationError."""
    document: dict[str, Any] = {"type": row.type, **row.config}
    for key in row.secrets:
        document[f"{key}_env"] = STORED
    return _ADAPTER.validate_python(document)


def _explain(exc: ValidationError, service: Service) -> dict[str, str]:
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
            problems.setdefault(spec.key, f"{text[:1].upper() + text[1:]}.")
    return problems
