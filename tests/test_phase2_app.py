"""Phase 2 through the app: an alert comes in, is investigated with tools, and gets a postmortem."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from paeon.app import create_app
from paeon.config import Settings, load
from paeon.investigation import SUBMIT
from paeon.models import ModelReply, ToolCall, ToolSpec, Turn
from paeon.rules import parse_rules
from paeon.schema import StageState
from paeon.store import Store
from paeon.tools import Tool, Toolbox

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
FINDINGS = {
    "summary": "The container is OOM killed in a loop.",
    "impact": "The service is down.",
    "root_cause": {"statement": "Memory limit too low for the new version.", "confidence": 0.8},
    "evidence": [{"claim": "OOM killed", "call": "c1"}],
    "hypotheses_rejected": [],
    "plan": [{"action": "Roll back", "why": "restores service", "risk": "low"}],
}


class Strong:
    """Plays the investigator (two turns) and then the postmortem writer."""

    def __init__(self) -> None:
        self.turns = 0

    def converse(
        self, *, system: str, turns: Sequence[Turn], tools: Sequence[ToolSpec] = ()
    ) -> ModelReply:
        self.turns += 1
        if not tools:
            text = json.dumps(
                {"title": "Service down after a deploy", "summary": "It was OOM killed."}
            )
            return ModelReply(text=text, model="strong-1", input_tokens=100, output_tokens=50)
        call = (
            ToolCall("t1", "inspect_container", {"name": "svc"})
            if self.turns == 1
            else ToolCall("t2", SUBMIT, FINDINGS)
        )
        return ModelReply(
            text="",
            model="strong-1",
            input_tokens=100,
            output_tokens=50,
            tool_calls=(call,),
            raw="raw",
        )


def settings(tmp_path: Path, **rules: Any) -> Settings:
    base = load({"PAEON_INGEST_TOKEN": TOKEN})
    document = {
        "models": {"strong": {"backend": "anthropic", "model": "claude-opus-5-5"}},
        "investigation": {"enabled": True, "model": "strong", "settle_seconds": 0},
        "postmortems": {"enabled": True, "model": "strong", "dir": str(tmp_path / "postmortems")},
        "correlation": {"window_minutes": 0},
        **rules,
    }
    return Settings(
        db_path=base.db_path,
        host=base.host,
        port=base.port,
        ingest_token=TOKEN,
        openobserve=None,
        heartbeat_url=None,
        heartbeat_seconds=60,
        rules=parse_rules(document),
    )


def kuma(status: int, time: str) -> dict[str, Any]:
    return {
        "heartbeat": {"monitorID": 1, "status": status, "time": time, "msg": "timeout"},
        "monitor": {"id": 1, "name": "svc", "type": "http"},
    }


def test_alert_to_investigation_to_postmortem(store: Store, tmp_path: Path) -> None:
    def inspect(args: dict[str, Any]) -> Any:
        return {"state": {"oomkilled": True}}

    toolbox = Toolbox([Tool(ToolSpec("inspect_container", "d", {"type": "object"}), inspect)])
    app = create_app(settings(tmp_path), store, model_clients={"strong": Strong()}, toolbox=toolbox)
    now = datetime.now(UTC)
    with TestClient(app) as client:
        down = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        client.post("/ingest/uptime-kuma", json=kuma(0, down), headers=AUTH)
        app.state.investigator.run_pending(now)
        up = (now - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
        client.post("/ingest/uptime-kuma", json=kuma(1, up), headers=AUTH)
        app.state.postmortems.run_pending(now)
        detail = client.get("/incidents/1", headers=AUTH).json()

    incident = detail["incident"]
    assert (incident["status"], incident["investigation"], incident["postmortem"]) == (
        "resolved",
        "done",
        "done",
    )
    kinds = [event["kind"] for event in detail["events"]]
    for expected in (
        "investigation.started",
        "tool.call",
        "investigation.completed",
        "postmortem.drafted",
    ):
        assert expected in kinds
    assert "notification.sent" not in kinds  # no ntfy topic configured: nothing is sent
    [draft] = list((tmp_path / "postmortems").glob("*.md"))
    assert "# Service down after a deploy" in draft.read_text()


def test_the_real_toolbox_is_built_from_the_rules(store: Store, tmp_path: Path) -> None:
    config = settings(
        tmp_path,
        investigation={
            "enabled": True,
            "model": "strong",
            "tools": {
                "docker": {"url": "http://dockerproxy:2375", "logs": ["media-*"]},
                "openobserve": {"streams": ["traefik"]},  # no OpenObserve account: left out
                "knowledge": {"paths": [str(tmp_path)]},
            },
        },
    )
    app = create_app(config, store)
    with TestClient(app):  # starts and stops the loops, closes the clients
        names = [spec.name for spec in app.state.investigator._toolbox.specs]
    assert names == [
        "open_incidents",
        "past_incidents",
        "recent_changes",
        "search_knowledge",
        "list_containers",
        "inspect_container",
        "container_logs",
    ]


def test_stages_are_off_by_default(store: Store, tmp_path: Path) -> None:
    app = create_app(load({"PAEON_INGEST_TOKEN": TOKEN}), store)
    assert app.state.investigator is None
    assert app.state.postmortems is None


def test_a_phase_1_database_gets_the_new_stage_columns(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE incidents (id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL,"
        " title TEXT NOT NULL, severity TEXT NOT NULL, host TEXT, opened_at TEXT NOT NULL,"
        " last_activity_at TEXT NOT NULL, resolved_at TEXT, flapping INTEGER NOT NULL DEFAULT 0,"
        " maintenance TEXT, triage TEXT NOT NULL);"
        "INSERT INTO incidents (status, title, severity, opened_at, last_activity_at, triage)"
        " VALUES ('open', 'old', 'warning', '2026-11-18T08:00:00+00:00',"
        " '2026-11-18T08:00:00+00:00', 'done');"
    )
    db.close()
    store = Store(path)
    try:
        [incident] = store.incidents()
        assert (incident.triage, incident.investigation, incident.postmortem) == (
            StageState.DONE,
            StageState.OFF,
            StageState.OFF,
        )
    finally:
        store.close()
