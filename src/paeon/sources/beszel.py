"""A Beszel hub, polled: its alerts in, systems, statistics and containers read.

Beszel's own notifications carry a title and a sentence, with nothing that says firing or
resolved but the wording. Its hub is a PocketBase application, and each alert is a record with
a `triggered` flag (beszel.dev/guide/rest-api, pocketbase.io/docs/api-records): reading those
is exact, so this source polls them instead of receiving anything.

Two things to know. The account signs in with its password, which is a POST: it is the only
one this adapter sends, and everything after it is a GET. And Beszel says its collections may
change between versions: a hub that answers differently makes the source stale, not wrong.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx2 as httpx

from paeon.models import ToolSpec
from paeon.rules import BeszelSource
from paeon.schema import Alert, Severity, Status
from paeon.sources import Connected, Polling, ReadError, tool_name
from paeon.sources.http import Reader, decoded
from paeon.tools import Tool, ToolError, _integer, _schema, _string

if TYPE_CHECKING:
    from paeon.recorder import Recorder
    from paeon.store import Store

KIND = "beszel"
_LOGIN = "/api/collections/users/auth-with-password"
_COLLECTIONS = ("alerts", "systems", "system_stats", "containers")
_PATHS = (_LOGIN, "/api/health", *(f"/api/collections/{name}/records" for name in _COLLECTIONS))
_ID = re.compile(r"[A-Za-z0-9]{1,40}")


class Hub:
    """Records of the hub's collections, with a session renewed when the hub refuses it."""

    def __init__(self, http: Reader, user: str, password: str) -> None:
        self._http = http
        self._user = user
        self._password = password
        self._signed_in = False

    def _sign_in(self) -> None:
        body = {"identity": self._user, "password": self._password}
        answer = decoded(KIND, self._http.post(_LOGIN, body, bare=True))
        token = answer.get("token") if isinstance(answer, dict) else None
        if not isinstance(token, str) or not token:
            raise ReadError("beszel did not sign the account in")
        self._http.authorize({"Authorization": token})
        self._signed_in = True

    def records(self, collection: str, **params: Any) -> list[dict[str, Any]]:
        if collection not in _COLLECTIONS:
            raise ReadError(f"beszel: {collection} is not a collection this source reads")
        path = f"/api/collections/{collection}/records"
        renewed = not self._signed_in
        if not self._signed_in:
            self._sign_in()
        try:
            answer = self._http.json(path, **params)
        except ReadError as exc:
            if renewed or not str(exc).endswith("HTTP 401"):
                raise
            self._sign_in()  # the session ran out: once more, with a new one
            answer = self._http.json(path, **params)
        items = answer.get("items") if isinstance(answer, dict) else None
        return [item for item in items if isinstance(item, dict)] if items else []

    def health(self) -> None:
        self._http.get("/api/health")


def _system(record: Mapping[str, Any]) -> str:
    """The name of the system a record belongs to, when the hub expanded it."""
    expand = record.get("expand")
    system = expand.get("system") if isinstance(expand, dict) else None
    name = system.get("name") if isinstance(system, dict) else None
    return str(name or record.get("system") or "")


def alert_of(record: Mapping[str, Any], *, now: datetime, source: str) -> Alert | None:
    """One record of the `alerts` collection as an observation of now."""
    identity = record.get("id")
    kind = record.get("name")
    if not isinstance(identity, str) or not identity or not isinstance(kind, str) or not kind:
        return None
    firing = record.get("triggered") is True
    system = _system(record)
    threshold = record.get("value")
    minutes = record.get("min")
    described = ""
    if kind.lower() == "status":
        described = "The hub lost its connection to the system."
    elif threshold is not None:
        described = f"{kind} is beyond {threshold}" + (
            f", averaged over {minutes} minutes." if minutes else "."
        )
    return Alert(
        source=source,
        fingerprint=identity,
        status=Status.FIRING if firing else Status.RESOLVED,
        severity=(Severity.CRITICAL if kind.lower() == "status" else Severity.WARNING)
        if firing
        else Severity.UNKNOWN,
        title=f"{system}: {kind}" if system else kind,
        description=described if firing else "",
        labels={"alert": kind, **({"system": system} if system else {})},
        starts_at=now,
        observed_at=now,
        raw={key: value for key, value in record.items() if key != "expand"},
    )


class BeszelPoller:
    def __init__(self, hub: Hub, store: Store, recorder: Recorder, *, name: str) -> None:
        self.name = name
        self._hub = hub
        self._store = store
        self._recorder = recorder

    def last_data_at(self) -> datetime | None:
        cursor = self._store.cursor(self.name)
        return None if cursor is None else datetime.fromisoformat(cursor)

    def poll(self, now: datetime) -> int:
        """Observe every alert of the hub. Returns how many changed state."""
        records = self._hub.records("alerts", perPage=500, expand="system")
        changes = 0
        for record in records:
            alert = alert_of(record, now=now, source=self.name)
            if alert is not None and self._recorder.observe(alert) is not None:
                changes += 1
        self._store.set_cursor(self.name, now.isoformat())  # the hub answered
        return changes


