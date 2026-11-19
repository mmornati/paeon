"""A Netdata agent: its custom notification in, active alerts and chart data read.

The agent calls `custom_sender()` of `health_alarm_notify.conf` on every status change, with
the alert in shell variables (learn.netdata.cloud, "Agent dispatched notifications: custom").
The guide gives a sender that posts them as a form, so that no value has to be quoted by hand.
`CRITICAL` and `WARNING` are firing, `CLEAR` is resolved, and the other statuses say nothing.

Reads use `/api/v1`, which the agent still serves next to v3 and whose answers are documented
field by field. Only the paths below are asked: some of the agent's other GETs change its state.
The agent has no read-only token; it limits by address (`allow dashboard from`).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx2 as httpx

from paeon.models import ToolSpec
from paeon.rules import NetdataSource
from paeon.schema import Alert, Severity, Status
from paeon.sources import Connected, ReadError, reader, tool_name
from paeon.tools import Tool, ToolError, _integer, _schema, _string

KIND = "netdata"
_PATHS = ("/api/v1/alarms", "/api/v1/data", "/api/v1/info")
_FIRING = {"CRITICAL": Severity.CRITICAL, "WARNING": Severity.WARNING}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse(payload: Mapping[str, Any], *, now: datetime, source: str) -> list[Alert]:
    state = _text(payload.get("status")).upper()
    name = _text(payload.get("name")) or _text(payload.get("alarm"))
    if not name or (state not in _FIRING and state != "CLEAR"):
        return []
    host = _text(payload.get("host"))
    chart = _text(payload.get("chart"))
    labels = {"alarm": name}
    for key, value in (("host", host), ("chart", chart)):
        if value:
            labels[key] = value
    value = _text(payload.get("value"))
    described = _text(payload.get("info"))
    if value:
        described = f"{described}\nValue: {value}".strip()
    return [
        Alert(
            source=source,
            fingerprint=f"{host}/{chart}/{name}",
            status=Status.FIRING if state in _FIRING else Status.RESOLVED,
            severity=_FIRING.get(state, Severity.UNKNOWN),
            title=name,
            description=described,
            labels=labels,
            starts_at=now,
            observed_at=now,
            source_url=_text(payload.get("url")) or None,
            raw=dict(payload),
        )
    ]


def _moment(seconds: Any) -> str | None:
    if isinstance(seconds, bool) or not isinstance(seconds, int | float) or seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def alarms_of(answer: Any, text: str = "") -> list[dict[str, Any]]:
    """The alarms of `/api/v1/alarms`, one entry each."""
    alarms = answer.get("alarms") if isinstance(answer, dict) else None
    found: list[dict[str, Any]] = []
    for alarm in alarms.values() if isinstance(alarms, dict) else []:
        if not isinstance(alarm, dict):
            continue
        if text not in f"{alarm.get('name')} {alarm.get('chart')}".lower():
            continue
        found.append(
            {
                "name": alarm.get("name"),
                "chart": alarm.get("chart"),
                "status": alarm.get("status"),
                "value": alarm.get("value_string") or alarm.get("value"),
                "units": alarm.get("units"),
                "info": str(alarm.get("summary") or alarm.get("info") or "")[:300],
                "since": _moment(alarm.get("last_status_change")),
            }
        )
    return sorted(found, key=lambda entry: str(entry["name"]))


def connect(
    name: str,
    config: NetdataSource,
    secrets: Mapping[str, str],
    transport: httpx.BaseTransport | None = None,
) -> Connected:
    def parsed(payload: Mapping[str, Any], now: datetime) -> list[Alert]:
        return parse(payload, now=now, source=name)

    if config.url is None:
        return Connected(name, KIND, parse=parsed)
    http = reader(KIND, config, secrets, _PATHS, transport=transport)

    def active_alerts(args: dict[str, Any]) -> Any:
        text = _string(args, "text", required=False).lower()
        answer = http.json("/api/v1/alarms", **({"all": ""} if args.get("all") is True else {}))
        found = alarms_of(answer, text)
        return {"alerts": found[:200], "count": len(found)}

    def chart_data(args: dict[str, Any]) -> Any:
        chart = _string(args, "chart")
        if not any(fnmatch.fnmatchcase(chart, pattern) for pattern in config.charts):
            raise ToolError(f"chart {chart!r} is not readable; readable: {list(config.charts)}")
        minutes = _integer(args, "since_minutes", 60, low=1, high=7 * 24 * 60)
        points = _integer(args, "points", 60, low=1, high=300)
        try:
            answer = http.json(
                "/api/v1/data",
                chart=chart,
                after=-minutes * 60,
                points=points,
                group="average",
                format="json",
            )
        except ReadError as exc:
            if str(exc).endswith(("HTTP 404", "HTTP 400")):
                raise ToolError("no chart with this id on this agent") from None
            raise
        if not isinstance(answer, dict):
            raise ToolError("netdata did not answer with a table")
        return {"columns": answer.get("labels"), "rows": answer.get("data")}

    def probe() -> str:
        info = http.json("/api/v1/info")
        version = info.get("version") if isinstance(info, dict) else None
        return f"Netdata {version or 'of an unknown version'}"

    tools = [
        Tool(
            ToolSpec(
                tool_name("active_alerts", name),
                f"The alerts the Netdata agent `{name}` raises now (warning or critical), each"
                " with its chart, its value and since when. With `all`, also those that are"
                " clear: every alert the agent evaluates.",
                _schema(
                    {
                        "text": {"type": "string", "description": "Part of a name or a chart."},
                        "all": {"type": "boolean"},
                    }
                ),
            ),
            active_alerts,
        )
    ]
    if config.charts:
        tools.append(
            Tool(
                ToolSpec(
                    tool_name("chart_data", name),
                    f"The values of one chart of the Netdata agent `{name}` over time, averaged"
                    " into `points` rows. `chart` is a chart id, as an alert names it"
                    f" (system.cpu, disk_space._). Readable: {', '.join(config.charts)}.",
                    _schema(
                        {
                            "chart": {"type": "string"},
                            "since_minutes": {"type": "integer", "description": "Default 60."},
                            "points": {"type": "integer", "description": "Default 60."},
                        },
                        ["chart"],
                    ),
                ),
                chart_data,
            )
        )
    return Connected(name, KIND, parse=parsed, tools=tools, probe=probe, close=http.close)
