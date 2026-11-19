"""The Prometheus HTTP API, read: Prometheus, VictoriaMetrics, Mimir, Thanos.

Written from the API's documentation (prometheus.io/docs/prometheus/latest/querying/api):
every call is a GET under `/api/v1`, and none of them changes anything. The alerts of these
servers reach Paeon through Alertmanager; this adapter only reads.
"""

from __future__ import annotations

import fnmatch
import math
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2 as httpx

from paeon.models import ToolSpec
from paeon.rules import PrometheusSource
from paeon.sources import Connected, reader, tool_name
from paeon.sources.http import mapping
from paeon.tools import Tool, ToolError, _integer, _schema, _string, metric_names

KIND = "prometheus"
_PATHS = ("/api/v1/query", "/api/v1/query_range", "/api/v1/rules", "/api/v1/targets")
# Prometheus refuses a range of more than 11000 points per series; VictoriaMetrics allows more.
_MAX_POINTS = 10000


def _data(answer: Any) -> Any:
    """The `data` of an answer `{"status": "success", "data": ...}`."""
    if not isinstance(answer, dict) or answer.get("status") != "success":
        error = answer.get("error") if isinstance(answer, dict) else None
        raise ToolError(f"prometheus refused the request: {str(error or 'no reason given')[:300]}")
    return answer.get("data")


def rules_of(data: Any, text: str = "", *, firing_only: bool = False) -> list[dict[str, Any]]:
    """The alerting rules of `/api/v1/rules`, one entry each, with the alerts they hold."""
    found: list[dict[str, Any]] = []
    groups = data.get("groups") if isinstance(data, dict) else None
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        for rule in group.get("rules") or []:
            if not isinstance(rule, dict) or rule.get("type", "alerting") != "alerting":
                continue
            name = str(rule.get("name") or "")
            if text not in name.lower() or (firing_only and rule.get("state") != "firing"):
                continue
            active = [a for a in rule.get("alerts") or [] if isinstance(a, dict)]
            found.append(
                {
                    "group": group.get("name"),
                    "name": name,
                    "query": rule.get("query"),
                    "for_seconds": rule.get("duration"),
                    "labels": rule.get("labels") or {},
                    "annotations": rule.get("annotations") or {},
                    "state": rule.get("state"),
                    "health": rule.get("health"),
                    "last_error": str(rule.get("lastError") or "")[:300] or None,
                    "last_evaluation": rule.get("lastEvaluation"),
                    "alerts": [
                        {
                            "labels": a.get("labels") or {},
                            "state": a.get("state"),
                            "active_at": a.get("activeAt"),
                            "value": a.get("value"),
                        }
                        for a in active[:20]
                    ],
                }
            )
    return found


def targets_of(data: Any, text: str = "", *, down_only: bool = False) -> list[dict[str, Any]]:
    """The active scrape targets. The scrape URL is left out: it can carry a credential."""
    found: list[dict[str, Any]] = []
    active = data.get("activeTargets") if isinstance(data, dict) else None
    for target in active if isinstance(active, list) else []:
        if not isinstance(target, dict):
            continue
        labels = mapping(target.get("labels"))
        job = str(labels.get("job") or target.get("scrapePool") or "")
        instance = str(labels.get("instance") or "")
        if text not in f"{job} {instance}".lower():
            continue
        if down_only and target.get("health") == "up":
            continue
        found.append(
            {
                "job": job,
                "instance": instance,
                "health": target.get("health"),
                "last_error": str(target.get("lastError") or "")[:300] or None,
                "last_scrape": target.get("lastScrape"),
                "last_scrape_seconds": target.get("lastScrapeDuration"),
            }
        )
    return found


def connect(
    name: str,
    config: PrometheusSource,
    secrets: Mapping[str, str],
    transport: httpx.BaseTransport | None = None,
) -> Connected:
    http = reader(KIND, config, secrets, _PATHS, transport=transport)

    def query_metrics(args: dict[str, Any]) -> Any:
        promql = _string(args, "promql")
        names = metric_names(promql)
        refused = sorted(
            metric
            for metric in names
            if not any(fnmatch.fnmatchcase(metric, pattern) for pattern in config.metrics)
        )
        if refused or not names:
            raise ToolError(
                f"not readable: {refused or 'no metric name found'};"
                f" readable metrics: {list(config.metrics)}"
            )
        minutes = _integer(args, "since_minutes", 60, low=1, high=7 * 24 * 60)
        step = _integer(args, "step_seconds", max(minutes, 15), low=15, high=3600)
        step = max(step, math.ceil(minutes * 60 / _MAX_POINTS))
        now = datetime.now(UTC)
        start = now - timedelta(minutes=minutes)
        return _data(
            http.json(
                "/api/v1/query_range",
                query=promql,
                start=int(start.timestamp()),
                end=int(now.timestamp()),
                step=step,
            )
        )

    def alert_rules(args: dict[str, Any]) -> Any:
        text = _string(args, "text", required=False).lower()
        found = rules_of(
            _data(http.json("/api/v1/rules", type="alert")),
            text,
            firing_only=args.get("firing_only") is True,
        )
        return {"rules": found[:100], "count": len(found)}

    def scrape_targets(args: dict[str, Any]) -> Any:
        text = _string(args, "text", required=False).lower()
        found = targets_of(
            _data(http.json("/api/v1/targets", state="active")),
            text,
            down_only=args.get("down_only") is True,
        )
        return {"targets": found[:200], "count": len(found)}

    def probe() -> str:
        _data(http.json("/api/v1/query", query="vector(1)"))
        return "answered a query"

    tools: list[Tool] = []
    if config.metrics:
        tools.append(
            Tool(
                ToolSpec(
                    tool_name("query_metrics", name),
                    f"Run a PromQL range query on the Prometheus source `{name}`, for example"
                    ' rate(node_cpu_seconds_total{mode="idle"}[5m]).'
                    f" Readable metrics: {', '.join(config.metrics)}.",
                    _schema(
                        {
                            "promql": {"type": "string"},
                            "since_minutes": {"type": "integer", "description": "Default 60."},
                            "step_seconds": {"type": "integer"},
                        },
                        ["promql"],
                    ),
                ),
                query_metrics,
            )
        )
    if config.rules:
        tools.append(
            Tool(
                ToolSpec(
                    tool_name("alert_rules", name),
                    f"The alerting rules of the Prometheus source `{name}`: each rule's query,"
                    " its `for`, its state (inactive, pending, firing), whether it evaluates,"
                    " and the alerts it holds now.",
                    _schema(
                        {
                            "text": {"type": "string", "description": "Part of a rule name."},
                            "firing_only": {"type": "boolean"},
                        }
                    ),
                ),
                alert_rules,
            )
        )
    if config.targets:
        tools.append(
            Tool(
                ToolSpec(
                    tool_name("scrape_targets", name),
                    f"The scrape targets of the Prometheus source `{name}`: which are up, and"
                    " the last error of those that are not. A target that is down explains"
                    " missing metrics.",
                    _schema(
                        {
                            "text": {"type": "string", "description": "Part of a job or instance."},
                            "down_only": {"type": "boolean"},
                        }
                    ),
                ),
                scrape_targets,
            )
        )
    return Connected(name, KIND, tools=tools, probe=probe, close=http.close)
