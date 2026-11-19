"""Grafana Loki, read: lines of the streams the rules file names.

Written from Loki's HTTP API reference (grafana.com/docs/loki/latest/reference/loki-http-api).
The model never writes LogQL. It chooses a stream by the name the owner gave it and gives words
to look for; the query is the owner's selector followed by line filters built here, with every
word written as a quoted string. A log query has one selector, so nothing but that stream can
be read. Loki's alerts come from its ruler through Alertmanager.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2 as httpx

from paeon.models import ToolSpec
from paeon.rules import LokiSource
from paeon.sources import Connected, reader, tool_name
from paeon.sources.http import mapping
from paeon.tools import Tool, ToolError, _integer, _schema

KIND = "loki"
_PATHS = ("/loki/api/v1/query_range", "/loki/api/v1/labels")
_MAX_LINE = 2000
_MAX_WORDS = 6


def words(args: dict[str, Any], name: str) -> list[str]:
    """A short list of non-empty strings: what to look for, or what to leave out."""
    value = args.get(name) or []
    if (
        not isinstance(value, list)
        or len(value) > _MAX_WORDS
        or not all(isinstance(word, str) and word.strip() and len(word) <= 200 for word in value)
    ):
        raise ToolError(f"{name} must be a list of at most {_MAX_WORDS} short strings")
    return [word for word in value]


def quoted(word: str) -> str:
    """A string as LogQL and LogsQL read it: double quotes, with Go's escapes."""
    return json.dumps(word, ensure_ascii=False)


def logql(selector: str, contains: list[str], excludes: list[str]) -> str:
    filters = [f"|= {quoted(word)}" for word in contains] + [
        f"!= {quoted(word)}" for word in excludes
    ]
    return " ".join([selector.strip(), *filters])


def lines_of(data: Any, limit: int) -> list[dict[str, Any]]:
    """The lines of a `streams` result, newest first, each with the labels of its stream."""
    found: list[tuple[int, dict[str, Any]]] = []
    result = data.get("result") if isinstance(data, dict) else None
    for stream in result if isinstance(result, list) else []:
        if not isinstance(stream, dict):
            continue
        labels = mapping(stream.get("stream"))
        for value in stream.get("values") or []:
            if not isinstance(value, list) or len(value) < 2 or not str(value[0]).isdigit():
                continue
            nanos = int(value[0])
            found.append(
                (
                    nanos,
                    {
                        "time": datetime.fromtimestamp(nanos / 1e9, UTC).isoformat(),
                        "line": str(value[1])[:_MAX_LINE],
                        "labels": labels,
                    },
                )
            )
    found.sort(key=lambda item: item[0], reverse=True)
    return [line for _, line in found[:limit]]


def connect(
    name: str,
    config: LokiSource,
    secrets: Mapping[str, str],
    transport: httpx.BaseTransport | None = None,
) -> Connected:
    http = reader(KIND, config, secrets, _PATHS, transport=transport)

    def search_logs(args: dict[str, Any]) -> Any:
        stream = args.get("stream")
        if not isinstance(stream, str) or stream not in config.streams:
            raise ToolError(f"stream {stream!r} is not readable; readable: {list(config.streams)}")
        minutes = _integer(args, "since_minutes", 60, low=1, high=7 * 24 * 60)
        limit = _integer(args, "limit", 50, low=1, high=config.max_lines)
        now = datetime.now(UTC)
        answer = http.json(
            "/loki/api/v1/query_range",
            query=logql(config.streams[stream], words(args, "contains"), words(args, "excludes")),
            start=int((now - timedelta(minutes=minutes)).timestamp() * 1e9),
            end=int(now.timestamp() * 1e9),
            limit=limit,
            direction="backward",
        )
        if not isinstance(answer, dict) or answer.get("status") != "success":
            raise ToolError("loki refused the query")
        found = lines_of(answer.get("data"), limit)
        return {"lines": found, "count": len(found)}

    def probe() -> str:
        answer = http.json("/loki/api/v1/labels")
        labels = answer.get("data") if isinstance(answer, dict) else None
        return f"{len(labels) if isinstance(labels, list) else 0} labels"

    tools: list[Tool] = []
    if config.streams:
        tools.append(
            Tool(
                ToolSpec(
                    tool_name("search_logs", name),
                    f"Search one log stream of the Loki source `{name}`, newest lines first."
                    " `contains` lists strings a line must hold (all of them), `excludes`"
                    " strings it must not; both are matched as written, case included."
                    f" Readable streams: {', '.join(config.streams)}.",
                    _schema(
                        {
                            "stream": {"type": "string", "enum": list(config.streams)},
                            "contains": {"type": "array", "items": {"type": "string"}},
                            "excludes": {"type": "array", "items": {"type": "string"}},
                            "since_minutes": {"type": "integer", "description": "Default 60."},
                            "limit": {"type": "integer", "description": "Default 50."},
                        },
                        ["stream"],
                    ),
                ),
                search_logs,
            )
        )
    return Connected(name, KIND, tools=tools, probe=probe, close=http.close)
