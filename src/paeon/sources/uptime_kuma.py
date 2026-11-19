"""uptime-kuma as a named source: the webhook of `paeon.sources.webhook`, and `/metrics`.

uptime-kuma 2 has no API for a monitor's state other than `/metrics`, which an API key opens
(as the password of basic authentication, with any user). Version 3 turns `/metrics` off unless
it is enabled in its settings.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx2 as httpx

from paeon.rules import UptimeKumaSource
from paeon.schema import Alert
from paeon.sources import Connected, ReadError, tool_name
from paeon.sources.webhook import parse_uptime_kuma
from paeon.tools import parse_monitors, uptime_kuma_tools

KIND = "uptime_kuma"


def connect(
    name: str,
    config: UptimeKumaSource,
    secrets: Mapping[str, str],
    transport: httpx.BaseTransport | None = None,
) -> Connected:
    def parse(payload: Mapping[str, Any], now: datetime) -> list[Alert]:
        alert = parse_uptime_kuma(dict(payload), now=now, source=name)
        return [] if alert is None else [alert]

    if config.url is None:
        return Connected(name, KIND, parse=parse)
    key = secrets.get("api_key") or secrets.get("password")
    http = httpx.Client(base_url=config.url.rstrip("/"), timeout=20, transport=transport)

    def probe() -> str:
        try:
            answer = http.get("/metrics", auth=("", key) if key else None)
        except httpx.HTTPError as exc:
            raise ReadError(f"uptime-kuma is unreachable: {type(exc).__name__}") from None
        if answer.status_code >= 400:
            raise ReadError(f"uptime-kuma answered HTTP {answer.status_code}")
        return f"{len(parse_monitors(answer.text))} monitors"

    tools = uptime_kuma_tools(http, key, name=tool_name("monitor_status", name))
    return Connected(name, KIND, parse=parse, tools=tools, probe=probe, close=http.close)
