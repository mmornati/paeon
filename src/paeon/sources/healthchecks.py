"""Healthchecks: a webhook integration in, the checks and their flips read.

The webhook integration sends the request you write, one for "down" and one for "up", with
placeholders (`$STATUS`, `$NAME_JSON`, `$SLUG`, `$TAGS`). The guide gives the body this parser
reads; it leaves out `$CODE`, the check's UUID, which is also the secret of its ping URL.
Reads use the project's **read-only** API key (healthchecks.io/docs/api): the list of checks,
one check, and its flips. With that key the service itself leaves ping URLs out of its answers.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx2 as httpx

from paeon.models import ToolSpec
from paeon.rules import HealthchecksSource
from paeon.schema import Alert, Severity, Status
from paeon.sources import Connected, reader, tool_name
from paeon.tools import Tool, ToolError, _integer, _schema, _string

KIND = "healthchecks"
_PATHS = ("/api/v3/checks",)
_STATES = {"down": Status.FIRING, "up": Status.RESOLVED}
_SHOWN = (
    "name", "slug", "tags", "desc", "status", "last_ping", "next_ping",
    "grace", "timeout", "schedule", "tz", "n_pings", "started",
)  # fmt: skip


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse(payload: Mapping[str, Any], *, now: datetime, source: str) -> list[Alert]:
    status = _STATES.get(_text(payload.get("status")).lower())
    name = _text(payload.get("name"))
    identity = _text(payload.get("slug")) or name
    if status is None or not identity:
        return []
    labels = {"check": name or identity}
    tags = _text(payload.get("tags"))
    if tags:
        labels["tags"] = tags
    return [
        Alert(
            source=source,
            fingerprint=identity,
            status=status,
            severity=Severity.WARNING if status is Status.FIRING else Severity.UNKNOWN,
            title=name or identity,
            description="The check did not report in time." if status is Status.FIRING else "",
            labels=labels,
            starts_at=now,
            observed_at=now,
            raw=dict(payload),
        )
    ]


def checks_of(answer: Any, text: str = "") -> list[dict[str, Any]]:
    """The checks of `GET /api/v3/checks/`, without the URLs that ping, pause or change them."""
    listed = answer.get("checks") if isinstance(answer, dict) else None
    found: list[dict[str, Any]] = []
    for check in listed if isinstance(listed, list) else []:
        if not isinstance(check, dict):
            continue
        if text not in f"{check.get('name')} {check.get('slug')} {check.get('tags')}".lower():
            continue
        found.append({key: check[key] for key in _SHOWN if check.get(key) not in (None, "")})
    return found


def connect(
    name: str,
    config: HealthchecksSource,
    secrets: Mapping[str, str],
    transport: httpx.BaseTransport | None = None,
) -> Connected:
    def parsed(payload: Mapping[str, Any], now: datetime) -> list[Alert]:
        return parse(payload, now=now, source=name)

    if config.url is None:
        return Connected(name, KIND, parse=parsed)
    key = secrets.get("api_key")
    http = reader(
        KIND,
        config,
        secrets,
        _PATHS,
        headers={"X-Api-Key": key} if key else None,
        transport=transport,
    )

    def check_status(args: dict[str, Any]) -> Any:
        text = _string(args, "text", required=False).lower()
        found = checks_of(http.json("/api/v3/checks/"), text)
        return {"checks": found[:200], "count": len(found)}

    def check_flips(args: dict[str, Any]) -> Any:
        wanted = _string(args, "check").lower()
        hours = _integer(args, "since_hours", 24, low=1, high=24 * 30)
        answer = http.json("/api/v3/checks/")
        listed = answer.get("checks") if isinstance(answer, dict) else None
        for check in listed if isinstance(listed, list) else []:
            if not isinstance(check, dict):
                continue
            if wanted in (str(check.get("name") or "").lower(), str(check.get("slug") or "")):
                # A read-only key gets `unique_key`; a read-write key gets `uuid`.
                identity = check.get("unique_key") or check.get("uuid")
                break
        else:
            raise ToolError("no check with this name or slug")
        if not isinstance(identity, str) or not identity.replace("-", "").isalnum():
            raise ToolError("this check has no key to ask its flips with")
        flips = http.json(f"/api/v3/checks/{identity}/flips/", seconds=hours * 3600)
        listed_flips = flips if isinstance(flips, list) else []
        return {
            "flips": [
                {"at": flip.get("timestamp"), "to": "up" if flip.get("up") else "down"}
                for flip in listed_flips[:200]
                if isinstance(flip, dict)
            ],
            "count": len(listed_flips[:200]),
        }

    def probe() -> str:
        return f"{len(checks_of(http.json('/api/v3/checks/')))} checks"

    tools = [
        Tool(
            ToolSpec(
                tool_name("check_status", name),
                f"The checks of the Healthchecks project `{name}` (cron jobs, backups and other"
                " things that must report in): each one's status (up, down, grace, paused,"
                " new), its last and next expected ping, and its schedule.",
                _schema({"text": {"type": "string", "description": "Part of a name or tag."}}),
            ),
            check_status,
        ),
        Tool(
            ToolSpec(
                tool_name("check_flips", name),
                f"When one check of the Healthchecks project `{name}` went down and came back"
                " up, over the last hours. `check` is its name or its slug.",
                _schema(
                    {
                        "check": {"type": "string"},
                        "since_hours": {"type": "integer", "description": "Default 24."},
                    },
                    ["check"],
                ),
            ),
            check_flips,
        ),
    ]
    return Connected(name, KIND, parse=parsed, tools=tools, probe=probe, close=http.close)
