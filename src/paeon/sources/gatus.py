"""Gatus: a custom alert in, the endpoint statuses read.

Gatus has no webhook of a fixed shape: its `custom` alerting provider sends the body you write,
with placeholders (github.com/TwiN/gatus, "Configuring custom alerts"). The guide gives the
body this parser reads. `[ALERT_TRIGGERED_OR_RESOLVED]` becomes `TRIGGERED` or `RESOLVED`.
Reads are the GETs of `/api/v1/endpoints`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx2 as httpx

from paeon.models import ToolSpec
from paeon.rules import GatusSource
from paeon.schema import Alert, Severity, Status
from paeon.sources import Connected, reader, tool_name
from paeon.tools import Tool, _schema, _string

KIND = "gatus"
_PATHS = ("/api/v1/endpoints/statuses", "/health")
_STATES = {"TRIGGERED": Status.FIRING, "RESOLVED": Status.RESOLVED}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def key_of(group: str, name: str) -> str:
    """An endpoint's key as Gatus builds it: `<group>_<name>`, with `-` for what a URL dislikes."""
    return (
        re.sub(r"[ /_,.#+&]", "-", group).lower() + "_" + re.sub(r"[ /_,.#+&]", "-", name).lower()
    )


def parse(payload: Mapping[str, Any], *, now: datetime, source: str) -> list[Alert]:
    status = _STATES.get(_text(payload.get("status")).upper())
    name = _text(payload.get("name"))
    if status is None or not name:
        return []
    group = _text(payload.get("group"))
    labels = {"endpoint": name}
    if group:
        labels["group"] = group
    described = [_text(payload.get("description")), _text(payload.get("errors"))]
    return [
        Alert(
            source=source,
            fingerprint=key_of(group, name),
            status=status,
            severity=Severity.CRITICAL if status is Status.FIRING else Severity.UNKNOWN,
            title=f"{group}/{name}" if group else name,
            description="\n".join(part for part in described if part),
            labels=labels,
            starts_at=now,
            observed_at=now,
            raw=dict(payload),
        )
    ]


def endpoints_of(
    listed: Any, text: str = "", *, failing_only: bool = False
) -> list[dict[str, Any]]:
    """One entry per endpoint of `/api/v1/endpoints/statuses`: its last result and its events."""
    found: list[dict[str, Any]] = []
    for entry in listed if isinstance(listed, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        group = str(entry.get("group") or "")
        if text not in f"{group} {name}".lower():
            continue
        results = [r for r in entry.get("results") or [] if isinstance(r, dict)]
        last = results[-1] if results else {}
        if failing_only and last.get("success") is True:
            continue
        duration = last.get("duration")
        events = [e for e in entry.get("events") or [] if isinstance(e, dict)]
        found.append(
            {
                "name": name,
                "group": group or None,
                "key": entry.get("key"),
                "healthy": last.get("success"),
                "http_status": last.get("status"),
                "response_ms": round(duration / 1e6, 1)
                if isinstance(duration, int | float) and not isinstance(duration, bool)
                else None,
                "errors": [str(error)[:300] for error in last.get("errors") or []][:5],
                "conditions": [
                    {"condition": c.get("condition"), "met": c.get("success")}
                    for c in last.get("conditionResults") or []
                    if isinstance(c, dict)
                ][:10],
                "checked_at": last.get("timestamp"),
                "failed_of_last": f"{sum(r.get('success') is not True for r in results)}"
                f" of {len(results)}",
                "events": [{"type": e.get("type"), "at": e.get("timestamp")} for e in events[-5:]],
            }
        )
    return found


def connect(
    name: str,
    config: GatusSource,
    secrets: Mapping[str, str],
    transport: httpx.BaseTransport | None = None,
) -> Connected:
    def parsed(payload: Mapping[str, Any], now: datetime) -> list[Alert]:
        return parse(payload, now=now, source=name)

    if config.url is None:
        return Connected(name, KIND, parse=parsed)
    http = reader(KIND, config, secrets, _PATHS, transport=transport)

    def endpoint_status(args: dict[str, Any]) -> Any:
        text = _string(args, "text", required=False).lower()
        listed = http.json("/api/v1/endpoints/statuses", page=1, pageSize=100)
        found = endpoints_of(listed, text, failing_only=args.get("failing_only") is True)
        return {"endpoints": found[:100], "count": len(found)}

    def probe() -> str:
        listed = http.json("/api/v1/endpoints/statuses", page=1, pageSize=100)
        return f"{len(listed) if isinstance(listed, list) else 0} endpoints"

    tools = [
        Tool(
            ToolSpec(
                tool_name("endpoint_status", name),
                f"The endpoints the Gatus `{name}` watches: whether each is healthy now, its"
                " last response time and status code, which conditions failed, how many of"
                " its recent checks failed, and when it last changed state.",
                _schema(
                    {
                        "text": {"type": "string", "description": "Part of a name or group."},
                        "failing_only": {"type": "boolean"},
                    }
                ),
            ),
            endpoint_status,
        )
    ]
    return Connected(name, KIND, parse=parsed, tools=tools, probe=probe, close=http.close)
