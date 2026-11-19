"""Grafana's alerting: a webhook contact point in, annotations and alert rules read.

The contact point sends the Alertmanager payload with a few keys of its own on each alert
(`values`, `dashboardURL`, `panelURL`, `silenceURL`): the values that made the rule fire are
kept in the description. Reads use a service account token with the Viewer role
(grafana.com/docs/grafana/latest/developers/http_api): annotations, and the alert rules of the
provisioning API. Whether Viewer may read the rules depends on the Grafana version and its
access control; a refusal is said as such.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2 as httpx

from paeon.models import ToolSpec
from paeon.rules import GrafanaSource
from paeon.schema import Alert
from paeon.sources import Connected, ReadError, reader, tool_name
from paeon.sources.webhook import parse_alertmanager
from paeon.tools import Tool, ToolError, _integer, _schema, _string

KIND = "grafana"
_PATHS = ("/api/annotations", "/api/v1/provisioning/alert-rules", "/api/health")
_QUERY_KEYS = ("expr", "query", "rawSql", "expression")


def parse(payload: Mapping[str, Any], *, now: datetime, source: str) -> list[Alert]:
    """The alerts of one call of a Grafana webhook contact point."""
    entries = payload.get("alerts")
    found: list[Alert] = []
    for entry in entries if isinstance(entries, list) else []:
        for alert in parse_alertmanager({"alerts": [entry]}, now=now, source=source):
            values = entry.get("values")
            described = alert.description
            if isinstance(values, dict) and values:
                read = ", ".join(f"{key}={value}" for key, value in sorted(values.items()))
                described = f"{described}\nValues: {read}".strip()
            link = alert.source_url
            for key in ("panelURL", "dashboardURL"):
                if not link and isinstance(entry.get(key), str):
                    link = entry[key] or None
            found.append(alert.model_copy(update={"description": described, "source_url": link}))
    return found


def _moment(millis: Any) -> str | None:
    if isinstance(millis, bool) or not isinstance(millis, int | float) or millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000, UTC).isoformat()


def annotations_of(listed: Any) -> list[dict[str, Any]]:
    """The entries of `GET /api/annotations`, with times a person can read."""
    found: list[dict[str, Any]] = []
    for entry in listed if isinstance(listed, list) else []:
        if not isinstance(entry, dict):
            continue
        found.append(
            {
                "time": _moment(entry.get("time")),
                "until": _moment(entry.get("timeEnd")),
                "text": str(entry.get("text") or "")[:500],
                "tags": entry.get("tags") or [],
                "alert": entry.get("alertName") or None,
                "new_state": entry.get("newState") or None,
                "previous_state": entry.get("prevState") or None,
                "dashboard": entry.get("dashboardUID") or None,
            }
        )
    return found


def rules_of(listed: Any, text: str = "") -> list[dict[str, Any]]:
    """The rules of the provisioning API, with the queries a person wrote in them."""
    found: list[dict[str, Any]] = []
    for rule in listed if isinstance(listed, list) else []:
        if not isinstance(rule, dict) or text not in str(rule.get("title") or "").lower():
            continue
        queries = []
        for step in rule.get("data") or []:
            model = step.get("model") if isinstance(step, dict) else None
            if not isinstance(model, dict):
                continue
            written = next(
                (str(model[key]) for key in _QUERY_KEYS if isinstance(model.get(key), str)), None
            )
            if written:
                queries.append({"ref": step.get("refId"), "query": written[:1000]})
        found.append(
            {
                "uid": rule.get("uid"),
                "title": rule.get("title"),
                "group": rule.get("ruleGroup"),
                "folder": rule.get("folderUID"),
                "condition": rule.get("condition"),
                "queries": queries,
                "for": rule.get("for"),
                "no_data_state": rule.get("noDataState"),
                "error_state": rule.get("execErrState"),
                "labels": rule.get("labels") or {},
                "annotations": rule.get("annotations") or {},
                "paused": rule.get("isPaused") is True,
            }
        )
    return found


def connect(
    name: str,
    config: GrafanaSource,
    secrets: Mapping[str, str],
    transport: httpx.BaseTransport | None = None,
) -> Connected:
    def parsed(payload: Mapping[str, Any], now: datetime) -> list[Alert]:
        return parse(payload, now=now, source=name)

    if config.url is None:
        return Connected(name, KIND, parse=parsed)
    http = reader(KIND, config, secrets, _PATHS, transport=transport)

    def annotations(args: dict[str, Any]) -> Any:
        minutes = _integer(args, "since_minutes", 24 * 60, low=1, high=30 * 24 * 60)
        limit = _integer(args, "limit", 50, low=1, high=200)
        now = datetime.now(UTC)
        listed = http.json(
            "/api/annotations",
            **{
                "from": int((now - timedelta(minutes=minutes)).timestamp() * 1000),
                "to": int(now.timestamp() * 1000),
                "limit": limit,
                "type": "alert" if args.get("alerts_only") is True else None,
            },
        )
        found = annotations_of(listed)
        return {"annotations": found[:limit], "count": len(found[:limit])}

    def alert_rules(args: dict[str, Any]) -> Any:
        text = _string(args, "text", required=False).lower()
        try:
            listed = http.json("/api/v1/provisioning/alert-rules")
        except ReadError as exc:
            if str(exc).endswith("HTTP 403"):
                raise ToolError(
                    "grafana refused: the service account may not read alert rules"
                ) from None
            raise
        found = rules_of(listed, text)
        return {"rules": found[:100], "count": len(found)}

    def probe() -> str:
        health = http.json("/api/health")
        version = health.get("version") if isinstance(health, dict) else None
        if config.annotations:
            http.json("/api/annotations", limit=1)  # the token, not only the server
        return f"Grafana {version or 'of an unknown version'}"

    tools: list[Tool] = []
    if config.annotations:
        tools.append(
            Tool(
                ToolSpec(
                    tool_name("annotations", name),
                    f"The annotations of the Grafana `{name}`, newest first: each state change"
                    " of an alert rule (from which state to which), and the marks people or"
                    " deploy scripts left on dashboards.",
                    _schema(
                        {
                            "since_minutes": {"type": "integer", "description": "Default 1440."},
                            "limit": {"type": "integer", "description": "Default 50."},
                            "alerts_only": {"type": "boolean"},
                        }
                    ),
                ),
                annotations,
            )
        )
    if config.rules:
        tools.append(
            Tool(
                ToolSpec(
                    tool_name("alert_rules", name),
                    f"The alert rules of the Grafana `{name}`: each rule's queries, its"
                    " condition, its `for`, what it does without data, and whether it is paused.",
                    _schema({"text": {"type": "string", "description": "Part of a rule title."}}),
                ),
                alert_rules,
            )
        )
    return Connected(name, KIND, parse=parsed, tools=tools, probe=probe, close=http.close)
