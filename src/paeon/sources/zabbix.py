"""Zabbix: a webhook media type in, problems, items and history read over its API.

A webhook media type is a script of yours (zabbix.com/documentation/7.0, "Webhook"): the guide
gives one that posts the event's macros as JSON. `{EVENT.VALUE}` is 1 for a problem and 0 for
its recovery; a message about an update of a problem (`{EVENT.UPDATE.STATUS}` 1) is not a
state change and is left out.

The API is JSON-RPC: every call is a POST to one address, reads included. So the list that
matters here is the list of **methods**: this adapter can only send the `get` methods below,
with parameters it writes itself. Give the token to a user whose role allows only `*.get`.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2 as httpx

from paeon.models import ToolSpec
from paeon.rules import ZabbixSource
from paeon.schema import Alert, Severity, Status
from paeon.sources import Connected, ReadError, reader, tool_name
from paeon.sources.http import Reader, decoded
from paeon.tools import Tool, ToolError, _integer, _schema, _string

KIND = "zabbix"
_PATH = "/api_jsonrpc.php"
METHODS = frozenset(
    {"apiinfo.version", "problem.get", "trigger.get", "host.get", "item.get", "history.get"}
)
# Not classified, information, warning, average, high, disaster.
_SEVERITY = (
    Severity.UNKNOWN,
    Severity.INFO,
    Severity.WARNING,
    Severity.WARNING,
    Severity.CRITICAL,
    Severity.CRITICAL,
)
_SEVERITY_NAME = ("not classified", "information", "warning", "average", "high", "disaster")


def _text(value: Any) -> str:
    return (
        str(value).strip() if isinstance(value, str | int) and not isinstance(value, bool) else ""
    )


def _level(value: Any) -> int:
    text = _text(value)
    return int(text) if text.isdigit() and int(text) < len(_SEVERITY) else 0


def parse(payload: Mapping[str, Any], *, now: datetime, source: str) -> list[Alert]:
    value = _text(payload.get("event_value"))
    if value not in ("0", "1") or _text(payload.get("update_status")) == "1":
        return []
    identity = _text(payload.get("trigger_id")) or _text(payload.get("event_id"))
    name = _text(payload.get("name"))
    if not identity or not name:
        return []
    labels = {"trigger_id": identity}
    for key in ("host", "event_id"):
        if _text(payload.get(key)):
            labels[key] = _text(payload.get(key))
    firing = value == "1"
    return [
        Alert(
            source=source,
            fingerprint=identity,
            status=Status.FIRING if firing else Status.RESOLVED,
            severity=_SEVERITY[_level(payload.get("severity"))] if firing else Severity.UNKNOWN,
            title=name,
            description=_text(payload.get("description")),
            labels=labels,
            starts_at=now,
            observed_at=now,
            source_url=_text(payload.get("url")) or None,
            raw=dict(payload),
        )
    ]


class Api:
    """JSON-RPC calls, limited to `METHODS`."""

    def __init__(self, http: Reader) -> None:
        self._http = http

    def call(self, method: str, params: Any) -> Any:
        if method not in METHODS:
            raise ReadError(f"zabbix: {method} is not a method this source calls")
        body = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        # `apiinfo.version` is refused when it comes with a token.
        bare = method == "apiinfo.version"
        answer = decoded(KIND, self._http.post(_PATH, body, bare=bare))
        if not isinstance(answer, dict) or "result" not in answer:
            error = answer.get("error") if isinstance(answer, dict) else None
            reason = error.get("message") if isinstance(error, dict) else None
            raise ReadError(f"zabbix refused {method}: {str(reason or 'no reason given')[:200]}")
        return answer["result"]


def _moment(clock: Any) -> str | None:
    text = _text(clock)
    return datetime.fromtimestamp(int(text), UTC).isoformat() if text.isdigit() else None


def _rows(result: Any) -> list[dict[str, Any]]:
    return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []


def connect(
    name: str,
    config: ZabbixSource,
    secrets: Mapping[str, str],
    transport: httpx.BaseTransport | None = None,
) -> Connected:
    def parsed(payload: Mapping[str, Any], now: datetime) -> list[Alert]:
        return parse(payload, now=now, source=name)

    if config.url is None:
        return Connected(name, KIND, parse=parsed)
    http = reader(KIND, config, secrets, (_PATH,), transport=transport)
    api = Api(http)

    def readable(host: str) -> bool:
        return any(fnmatch.fnmatchcase(host, pattern) for pattern in config.hosts)

    def problems(args: dict[str, Any]) -> Any:
        text = _string(args, "text", required=False).lower()
        listed = _rows(
            api.call(
                "problem.get",
                {
                    "output": ["eventid", "objectid", "clock", "name", "severity", "acknowledged"],
                    "sortfield": ["eventid"],
                    "sortorder": "DESC",
                    "limit": 200,
                },
            )
        )
        triggers = _rows(
            api.call(
                "trigger.get",
                {
                    "triggerids": sorted({_text(p.get("objectid")) for p in listed}),
                    "output": ["triggerid"],
                    "selectHosts": ["host"],
                },
            )
            if listed
            else []
        )
        hosts = {
            _text(t.get("triggerid")): [_text(h.get("host")) for h in _rows(t.get("hosts"))]
            for t in triggers
        }
        found = []
        for p in listed:
            on = hosts.get(_text(p.get("objectid")), [])
            title = _text(p.get("name"))
            if text not in f"{title} {' '.join(on)}".lower():
                continue
            found.append(
                {
                    "event_id": _text(p.get("eventid")),
                    "name": title,
                    "severity": _SEVERITY_NAME[_level(p.get("severity"))],
                    "hosts": on,
                    "since": _moment(p.get("clock")),
                    "acknowledged": _text(p.get("acknowledged")) == "1",
                }
            )
        return {"problems": found[:100], "count": len(found)}

    def host_id(host: str) -> str:
        if not readable(host):
            raise ToolError(f"host {host!r} is not readable; readable: {list(config.hosts)}")
        found = _rows(api.call("host.get", {"filter": {"host": [host]}, "output": ["hostid"]}))
        if not found:
            raise ToolError("no host with this name")
        return _text(found[0].get("hostid"))

    def host_items(args: dict[str, Any]) -> Any:
        host = _string(args, "host")
        text = _string(args, "text", required=False)
        params: dict[str, Any] = {
            "hostids": [host_id(host)],
            "output": ["itemid", "name", "key_", "lastvalue", "units", "lastclock"],
            "sortfield": "name",
            "limit": 100,
        }
        if text:
            params |= {"search": {"name": text, "key_": text}, "searchByAny": True}
        found = [
            {
                "item_id": _text(item.get("itemid")),
                "name": _text(item.get("name")),
                "key": _text(item.get("key_")),
                "last_value": str(item.get("lastvalue") or "")[:300],
                "units": _text(item.get("units")) or None,
                "last_at": _moment(item.get("lastclock")),
            }
            for item in _rows(api.call("item.get", params))
        ]
        return {"items": found, "count": len(found)}

    def item_history(args: dict[str, Any]) -> Any:
        item_id = _string(args, "item_id")
        if not item_id.isdigit():
            raise ToolError("item_id is a number, as host_items gives it")
        minutes = _integer(args, "since_minutes", 60, low=1, high=7 * 24 * 60)
        items = _rows(
            api.call(
                "item.get",
                {
                    "itemids": [item_id],
                    "output": ["name", "units", "value_type"],
                    "selectHosts": ["host"],
                },
            )
        )
        if not items:
            raise ToolError("no item with this id")
        owners = [_text(h.get("host")) for h in _rows(items[0].get("hosts"))]
        if not owners or not all(readable(host) for host in owners):
            raise ToolError(f"this item's host is not readable; readable: {list(config.hosts)}")
        kind = _text(items[0].get("value_type"))
        since = datetime.now(UTC) - timedelta(minutes=minutes)
        values = _rows(
            api.call(
                "history.get",
                {
                    "history": int(kind) if kind.isdigit() else 3,
                    "itemids": [item_id],
                    "time_from": int(since.timestamp()),
                    "output": ["clock", "value"],
                    "sortfield": "clock",
                    "sortorder": "DESC",
                    "limit": config.max_values,
                },
            )
        )
        return {
            "item": _text(items[0].get("name")),
            "units": _text(items[0].get("units")) or None,
            "values": [
                {"at": _moment(v.get("clock")), "value": str(v.get("value") or "")[:500]}
                for v in values
            ],
            "count": len(values),
        }

    def probe() -> str:
        version = api.call("apiinfo.version", {})
        api.call("host.get", {"output": ["hostid"], "limit": 1})  # the token, not only the server
        return f"Zabbix {version}"

    tools = [
        Tool(
            ToolSpec(
                tool_name("problems", name),
                f"The problems the Zabbix `{name}` holds open now, newest first: each one's"
                " name, severity, hosts, since when, and whether someone acknowledged it.",
                _schema({"text": {"type": "string", "description": "Part of a name or a host."}}),
            ),
            problems,
        )
    ]
    if config.hosts:
        tools += [
            Tool(
                ToolSpec(
                    tool_name("host_items", name),
                    f"The items the Zabbix `{name}` collects on one host, with each item's last"
                    " value. `text` narrows by name or key (cpu, vfs.fs, net.if)."
                    f" Readable hosts: {', '.join(config.hosts)}.",
                    _schema({"host": {"type": "string"}, "text": {"type": "string"}}, ["host"]),
                ),
                host_items,
            ),
            Tool(
                ToolSpec(
                    tool_name("item_history", name),
                    f"The values of one item of the Zabbix `{name}` over time, newest first."
                    " `item_id` comes from host_items.",
                    _schema(
                        {
                            "item_id": {"type": "string"},
                            "since_minutes": {"type": "integer", "description": "Default 60."},
                        },
                        ["item_id"],
                    ),
                ),
                item_history,
            ),
        ]
    return Connected(name, KIND, parse=parsed, tools=tools, probe=probe, close=http.close)
