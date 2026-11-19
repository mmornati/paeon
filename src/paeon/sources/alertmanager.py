"""Alertmanager: its webhook brings alerts in, and its API says what is active and silenced.

The webhook is version 4 of the payload (`paeon.sources.webhook.parse_alertmanager`), which
Grafana, vmalert and many other tools send too. The reads are the GETs of API v2
(github.com/prometheus/alertmanager, `api/v2/openapi.yaml`); the calls that create or delete a
silence are POST and DELETE, and are not on the list of paths.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx2 as httpx

from paeon.models import ToolSpec
from paeon.rules import AlertmanagerSource
from paeon.schema import Alert
from paeon.sources import Connected, reader, tool_name
from paeon.sources.http import listing, mapping
from paeon.sources.webhook import parse_alertmanager
from paeon.tools import Tool, _schema, _string

KIND = "alertmanager"
_PATHS = ("/api/v2/alerts", "/api/v2/silences", "/api/v2/status")


def alerts_of(listed: Any, text: str = "") -> list[dict[str, Any]]:
    """The alerts of `GET /api/v2/alerts`, with what keeps each one quiet."""
    found: list[dict[str, Any]] = []
    for entry in listed if isinstance(listed, list) else []:
        if not isinstance(entry, dict):
            continue
        labels = mapping(entry.get("labels"))
        if text not in " ".join(str(value) for value in labels.values()).lower():
            continue
        status = mapping(entry.get("status"))
        receivers = listing(entry.get("receivers"))
        found.append(
            {
                "fingerprint": entry.get("fingerprint"),
                "labels": labels,
                "annotations": entry.get("annotations") or {},
                "starts_at": entry.get("startsAt"),
                "state": status.get("state"),  # active, suppressed or unprocessed
                "silenced_by": status.get("silencedBy") or [],
                "inhibited_by": status.get("inhibitedBy") or [],
                "receivers": [r.get("name") for r in receivers if isinstance(r, dict)],
            }
        )
    return found


def silences_of(listed: Any, *, expired: bool = False) -> list[dict[str, Any]]:
    """The silences of `GET /api/v2/silences`: what they match, until when, and who said why."""
    found: list[dict[str, Any]] = []
    for entry in listed if isinstance(listed, list) else []:
        if not isinstance(entry, dict):
            continue
        status = mapping(entry.get("status"))
        if status.get("state") == "expired" and not expired:
            continue
        matchers = listing(entry.get("matchers"))
        found.append(
            {
                "id": entry.get("id"),
                "state": status.get("state"),
                "matchers": [
                    {
                        "name": m.get("name"),
                        "value": m.get("value"),
                        "regex": m.get("isRegex") is True,
                        "equal": m.get("isEqual") is not False,
                    }
                    for m in matchers
                    if isinstance(m, dict)
                ],
                "starts_at": entry.get("startsAt"),
                "ends_at": entry.get("endsAt"),
                "created_by": entry.get("createdBy"),
                "comment": str(entry.get("comment") or "")[:300],
            }
        )
    return found


def connect(
    name: str,
    config: AlertmanagerSource,
    secrets: Mapping[str, str],
    transport: httpx.BaseTransport | None = None,
) -> Connected:
    def parse(payload: Mapping[str, Any], now: datetime) -> list[Alert]:
        return parse_alertmanager(dict(payload), now=now, source=name)

    if config.url is None:
        return Connected(name, KIND, parse=parse)
    http = reader(KIND, config, secrets, _PATHS, transport=transport)

    def active_alerts(args: dict[str, Any]) -> Any:
        text = _string(args, "text", required=False).lower()
        found = alerts_of(http.json("/api/v2/alerts"), text)
        return {"alerts": found[:100], "count": len(found)}

    def silences(args: dict[str, Any]) -> Any:
        found = silences_of(http.json("/api/v2/silences"), expired=args.get("expired") is True)
        return {"silences": found[:100], "count": len(found)}

    def probe() -> str:
        status = http.json("/api/v2/status")
        version = status.get("versionInfo", {}) if isinstance(status, dict) else {}
        return f"Alertmanager {version.get('version', 'of an unknown version')}"

    tools = [
        Tool(
            ToolSpec(
                tool_name("active_alerts", name),
                f"The alerts the Alertmanager `{name}` holds now, each with its state: active,"
                " or suppressed and by which silence or which other alert. Says why an alert"
                " that fires notified nobody.",
                _schema({"text": {"type": "string", "description": "Part of a label value."}}),
            ),
            active_alerts,
        ),
        Tool(
            ToolSpec(
                tool_name("silences", name),
                f"The silences of the Alertmanager `{name}`: what each matches, until when,"
                " who created it and the comment they left.",
                _schema({"expired": {"type": "boolean", "description": "Also the expired ones."}}),
            ),
            silences,
        ),
    ]
    return Connected(name, KIND, parse=parse, tools=tools, probe=probe, close=http.close)
