"""Sources: one monitoring service each, named in the rules file under `sources`.

A source can do up to three things, and an adapter offers those its service allows:

- **receive** alerts the service pushes (`parse`, served at `/ingest/<name>`);
- **poll** the service for its alerts (`polling`), with a cursor and a staleness check;
- **read** for the investigator (`tools`): logs, metrics, rule definitions, monitor states.

Every adapter turns what it gets into `paeon.schema.Alert` and reads through
`paeon.sources.http.Reader`, on paths it lists itself. Like the notification channels, the set
is closed and lives in this package: nothing is loaded from outside.

A tool carries the name of its source (`query_metrics_prom`), so two Prometheus servers are two
tools and the model sees which one it asks.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

import httpx2 as httpx

from paeon import rules
from paeon.schema import Alert
from paeon.sources.http import Reader, ReadError

if TYPE_CHECKING:
    from paeon.recorder import Recorder
    from paeon.store import Store
    from paeon.tools import Tool

__all__ = ["Connected", "Polling", "ReadError", "build", "built_in", "reader", "tool_name"]

Parse = Callable[[Mapping[str, Any], datetime], list[Alert]]


class Poller(Protocol):
    name: str

    def poll(self, now: datetime) -> Any: ...

    def last_data_at(self) -> datetime | None: ...


@dataclass(frozen=True)
class Polling:
    poller: Poller
    every_seconds: float
    stale_seconds: float


@dataclass
class Connected:
    """One source, ready: what it receives, what it polls and what it lets the model read."""

    name: str
    kind: str
    parse: Parse | None = None
    polling: Polling | None = None
    tools: list[Tool] = field(default_factory=list)
    probe: Callable[[], str] | None = None  # one read, for `paeon source`; raises ReadError
    close: Callable[[], None] = lambda: None

    @property
    def alerts(self) -> str:
        """How its alerts arrive, in a word, for the console."""
        if self.parse is not None:
            return "webhook"
        return "polled" if self.polling is not None else "none"


def tool_name(tool: str, source: str) -> str:
    """A tool of a named source. The model's API takes letters, digits and underscores."""
    return f"{tool}_{source.replace('-', '_')}"


def reader(
    kind: str,
    config: Any,
    secrets: Mapping[str, str],
    paths: tuple[str, ...],
    *,
    headers: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Reader:
    """The reader of a source that has a `url`, with the way in its rules name."""
    return Reader(
        kind,
        config.url,
        paths=paths,
        user=config.user,
        password=secrets.get("password"),
        token=secrets.get("token"),
        headers={**getattr(config, "headers", {}), **(headers or {})},
        transport=transport,
    )


def built_in(name: str) -> rules.Source | None:
    """The source a name stands for when the rules file has no entry for it: the two webhooks
    Paeon has always answered."""
    if name == "alertmanager":
        return rules.AlertmanagerSource(type="alertmanager")
    if name == "uptime-kuma":
        return rules.UptimeKumaSource(type="uptime_kuma")
    return None


def build(
    name: str,
    config: rules.Source,
    secrets: Mapping[str, str],
    *,
    store: Store | None = None,
    recorder: Recorder | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Connected:
    """The adapter for one source of the rules file. Each is imported when it is used.

    Without a `store` and a `recorder` nothing is polled: that is how `paeon source` asks a
    service one question without recording anything.
    """
    if isinstance(config, rules.PrometheusSource):
        from paeon.sources import prometheus

        return prometheus.connect(name, config, secrets, transport)
    if isinstance(config, rules.AlertmanagerSource):
        from paeon.sources import alertmanager

        return alertmanager.connect(name, config, secrets, transport)
    if isinstance(config, rules.GrafanaSource):
        from paeon.sources import grafana

        return grafana.connect(name, config, secrets, transport)
    if isinstance(config, rules.LokiSource):
        from paeon.sources import loki

        return loki.connect(name, config, secrets, transport)
    if isinstance(config, rules.VictoriaLogsSource):
        from paeon.sources import victorialogs

        return victorialogs.connect(name, config, secrets, transport)
    if isinstance(config, rules.OpenObserveSource):
        from paeon.sources import openobserve

        return openobserve.connect(name, config, secrets, transport, store, recorder)
    if isinstance(config, rules.UptimeKumaSource):
        from paeon.sources import uptime_kuma

        return uptime_kuma.connect(name, config, secrets, transport)
    if isinstance(config, rules.GatusSource):
        from paeon.sources import gatus

        return gatus.connect(name, config, secrets, transport)
    if isinstance(config, rules.HealthchecksSource):
        from paeon.sources import healthchecks

        return healthchecks.connect(name, config, secrets, transport)
    if isinstance(config, rules.NetdataSource):
        from paeon.sources import netdata

        return netdata.connect(name, config, secrets, transport)
    if isinstance(config, rules.ZabbixSource):
        from paeon.sources import zabbix

        return zabbix.connect(name, config, secrets, transport)
    from paeon.sources import beszel

    return beszel.connect(name, config, secrets, transport, store, recorder)