def connect(
    name: str,
    config: BeszelSource,
    secrets: Mapping[str, str],
    transport: httpx.BaseTransport | None = None,
    store: Store | None = None,
    recorder: Recorder | None = None,
) -> Connected:
    http = Reader(KIND, config.url, paths=_PATHS, transport=transport)
    hub = Hub(http, config.user or "", secrets.get("password", ""))

    def systems(text: str = "") -> list[dict[str, Any]]:
        return [
            record
            for record in hub.records("systems", perPage=500, sort="name")
            if text in str(record.get("name") or "").lower()
        ]

    def system_status(args: dict[str, Any]) -> Any:
        found = [
            {
                "name": record.get("name"),
                "status": record.get("status"),
                "info": record.get("info"),
                "updated": record.get("updated"),
            }
            for record in systems(_string(args, "text", required=False).lower())
        ]
        return {"systems": found[:200], "count": len(found)}

    def system_stats(args: dict[str, Any]) -> Any:
        wanted = _string(args, "system").lower()
        minutes = _integer(args, "since_minutes", 60, low=1, high=24 * 60)
        match = next((s for s in systems() if str(s.get("name") or "").lower() == wanted), None)
        identity = None if match is None else match.get("id")
        if not isinstance(identity, str) or _ID.fullmatch(identity) is None:
            raise ToolError("no system with this name")
        since = (datetime.now(UTC) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        records = hub.records(
            "system_stats",
            perPage=min(minutes, 120),
            sort="-created",
            filter=f"system='{identity}' && type='1m' && created>='{since}'",
        )
        return {
            "stats": [{"at": r.get("created"), **_stats(r.get("stats"))} for r in records],
            "count": len(records),
        }

    def containers(args: dict[str, Any]) -> Any:
        text = _string(args, "text", required=False).lower()
        try:
            records = hub.records("containers", perPage=500, sort="name", expand="system")
        except ReadError as exc:
            if str(exc).endswith("HTTP 404"):
                raise ToolError("this hub keeps no list of containers") from None
            raise
        found = [
            {
                "name": r.get("name"),
                "system": _system(r),
                "status": r.get("status"),
                "health": r.get("health"),
                "cpu": r.get("cpu"),
                "memory": r.get("memory"),
                "image": r.get("image"),
            }
            for r in records
            if text in f"{r.get('name')} {_system(r)}".lower()
        ]
        return {"containers": found[:200], "count": len(found)}

    def probe() -> str:
        hub.health()
        return f"{len(systems())} systems"

    tools = [
        Tool(
            ToolSpec(
                tool_name("system_status", name),
                f"The systems the Beszel hub `{name}` watches: whether each is up, down or"
                " paused, and its last figures as Beszel names them (cpu: CPU %, mp: memory %,"
                " dp: disk %, u: uptime in seconds).",
                _schema({"text": {"type": "string", "description": "Part of a system name."}}),
            ),
            system_status,
        ),
        Tool(
            ToolSpec(
                tool_name("system_stats", name),
                f"One system of the Beszel hub `{name}` minute by minute, newest first: CPU,"
                " memory, disk and network, under Beszel's own short names (cpu, mp, dp, ns,"
                " nr). `system` is its exact name.",
                _schema(
                    {
                        "system": {"type": "string"},
                        "since_minutes": {"type": "integer", "description": "Default 60."},
                    },
                    ["system"],
                ),
            ),
            system_stats,
        ),
        Tool(
            ToolSpec(
                tool_name("containers", name),
                f"The containers the Beszel hub `{name}` sees on its systems: status, health,"
                " CPU and memory use, and image.",
                _schema({"text": {"type": "string", "description": "Part of a name."}}),
            ),
            containers,
        ),
    ]
    polling = None
    if config.alerts and store is not None and recorder is not None:
        polling = Polling(
            BeszelPoller(hub, store, recorder, name=name), config.poll_seconds, config.stale_seconds
        )
    return Connected(name, KIND, polling=polling, tools=tools, probe=probe, close=http.close)


def _stats(stats: Any) -> dict[str, Any]:
    """The figures of one sample: numbers and short texts only, whatever their names are."""
    if not isinstance(stats, dict):
        return {}
    return {
        str(key): value
        for key, value in stats.items()
        if isinstance(value, int | float | str) and not isinstance(value, bool)
    }
