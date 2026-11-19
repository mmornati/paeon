"""VictoriaLogs, read: entries of the streams the rules file names.

Written from its querying documentation (docs.victoriametrics.com/victorialogs/querying):
`/select/logsql/query` answers one JSON object per line. As with Loki, the model chooses a
stream by name and gives words; the query is the owner's filter in parentheses, then each word
as a quoted phrase. A start time and a limit are always sent: without a start, VictoriaLogs
scans everything it keeps. It has no alerting of its own (vmalert and Alertmanager do that).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2 as httpx

from paeon.models import ToolSpec
from paeon.rules import VictoriaLogsSource
from paeon.sources import Connected, reader, tool_name
from paeon.sources.loki import quoted, words
from paeon.tools import Tool, ToolError, _integer, _schema

KIND = "victorialogs"
_PATHS = ("/select/logsql/query", "/health")
_MAX_VALUE = 2000
_MAX_FIELDS = 30


def logsql(stream: str, contains: list[str], excludes: list[str]) -> str:
    filters = [quoted(word) for word in contains] + [f"!{quoted(word)}" for word in excludes]
    return " ".join([f"({stream.strip()})", *filters])


def entries_of(text: str, limit: int) -> list[dict[str, Any]]:
    """The entries of a JSON-lines answer. A line that is not an object is skipped."""
    found: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        fields = {
            key: str(value)[:_MAX_VALUE]
            for key, value in list(entry.items())[:_MAX_FIELDS]
            if key not in ("_time", "_msg", "_stream", "_stream_id")
        }
        found.append(
            {
                "time": entry.get("_time"),
                "line": str(entry.get("_msg") or "")[:_MAX_VALUE],
                "stream": entry.get("_stream"),
                "fields": fields,
            }
        )
        if len(found) == limit:
            break
    return found


def connect(
    name: str,
    config: VictoriaLogsSource,
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
        answer = http.get(
            "/select/logsql/query",
            query=logsql(config.streams[stream], words(args, "contains"), words(args, "excludes")),
            start=(now - timedelta(minutes=minutes)).isoformat(),
            end=now.isoformat(),
            limit=limit,
        )
        found = entries_of(answer.text, limit)
        found.sort(key=lambda entry: str(entry["time"]), reverse=True)
        return {"lines": found, "count": len(found)}

    def probe() -> str:
        http.get("/health")
        return "healthy"

    tools: list[Tool] = []
    if config.streams:
        tools.append(
            Tool(
                ToolSpec(
                    tool_name("search_logs", name),
                    f"Search one log stream of the VictoriaLogs source `{name}`, newest entries"
                    " first. `contains` lists words or phrases an entry must hold (all of"
                    " them), `excludes` those it must not."
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
