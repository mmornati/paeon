"""The investigator's tools. Every one of them reads; none can change anything.

Three rules hold for all of them (design §4):

- an allow-list decides what is readable (log streams, containers whose logs may be read, mount
  sources, labels): what is not listed does not exist for the model;
- arguments are validated here, never passed through: no free SQL, no path, no URL;
- results are redacted, cut, and handed back as data. They contain text written by third
  parties (a log line, a container name), and the prompt says so.

The credentials behind them should be read-only as well (an OpenObserve account that can only
search, a Docker socket proxy limited to GET): the checks here are the second net, not the first.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx2 as httpx

from paeon import knowledge, proc
from paeon.models import ToolSpec
from paeon.redact import redact
from paeon.rules import DockerTools, GitTools, OpenObserveTools, SshTools
from paeon.schema import IncidentStatus, to_micros
from paeon.sources.openobserve import AlertDefinition
from paeon.store import Store


class ToolError(Exception):
    """Shown to the model as the tool's error. Must not carry a URL or a credential."""


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    run: Callable[[dict[str, Any]], Any]


def _schema(properties: dict[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _integer(args: dict[str, Any], name: str, default: int, *, low: int, high: int) -> int:
    value = args.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"{name} must be an integer")
    return max(low, min(high, value))


def _string(args: dict[str, Any], name: str, *, required: bool = True) -> str:
    value = args.get(name, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise ToolError(f"{name} must be a non-empty string")
    return value.strip()


class Toolbox:
    def __init__(self, tools: Sequence[Tool], *, max_result_chars: int = 12000) -> None:
        self._tools = {tool.spec.name: tool for tool in tools}
        self._max = max_result_chars

    @property
    def specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def call(self, name: str, args: dict[str, Any]) -> tuple[Any, bool]:
        """Run a tool. Returns its redacted, bounded result and whether it is an error."""
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"unknown tool {name!r}"}, True
        try:
            result = redact(tool.run(args), max_length=4000)
        except ToolError as exc:
            return {"error": str(exc)}, True
        except Exception as exc:
            # The message of an unexpected error may quote a URL or a payload: keep its kind.
            return {"error": f"the tool failed: {type(exc).__name__}"}, True
        text = json.dumps(result, default=str)
        if len(text) > self._max:
            return {
                "truncated": f"the result was {len(text)} characters; narrow the request",
                "start": text[: self._max],
            }, False
        return result, False


# --- Paeon's own record ---------------------------------------------------------------------------


def paeon_tools(store: Store, knowledge_paths: Sequence[Path | str]) -> list[Tool]:
    def open_incidents(args: dict[str, Any]) -> Any:
        return [
            {
                "id": incident.id,
                "title": incident.title,
                "host": incident.host,
                "severity": incident.severity.value,
                "opened_at": incident.opened_at.isoformat(),
                "flapping": incident.flapping,
                "alerts": [
                    {"source": a.source, "title": a.title, "status": a.status.value}
                    for a in incident.alerts
                ],
            }
            for incident in store.incidents(status=IncidentStatus.OPEN)
        ]

    def past_incidents(args: dict[str, Any]) -> Any:
        text = _string(args, "text", required=False).lower()
        host = _string(args, "host", required=False)
        days = _integer(args, "days", 30, low=1, high=365)
        limit = _integer(args, "limit", 10, low=1, high=30)
        since = datetime.now(UTC) - timedelta(days=days)
        found = []
        for incident in reversed(store.incidents(active_since=since)):
            titles = " ".join([incident.title, *(a.title for a in incident.alerts)]).lower()
            if (text and text not in titles) or (host and incident.host != host):
                continue
            entry: dict[str, Any] = {
                "id": incident.id,
                "title": incident.title,
                "host": incident.host,
                "status": incident.status.value,
                "opened_at": incident.opened_at.isoformat(),
                "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
                "flapping": incident.flapping,
                "alerts": [a.title for a in incident.alerts],
            }
            for kind, key in (
                ("triage.verdict", "triage"),
                ("investigation.completed", "investigation"),
                ("feedback.recorded", "feedback"),
            ):
                for event in store.events(
                    incident_id=incident.id, kind=kind, newest_first=True, limit=1
                ):
                    if kind == "investigation.completed":
                        findings = event.payload.get("findings") or {}
                        entry[key] = {
                            "summary": findings.get("summary"),
                            "root_cause": findings.get("root_cause"),
                        }
                    elif kind == "triage.verdict":
                        entry[key] = {
                            "verdict": event.payload.get("effective_verdict"),
                            "reason": event.payload.get("reason"),
                        }
                    else:
                        entry[key] = event.payload.get("vote")
            found.append(entry)
            if len(found) >= limit:
                break
        return found

    def recent_changes(args: dict[str, Any]) -> Any:
        hours = _integer(args, "hours", 6, low=1, high=168)
        since = datetime.now(UTC) - timedelta(hours=hours)
        return [
            event.payload
            for event in store.events(kind="change.recorded", recorded_since=since, limit=100)
        ]

    def search_knowledge(args: dict[str, Any]) -> Any:
        query = _string(args, "query")
        hits = knowledge.search(knowledge_paths, query, limit=5)
        return [
            {"path": h.path, "title": h.title, "section": h.heading, "excerpt": h.excerpt}
            | ({"unreviewed_draft": True} if h.draft else {})
            for h in hits
        ]

    def alert_history(args: dict[str, Any]) -> Any:
        text = _string(args, "text").lower()
        days = _integer(args, "days", 7, low=1, high=90)
        since = datetime.now(UTC) - timedelta(days=days)
        found: dict[tuple[str, str], dict[str, Any]] = {}
        for event in store.events(
            kinds=("alert.firing", "alert.resolved"), recorded_since=since, newest_first=True
        ):
            title = str(event.payload.get("title") or "")
            if text not in title.lower():
                continue
            key = (str(event.source), str(event.fingerprint))
            if key not in found and len(found) >= 10:
                continue
            entry = found.setdefault(
                key, {"source": event.source, "title": title, "changes": [], "firings": 0}
            )
            entry["firings"] += int(event.kind == "alert.firing")
            if len(entry["changes"]) < 20:
                entry["changes"].append(
                    {
                        "at": event.payload.get("observed_at"),
                        "status": event.kind.removeprefix("alert."),
                    }
                )
        for (source, fingerprint), entry in found.items():
            state = store.alert_state(source, fingerprint)
            entry["now"] = None if state is None else state.status
        return list(found.values())

    return [
        Tool(
            ToolSpec(
                "open_incidents",
                "List the incidents that are open right now, with their alerts. Use it to see"
                " what else is failing at the same time.",
                _schema({}),
            ),
            open_incidents,
        ),
        Tool(
            ToolSpec(
                "past_incidents",
                "Search earlier incidents by a text in their alert titles and/or by host. Returns"
                " when each happened, how it ended, and what triage and investigation concluded.",
                _schema(
                    {
                        "text": {"type": "string", "description": "Part of an alert title."},
                        "host": {"type": "string"},
                        "days": {"type": "integer", "description": "How far back; default 30."},
                        "limit": {"type": "integer"},
                    }
                ),
            ),
            past_incidents,
        ),
        Tool(
            ToolSpec(
                "recent_changes",
                "The change calendar: deploys, update runs, backups and restarts that were"
                " announced in the last hours.",
                _schema({"hours": {"type": "integer", "description": "Default 6, at most 168."}}),
            ),
            recent_changes,
        ),
        Tool(
            ToolSpec(
                "search_knowledge",
                "Keyword search in the postmortems and runbooks of this server. Returns excerpts."
                " One marked `unreviewed_draft` was written by a model and read by nobody:"
                " a lead to check, not a fact.",
                _schema({"query": {"type": "string"}}, ["query"]),
            ),
            search_knowledge,
        ),
        Tool(
            ToolSpec(
                "alert_history",
                "When the alerts and monitors whose title contains a text fired and recovered"
                " in the last days, newest first, and whether each is firing now. Use it for"
                " the history of an uptime-kuma monitor or of any other alert.",
                _schema(
                    {
                        "text": {"type": "string", "description": "Part of the title."},
                        "days": {"type": "integer", "description": "Default 7, at most 90."},
                    },
                    ["text"],
                ),
            ),
            alert_history,
        ),
    ]


# --- OpenObserve ----------------------------------------------------------------------------------


class Telemetry(Protocol):
    def search(self, sql: str, start: int, end: int, size: int) -> list[dict[str, Any]]: ...

    def query_range(self, promql: str, start: int, end: int, step: int) -> Any: ...


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A filter is one boolean expression on the stream's own columns: no second statement, no
# sub-query (which could read another stream), no comment to hide either.
_FORBIDDEN = re.compile(r";|--|/\*|\b(select|union|join|from|into)\b", re.IGNORECASE)


_PROMQL_STRING = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`[^`]*`')
_PROMQL_GROUPING = re.compile(
    r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^()]*\)", re.IGNORECASE
)
_PROMQL_NAME = re.compile(r"(?<![\w:.])([A-Za-z_:][\w:]*)(\s*\()?")
# Operators and modifiers: never a metric, wherever they stand.
_PROMQL_WORDS = frozenset(
    {"and", "or", "unless", "atan2", "offset", "bool", "inf", "nan", "start", "end",
     "by", "without", "on", "ignoring", "group_left", "group_right"}
)  # fmt: skip
# Aggregations are functions in every valid use; before a `{` they cannot be a metric either.
_PROMQL_RESERVED = _PROMQL_WORDS | frozenset(
    {"sum", "min", "max", "avg", "group", "stddev", "stdvar", "count", "count_values",
     "bottomk", "topk", "quantile", "limitk", "limit_ratio"}
)  # fmt: skip


_PROMQL_SELECTOR = re.compile(r"([A-Za-z_:][\w:]*)?\s*\{([^{}]*)\}")
_STRING_MARK = "\x00"


def metric_names(promql: str) -> set[str]:
    """Every metric a PromQL expression reads. Raises ToolError when that cannot be told.

    This is not a PromQL parser, so it refuses whatever it cannot account for instead of
    guessing: comments and line breaks, a selector by `__name__`, a label matcher that is not
    attached to a metric name (`{job="x"}` alone selects every metric), a quoted metric name
    inside braces. Strings, matchers, durations and grouping clauses are then removed; of what
    is left, a word followed by `(` is a function and anything else is a metric.
    """
    if "\n" in promql or "\r" in promql or _STRING_MARK in promql:
        raise ToolError("the query must be on one line")
    if "__name__" in promql:
        raise ToolError("selecting by __name__ is not allowed; name the metric")
    text = _PROMQL_STRING.sub(_STRING_MARK, promql)
    if any(quote in text for quote in "\"'`"):
        raise ToolError("the query has an unterminated string")
    if "#" in text:
        raise ToolError("comments are not allowed in the query")
    for selector in _PROMQL_SELECTOR.finditer(text):
        name, matchers = selector.group(1), selector.group(2)
        if name is None or name.lower() in _PROMQL_RESERVED:
            raise ToolError("every {...} matcher must follow a metric name")
        if matchers.strip().startswith(_STRING_MARK):
            raise ToolError("name the metric before the braces, not inside them")
    text = _PROMQL_SELECTOR.sub(lambda m: f" {m.group(1)} ", text)
    text = re.sub(r"\[[^\[\]]*\]", " ", text).replace(_STRING_MARK, " ")
    if any(bracket in text for bracket in "{}[]"):
        raise ToolError("the query has unbalanced braces or brackets")
    text = _PROMQL_GROUPING.sub(" ", text)
    return {
        match.group(1)
        for match in _PROMQL_NAME.finditer(text)
        if not match.group(2) and match.group(1).lower() not in _PROMQL_WORDS
    }


_SQL_TOKEN = re.compile(
    r"""\s+
      | (?P<string>'(?:[^']|'')*')
      | (?P<quoted>"[^"]*")
      | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<number>[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)
      | (?P<mark>.)""",
    re.VERBOSE | re.DOTALL,
)
_SQL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SQL_REFUSED = frozenset(";\\`[]{}#$@'\"")  # as a mark: outside any string or identifier
_SQL_CLAUSE_END = frozenset(
    {"where", "group", "order", "limit", "having", "union", "intersect", "except", "offset"}
)


def _sql_tokens(sql: str) -> list[tuple[str, str]] | None:
    """A query as (kind, text) tokens, read once from left to right, or None if it has a
    comment, a backslash, or anything else this reader does not account for.

    Strings and quoted identifiers are told apart in the same scan, so a quote inside one can
    never open the other."""
    if "--" in sql or "/*" in sql or "\\" in sql:
        return None
    tokens: list[tuple[str, str]] = []
    for match in _SQL_TOKEN.finditer(sql):
        kind = match.lastgroup
        if kind is None:  # whitespace
            continue
        text = match.group(kind)
        if kind == "mark" and (text in _SQL_REFUSED or not text.isascii()):
            return None  # includes a quote that opens nothing: an unterminated string
        if kind == "number" and sql[match.end() : match.end() + 1].isidentifier():
            return None  # `1from x`: where the number ends is the engine's call, not ours
        if kind == "quoted":
            text = text[1:-1]
            if _SQL_NAME.fullmatch(text) is None:
                return None
        tokens.append((kind, text.lower() if kind == "word" else text))
    return tokens


def sql_streams(sql: str) -> set[str] | None:
    """Every stream a SQL query reads, or None when that cannot be told for certain.

    Not a SQL parser, so it refuses what it cannot account for. Every `from` and `join` must be
    followed by a sub-query (whose own tables are found in turn) or by one plain table name,
    and that name must not go on as `schema.table`, a function call or anything else. A comma
    in a from clause (a comma join) is refused. `extract(x from y)` makes `y` a table, which
    the allow-list then refuses: a query that is not re-run costs a tool call, one that reads
    an unlisted stream leaks it.
    """
    tokens = _sql_tokens(sql)
    if tokens is None:
        return None
    tables: set[str] = set()
    depth = 0
    from_at: set[int] = set()  # paren depths at which a from clause is open
    for index, (kind, text) in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else ("end", "")
        if kind == "word" and index and tokens[index - 1] == ("mark", "."):
            # `t.where`: a column, whatever it is called. Only after a real qualifier, though:
            # in `1.from x` the engine reads the number `1.` and then a from clause.
            qualifier = tokens[index - 2][0] if index >= 2 else "start"
            if text in ("from", "join") or qualifier not in ("word", "quoted"):
                return None
            continue
        if kind == "mark" and text == "(":
            depth += 1
        elif kind == "mark" and text == ")":
            from_at.discard(depth)
            depth -= 1
            if depth < 0:
                return None
        elif kind == "mark" and text == "," and depth in from_at:
            return None
        elif kind == "word" and text in _SQL_CLAUSE_END:
            from_at.discard(depth)
        elif kind == "word" and text in ("from", "join"):
            from_at.add(depth)
            if following == ("mark", "("):
                continue
            after = tokens[index + 2] if index + 2 < len(tokens) else ("end", "")
            if following[0] not in ("word", "quoted") or after[0] in ("quoted", "string"):
                return None
            if after[0] == "mark" and after[1] in ".(:":
                return None
            tables.add(following[1])
    return tables if tables and depth == 0 else None


def openobserve_tools(
    client: Telemetry,
    config: OpenObserveTools,
    definitions: Callable[[str], AlertDefinition | None] | None = None,
) -> list[Tool]:
    def readable_metrics(promql: str) -> bool:
        names = metric_names(promql)
        return bool(names) and all(
            any(fnmatch.fnmatchcase(name, pattern) for pattern in config.metrics) for name in names
        )

    def alert_query(args: dict[str, Any]) -> Any:
        """The alert's own definition and, where its data is readable, its query re-run."""
        if definitions is None:  # pragma: no cover - the tool is not offered without them
            raise ToolError("alert definitions are not available")
        key = _string(args, "alert")
        try:
            definition = definitions(key)
        except Exception as exc:
            raise ToolError(f"the alert definitions were not read: {type(exc).__name__}") from None
        if definition is None:
            raise ToolError("no alert with this name or id")
        minutes = _integer(args, "since_minutes", 120, low=1, high=7 * 24 * 60)
        now = datetime.now(UTC)
        start = now - timedelta(minutes=minutes)
        answer: dict[str, Any] = {"definition": definition.as_dict()}
        # The query was written by the operator, but what it returns goes to the model: the
        # same allow-lists decide, on every stream and every metric the query names.
        if definition.kind == "promql" and definition.promql:
            try:
                allowed = readable_metrics(definition.promql)
            except ToolError:
                allowed = False
            if not allowed:
                answer["not_run"] = "the query reads a metric that is not on the allow-list"
            else:
                step = max(minutes * 60 // 60, 15)
                answer["result"] = client.query_range(
                    definition.promql, int(start.timestamp()), int(now.timestamp()), step
                )
        elif definition.kind == "sql" and definition.sql and definition.stream_type == "logs":
            streams = sql_streams(definition.sql)
            if streams is None:
                answer["not_run"] = (
                    "the streams this query reads cannot be told for certain;"
                    " use search_logs on its stream"
                )
            elif not streams | {definition.stream} <= set(config.streams):
                unlisted = sorted((streams | {definition.stream}) - set(config.streams))
                answer["not_run"] = f"the query reads {unlisted}, which is not on the allow-list"
            else:
                rows = client.search(definition.sql, to_micros(start), to_micros(now), 50)
                answer["result"] = {"rows": rows[:50], "count": len(rows[:50])}
        else:
            answer["not_run"] = (
                "this alert has no query to re-run (conditions, or a burn rate on an SLO);"
                " use search_logs or query_metrics on what it watches"
            )
        return answer

    def search_logs(args: dict[str, Any]) -> Any:
        stream = _string(args, "stream")
        if stream not in config.streams:
            raise ToolError(f"stream {stream!r} is not readable; readable: {list(config.streams)}")
        where = _string(args, "where", required=False)
        if _FORBIDDEN.search(where):
            raise ToolError("where must be one filter expression on this stream's columns")
        columns = args.get("columns") or []
        if not isinstance(columns, list) or not all(
            isinstance(c, str) and _IDENTIFIER.fullmatch(c) for c in columns
        ):
            raise ToolError("columns must be a list of column names")
        minutes = _integer(args, "since_minutes", 60, low=1, high=7 * 24 * 60)
        limit = _integer(args, "limit", 50, low=1, high=config.max_rows)
        selected = ", ".join(columns) if columns else "*"
        # `stream` is one of the configured names and `columns` are identifiers.
        sql = f'SELECT {selected} FROM "{stream}"'  # noqa: S608
        if where:
            sql += f" WHERE {where}"
        sql += " ORDER BY _timestamp DESC"
        now = datetime.now(UTC)
        rows = client.search(
            sql, to_micros(now - timedelta(minutes=minutes)), to_micros(now), limit
        )
        return {"rows": rows[:limit], "count": len(rows[:limit])}

    def query_metrics(args: dict[str, Any]) -> Any:
        promql = _string(args, "promql")
        names = metric_names(promql)
        refused = sorted(
            name
            for name in names
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in config.metrics)
        )
        if refused or not names:
            raise ToolError(
                f"not readable: {refused or 'no metric name found'};"
                f" readable metrics: {list(config.metrics)}"
            )
        minutes = _integer(args, "since_minutes", 60, low=1, high=7 * 24 * 60)
        step = _integer(args, "step_seconds", max(minutes * 60 // 60, 15), low=15, high=3600)
        now = datetime.now(UTC)
        return client.query_range(
            promql, int((now - timedelta(minutes=minutes)).timestamp()), int(now.timestamp()), step
        )

    tools = []
    if config.streams:
        tools.append(
            Tool(
                ToolSpec(
                    "search_logs",
                    "Search one log stream, newest rows first. `where` is a SQL filter on the"
                    " stream's columns, for example: level = 'error' AND str_match(log, 'timeout')."
                    f" Readable streams: {', '.join(config.streams)}.",
                    _schema(
                        {
                            "stream": {"type": "string", "enum": list(config.streams)},
                            "where": {"type": "string"},
                            "columns": {"type": "array", "items": {"type": "string"}},
                            "since_minutes": {"type": "integer", "description": "Default 60."},
                            "limit": {"type": "integer", "description": "Default 50."},
                        },
                        ["stream"],
                    ),
                ),
                search_logs,
            )
        )
    if config.metrics:
        tools.append(
            Tool(
                ToolSpec(
                    "query_metrics",
                    "Run a PromQL range query on the metrics, for example"
                    ' rate(container_cpu_usage_seconds_total{name="api"}[5m]).',
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
    if definitions is not None:
        tools.append(
            Tool(
                ToolSpec(
                    "alert_query",
                    "What an OpenObserve alert watches (its query, threshold and period) and,"
                    " when the data it reads is on the allow-list, the result of that query"
                    " over a wider window. `alert` is the alert's name or its id.",
                    _schema(
                        {
                            "alert": {"type": "string"},
                            "since_minutes": {"type": "integer", "description": "Default 120."},
                        },
                        ["alert"],
                    ),
                ),
                alert_query,
            )
        )
    return tools


# --- Docker ---------------------------------------------------------------------------------------

_CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def _under(source: str, roots: Sequence[str]) -> bool:
    """Whether a mount source is one of the roots or below it, compared on whole path components."""
    return any(
        source == root.rstrip("/") or source.startswith(root.rstrip("/") + "/") for root in roots
    )


def demux_logs(raw: bytes) -> str:
    """Docker log bytes as text. Without a TTY the stream is framed: 8-byte header, payload."""
    if len(raw) >= 8 and raw[0] in (0, 1, 2) and raw[1:4] == b"\x00\x00\x00":
        chunks = []
        offset = 0
        while offset + 8 <= len(raw):
            size = int.from_bytes(raw[offset + 4 : offset + 8], "big")
            chunks.append(raw[offset + 8 : offset + 8 + size])
            offset += 8 + size
        raw = b"".join(chunks)
    return raw.decode("utf-8", errors="replace")


def docker_tools(http: httpx.Client, config: DockerTools) -> list[Tool]:
    def get(path: str, **params: Any) -> httpx.Response:
        try:
            response = http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise ToolError(f"docker is unreachable: {type(exc).__name__}") from None
        if response.status_code == 404:
            raise ToolError("no such container")
        if response.status_code >= 400:
            raise ToolError(f"docker answered HTTP {response.status_code}")
        return response

    def containers() -> list[dict[str, Any]]:
        return [e for e in get("/containers/json", all="true").json() if isinstance(e, dict)]

    def names_of(entry: dict[str, Any]) -> list[str]:
        return [str(name).lstrip("/") for name in entry.get("Names") or []]

    def resolve(args: dict[str, Any]) -> tuple[str, str]:
        """The canonical name and full id of the container an argument names.

        Docker also accepts an id or an id prefix where a name is expected. The argument is
        therefore never passed to it: it must equal a container's name, the allow-lists are
        checked against that name, and the request is made with the container's id.
        """
        name = _string(args, "name")
        if not _CONTAINER.fullmatch(name):
            raise ToolError("name must be a container name")
        for entry in containers():
            container_id = entry.get("Id")
            if name in names_of(entry) and isinstance(container_id, str) and container_id:
                if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
                    raise ToolError("docker returned an unexpected container id")
                return name, container_id
        raise ToolError("no such container; use list_containers for the names")

    def logs_readable(name: str) -> bool:
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in config.logs)

    def list_containers(args: dict[str, Any]) -> Any:
        return [
            {
                "name": (names_of(entry) or ["?"])[0],
                "image": entry.get("Image"),
                "state": entry.get("State"),
                "status": entry.get("Status"),
            }
            for entry in containers()
        ]

    def inspect_container(args: dict[str, Any]) -> Any:
        name, container_id = resolve(args)
        data = get(f"/containers/{container_id}/json").json()
        state = data.get("State") or {}
        host = data.get("HostConfig") or {}
        container = data.get("Config") or {}
        health = state.get("Health") or {}
        last_check = (health.get("Log") or [{}])[-1]
        labels = container.get("Labels") or {}
        # A projection: the environment, the command line and unlisted labels hold credentials.
        return {
            "name": name,
            "image": container.get("Image"),
            "created": data.get("Created"),
            "restart_count": data.get("RestartCount"),
            "state": {
                key.lower(): state.get(key)
                for key in (
                    "Status",
                    "Running",
                    "Restarting",
                    "OOMKilled",
                    "ExitCode",
                    "Error",
                    "StartedAt",
                    "FinishedAt",
                )
            },
            "health": {
                "status": health.get("Status"),
                "failing_streak": health.get("FailingStreak"),
                # The check's output is written by the container, like its logs: same gate.
                "last_output": last_check.get("Output") if logs_readable(name) else "[not shown]",
            },
            "restart_policy": (host.get("RestartPolicy") or {}).get("Name"),
            "memory_limit": host.get("Memory"),
            "networks": {
                network: {"ip": net.get("IPAddress"), "aliases": net.get("Aliases")}
                for network, net in (
                    (data.get("NetworkSettings") or {}).get("Networks") or {}
                ).items()
            },
            "mounts": [
                {
                    "destination": mount.get("Destination"),
                    "type": mount.get("Type"),
                    "read_write": mount.get("RW"),
                    "source": mount.get("Source")
                    if _under(str(mount.get("Source", "")), config.mount_sources)
                    else "[not shown]",
                }
                for mount in data.get("Mounts") or []
            ],
            "labels": {key: labels[key] for key in config.labels if key in labels},
        }

    def container_logs(args: dict[str, Any]) -> Any:
        name, container_id = resolve(args)
        if not logs_readable(name):
            raise ToolError(f"the logs of {name!r} are not readable")
        tail = _integer(args, "tail", 100, low=1, high=config.max_log_lines)
        minutes = _integer(args, "since_minutes", 0, low=0, high=7 * 24 * 60)
        params: dict[str, Any] = {"stdout": "true", "stderr": "true", "timestamps": "true"}
        params["tail"] = str(tail)
        if minutes:
            params["since"] = str(int((datetime.now(UTC) - timedelta(minutes=minutes)).timestamp()))
        lines = demux_logs(get(f"/containers/{container_id}/logs", **params).content).splitlines()
        return {"lines": lines[-tail:]}

    readable = ", ".join(config.logs) or "none"
    return [
        Tool(
            ToolSpec(
                "list_containers",
                "List every container with its image, state and status (like `docker ps -a`).",
                _schema({}),
            ),
            list_containers,
        ),
        Tool(
            ToolSpec(
                "inspect_container",
                "State of one container: running or not, exit code, OOM kill, restart count,"
                " health check, networks, mounts. The environment is never returned. Use the"
                " exact name from list_containers.",
                _schema({"name": {"type": "string"}}, ["name"]),
            ),
            inspect_container,
        ),
        Tool(
            ToolSpec(
                "container_logs",
                f"The last log lines of a container. Readable containers: {readable}.",
                _schema(
                    {
                        "name": {"type": "string"},
                        "tail": {"type": "integer", "description": "Lines; default 100."},
                        "since_minutes": {"type": "integer"},
                    },
                    ["name"],
                ),
            ),
            container_logs,
        ),
    ]


# --- uptime-kuma ----------------------------------------------------------------------------------

_METRIC_LINE = re.compile(r"^(monitor_[a-z_]+)\{(.*)\}\s+(-?[0-9.eE+]+|NaN)$")
_METRIC_LABEL = re.compile(r'([a-z_]+)="((?:[^"\\]|\\.)*)"')
_MONITOR_STATES = {0: "down", 1: "up", 2: "pending", 3: "maintenance"}


def parse_monitors(text: str) -> list[dict[str, Any]]:
    """uptime-kuma's `/metrics` as one entry per monitor. The monitored URL is left out."""
    monitors: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        match = _METRIC_LINE.match(line.strip())
        if match is None:
            continue
        labels = dict(_METRIC_LABEL.findall(match.group(2)))
        name = labels.get("monitor_name")
        if not name or match.group(3) == "NaN":
            continue
        entry = monitors.setdefault(name, {"name": name, "type": labels.get("monitor_type")})
        value = float(match.group(3))
        if match.group(1) == "monitor_status":
            entry["status"] = _MONITOR_STATES.get(int(value), "unknown")
        elif match.group(1) == "monitor_response_time":
            entry["response_ms"] = value
        elif match.group(1) == "monitor_cert_days_remaining":
            entry["certificate_days_left"] = value
    return sorted(monitors.values(), key=lambda entry: str(entry["name"]))


def uptime_kuma_tools(http: httpx.Client, api_key: str | None) -> list[Tool]:
    def monitor_status(args: dict[str, Any]) -> Any:
        text = _string(args, "text", required=False).lower()
        try:
            response = http.get("/metrics", auth=("", api_key) if api_key else None)
        except httpx.HTTPError as exc:
            raise ToolError(f"uptime-kuma is unreachable: {type(exc).__name__}") from None
        if response.status_code >= 400:
            raise ToolError(f"uptime-kuma answered HTTP {response.status_code}")
        monitors = [m for m in parse_monitors(response.text) if text in str(m["name"]).lower()]
        return {"monitors": monitors[:200], "count": len(monitors)}

    return [
        Tool(
            ToolSpec(
                "monitor_status",
                "The state of the uptime-kuma monitors right now: up, down, pending or in"
                " maintenance, with the last response time. Use alert_history for the past.",
                _schema({"text": {"type": "string", "description": "Part of a monitor name."}}),
            ),
            monitor_status,
        )
    ]


# --- git and other hosts --------------------------------------------------------------------------

_COMMIT = "\x1e%h\x1f%cI\x1f%s"


def git_tools(config: GitTools, run: proc.Runner = proc.run) -> list[Tool]:
    def recent_commits(args: dict[str, Any]) -> Any:
        repo = _string(args, "repo")
        if repo not in config.repos:
            raise ToolError(f"repo {repo!r} is not readable; readable: {sorted(config.repos)}")
        hours = _integer(args, "since_hours", 48, low=1, high=24 * 90)
        limit = _integer(args, "limit", 15, low=1, high=config.max_commits)
        argv = [
            *proc.GIT, "-C", config.repos[repo], "log", f"--since={hours} hours ago",
            f"--max-count={limit}", f"--pretty=format:{_COMMIT}", "--name-only", "--no-color",
        ]  # fmt: skip
        try:
            code, output = run(argv, timeout=20, env=proc.GIT_ENV)
        except Exception as exc:
            raise ToolError(f"git could not be run: {type(exc).__name__}") from None
        if code != 0:
            raise ToolError("git could not read this repository")
        commits = []
        for block in output.split("\x1e")[1:]:
            head, _, files = block.partition("\n")
            parts = head.split("\x1f")
            if len(parts) != 3:
                continue
            names = [line for line in files.splitlines() if line.strip()]
            commits.append(
                {"commit": parts[0], "at": parts[1], "subject": parts[2], "files": names[:40]}
            )
        return {"commits": commits, "count": len(commits)}

    if not config.repos:
        return []
    return [
        Tool(
            ToolSpec(
                "recent_commits",
                "The last commits of a repository of this server (its configuration, for"
                " example): when, the subject line and the files changed. Authors are not"
                f" returned. Readable repositories: {', '.join(sorted(config.repos))}.",
                _schema(
                    {
                        "repo": {"type": "string", "enum": sorted(config.repos)},
                        "since_hours": {"type": "integer", "description": "Default 48."},
                        "limit": {"type": "integer"},
                    },
                    ["repo"],
                ),
            ),
            recent_commits,
        )
    ]


def ssh_tools(config: SshTools, run: proc.Runner = proc.run) -> list[Tool]:
    def host_command(args: dict[str, Any]) -> Any:
        host = _string(args, "host")
        name = _string(args, "command")
        entry = config.hosts.get(host)
        if entry is None or name not in entry.commands:
            raise ToolError(
                "unknown host or command; available: "
                + json.dumps({h: sorted(e.commands) for h, e in config.hosts.items()})
            )
        # The command line is the operator's, word for word: the model only chose its name.
        argv = [
            *proc.ssh_command(config.identity_file, config.known_hosts_file),
            "-p", str(entry.port), "--", entry.target, entry.commands[name],
        ]  # fmt: skip
        try:
            code, output = run(argv, timeout=config.timeout_seconds)
        except Exception as exc:
            raise ToolError(f"the host was not reached: {type(exc).__name__}") from None
        return {"exit_code": code, "lines": output.splitlines()[-200:]}

    if not config.hosts:
        return []
    listing = "; ".join(
        f"{host}: {', '.join(sorted(entry.commands))}" for host, entry in config.hosts.items()
    )
    return [
        Tool(
            ToolSpec(
                "host_command",
                "Run one of the read-only commands the owner prepared on another host, and"
                f" return its output. You choose a name; you cannot write a command. {listing}.",
                _schema(
                    {
                        "host": {"type": "string", "enum": sorted(config.hosts)},
                        "command": {"type": "string"},
                    },
                    ["host", "command"],
                ),
            ),
            host_command,
        )
    ]
