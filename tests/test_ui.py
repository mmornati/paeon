"""The console: off by default, read-only, and showing nothing it did not redact."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from paeon.app import create_app
from paeon.config import Settings, load
from paeon.rules import RulesError, parse_rules
from paeon.schema import Severity, StageState, Status
from paeon.store import Event, Store
from paeon.ui import views
from ui_data import populate

TOKEN = "t" * 32
NOW = datetime(2026, 11, 18, 21, 10, tzinfo=UTC)
SAME = {"Sec-Fetch-Site": "same-origin"}


def client(store: Store, **rules: Any) -> TestClient:
    base = load({"PAEON_INGEST_TOKEN": TOKEN})
    document = {"ui": {"enabled": True}, **rules}
    settings = Settings(
        db_path=base.db_path,
        host=base.host,
        port=base.port,
        ingest_token=TOKEN,
        openobserve=None,
        heartbeat_url=None,
        heartbeat_seconds=60,
        rules=parse_rules(document),
    )
    return TestClient(create_app(settings, store))


@pytest.fixture
def seeded(store: Store) -> dict[str, int]:
    return populate(store)


def test_off_by_default(store: Store) -> None:
    assert client(store, ui={}).get("/ui").status_code == 404
    assert client(store, ui={}).get("/").status_code == 404


def test_unknown_ui_key_is_refused() -> None:
    with pytest.raises(RulesError):
        parse_rules({"ui": {"enabled": True, "password": "x"}})


def test_board_puts_each_incident_in_its_bay(store: Store, seeded: dict[str, int]) -> None:
    bays = {bay.key: bay for bay in views.board(store, datetime.now(UTC))}
    where = {bay.key: [s.incident.id for s in bay.strips] for bay in bays.values()}
    assert where["triage"] == [seeded["load"]]
    assert where["investigation"] == [seeded["vpn"]]
    assert where["open"] == [seeded["media"]]
    assert set(where["resolved"]) == {seeded["late"], seeded["stale"]}

    media = {cell.label: cell for cell in bays["open"].strips[0].cells}
    assert (media["Triage"].value, media["Triage"].pen) == ("real 86%", True)
    assert (media["Investigation"].value, media["Investigation"].full) == (
        "78%, 3 calls",
        "78% sure, 3 calls",
    )
    assert [cell.label for cell in bays["open"].strips[0].cells] == [
        "Host",
        "Alerts",
        "Triage",
        "Investigation",
        "Postmortem",
        "Cost",
    ]
    assert media["Alerts"].value == "2 of 2 firing"
    assert media["Cost"].value == "0.21"

    vpn = {cell.label: cell for cell in bays["investigation"].strips[0].cells}
    assert (vpn["Triage"].value, vpn["Triage"].pen) == ("skipped", False)
    assert vpn["Triage"].full == "skipped (page level)"
    assert vpn["Investigation"].value == "running"

    stale = next(s for s in bays["resolved"].strips if s.incident.id == seeded["stale"])
    cells = {cell.label: cell for cell in stale.cells}
    assert cells["Triage"].value == "failed open"
    assert cells["Triage"].full == "real, failed open (low confidence)"
    assert cells["Triage"].pen is False  # code decided, not the model
    assert cells["Investigation"].full == "failed (model error)"
    assert bays["triage"].strips[0].flags == ("flapping",)


def test_board_page_and_fragment(store: Store, seeded: dict[str, int]) -> None:
    http = client(store)
    page = http.get("/ui")
    assert page.status_code == 200
    assert "slo_traefik_5xx" in page.text
    assert "<html" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    fragment = http.get("/ui/board")
    assert "<html" not in fragment.text
    assert f'data-id="{seeded["media"]}"' in fragment.text
    assert http.get("/", follow_redirects=False).headers["location"] == "/ui"
    assert http.get("/ui/static/mark.svg").status_code == 200
    assert http.get("/ui/static/console.css").status_code == 200
    assert http.get("/ui/static/console.js").status_code == 200
    assert http.get("/ui/static/theme.js").status_code == 200


def test_empty_board_says_what_each_bay_is_for(store: Store) -> None:
    text = client(store).get("/ui").text
    assert "No incident is waiting for a verdict." in text
    assert "The log is empty" in client(store).get("/ui/log").text
    assert "No model was called" in client(store).get("/ui/ledger").text


def test_incident_page_ties_claims_to_tool_calls(store: Store, seeded: dict[str, int]) -> None:
    http = client(store)
    text = http.get(f"/ui/incidents/{seeded['media']}").text
    assert 'href="#call-c1"' in text
    assert 'id="call-c1"' in text
    assert "no tool call behind this" in text  # the claim that cites c9
    assert 'href="#call-c9"' not in text
    assert "Advice only. Paeon runs none of it." in text
    assert "Was triage right?" in text
    assert http.get("/ui/incidents/999").status_code == 404


def test_incident_page_for_each_situation(store: Store, seeded: dict[str, int]) -> None:
    http = client(store)
    assert "Not sent to a model (page_level)" in http.get(f"/ui/incidents/{seeded['vpn']}").text
    assert "Running." in http.get(f"/ui/incidents/{seeded['vpn']}").text
    stale = http.get(f"/ui/incidents/{seeded['stale']}").text
    assert "triage failed open (low_confidence)" in stale
    assert "Investigation failed (model_error: timeout after 300 s)" in stale
    load_page = http.get(f"/ui/incidents/{seeded['load']}").text
    assert "Waiting: related alerts" in load_page
    assert "Investigation was off" in load_page
    late = http.get(f"/ui/incidents/{seeded['late']}").text
    assert "You said triage was right." in late
    assert "Investigation skipped (triage: noise)" in late


def test_page_escapes_and_redacts_what_others_wrote(store: Store) -> None:
    incident = store.open_incident(
        title="<script>alert(1)</script>",
        severity=Severity.WARNING,
        host=None,
        opened_at=NOW,
        maintenance=None,
        triage=StageState.OFF,
    )
    store.append(
        "webhook.received",
        {"url": "https://kuma.example/?token=hunter2hunter2", "password": "swordfish"},
        source="uptime-kuma",
        incident_id=incident,
    )
    http = client(store)
    text = http.get(f"/ui/incidents/{incident}").text
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text
    assert "hunter2" not in text
    assert "swordfish" not in text
    # Text the views pass through as it is: an alert title, a failure reason.
    store.attach_alert(
        incident,
        source="kuma",
        fingerprint="a",
        title="GET https://x.example/?api_key=sesame-sesame failed",
        status=Status.FIRING,
    )
    store.set_stage(incident, "investigation", StageState.FAILED)
    store.append(
        "investigation.failed",
        {"reason": "model_error: 401 for Bearer abcdef0123456789", "tool_calls": 0},
        incident_id=incident,
    )
    for page in (http.get(f"/ui/incidents/{incident}").text, http.get("/ui").text):
        assert "sesame" not in page
        assert "abcdef0123456789" not in page
    event = http.get("/ui/events/1").text
    assert "hunter2" not in event
    assert "[REDACTED]" in event
    assert http.get("/ui/events/42").status_code == 404


def test_feedback_is_recorded_from_the_same_origin_only(
    store: Store, seeded: dict[str, int]
) -> None:
    http = client(store)
    url = f"/ui/incidents/{seeded['media']}/feedback"
    for headers in ({}, {"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://evil.example"}):
        refused = http.post(url, data={"vote": "down"}, headers=headers)
        assert refused.status_code == 403
    assert not list(store.events(kind="feedback.recorded", incident_id=seeded["media"]))

    answer = http.post(
        url, data={"vote": "down", "note": " not real "}, headers=SAME, follow_redirects=False
    )
    assert answer.status_code == 303
    assert answer.headers["location"] == f"/ui/incidents/{seeded['media']}#triage"
    [event] = store.events(kind="feedback.recorded", incident_id=seeded["media"])
    assert event.payload == {"about": "triage", "vote": "down", "note": "not real"}

    by_origin = http.post(url, data={"vote": "up"}, headers={"Origin": "http://testserver"})
    assert by_origin.status_code == 200  # the redirect was followed to the incident page
    assert http.post(url, data={"vote": "maybe"}, headers=SAME).status_code == 422
    assert (
        http.post("/ui/incidents/999/feedback", data={"vote": "up"}, headers=SAME).status_code
        == 404
    )


def test_require_header_refuses_what_the_proxy_did_not_mark(store: Store) -> None:
    http = client(store, ui={"enabled": True, "require_header": "X-authentik-username"})
    assert http.get("/ui").status_code == 403
    assert http.get("/ui", headers={"X-authentik-username": "me"}).status_code == 200


def test_log_filters_and_pages(store: Store, seeded: dict[str, int]) -> None:
    http = client(store)
    everything = http.get("/ui/log").text
    assert "Digest sent: 2 incidents" in everything
    tools = http.get("/ui/log?show=tool").text
    assert "search_logs" in tools
    assert "Digest sent" not in tools
    assert "Digest sent" in http.get("/ui/log?show=nonsense").text
    assert "No event of this kind" in http.get("/ui/log?show=tool&before=1").text

    for _ in range(120):
        store.append("change.recorded", {"kind": "deploy", "title": "x"}, source="change")
    first = http.get("/ui/log?show=in")
    assert "Older" in first.text
    assert "Newest" not in first.text
    last = max(event.id for event in store.events())
    second = http.get(f"/ui/log?show=in&before={last - 99}")
    assert "Newest" in second.text


def test_ledger_counts_calls_budgets_and_feedback(store: Store, seeded: dict[str, int]) -> None:
    rules = parse_rules(
        {
            "models": {"m": {"backend": "anthropic", "model": "x"}},
            "triage": {"enabled": True, "model": "m", "daily_budget": 0.1},
            "investigation": {"enabled": True, "model": "m"},
        }
    )
    view = views.ledger_view(store, rules, datetime.now(UTC) + timedelta(seconds=1))
    assert view.total.calls == 7
    assert view.total.errors == 1
    assert round(view.total.cost, 4) == 0.2734
    assert next((stage, model) for stage, model, _ in view.by_model) == (
        "investigate",
        "claude-opus-5-5",
    )
    assert sum(tally.calls for _, tally, _ in view.by_day) == 7  # quiet days are left out
    assert dict(view.verdicts) == {"noise": 1, "real": 1}
    assert dict(view.fail_open) == {"low_confidence": 1}
    assert (view.rated, view.right) == (1, 1)
    # Every call may sit before UTC midnight when the test runs just after it.
    [budget] = view.budgets
    assert budget.stage == "triage"
    assert budget.exhausted is (view.today >= 0.1)
    assert 0 <= budget.share <= 100

    page = client(
        store,
        models={"m": {"backend": "anthropic", "model": "x"}},
        triage={"enabled": True, "model": "m", "daily_budget": 0.0001},
    ).get("/ui/ledger")
    assert page.status_code == 200
    assert "claude-opus-5-5" in page.text
    assert "You rated 1 incident: triage was right on 1." in page.text


def test_budget_share() -> None:
    assert views.Budget("triage", 0, 0).share == 100.0
    assert views.Budget("triage", 2, 0.5).share == 25.0
    assert views.Budget("triage", 2, 5).exhausted
    assert views.Budget("triage", 2, 5).left == 0
    assert views.Budget("triage", 2, 0.5).left == 1.5


@pytest.mark.parametrize(
    ("kind", "payload", "expected"),
    [
        ("alert.resolved", {"title": "disk"}, "disk resolved"),
        ("change.recorded", {"kind": "deploy", "title": "v2", "host": "a"}, "deploy: v2 on a"),
        (
            "incident.opened",
            {"maintenance": "weekly"},
            "Incident opened, in maintenance window weekly",
        ),
        ("incident.reopened", {}, "Incident reopened: the same alert fired again"),
        (
            "triage.verdict",
            {"fail_open": "invalid_output"},
            "Triage failed open (invalid_output): treated as real",
        ),
        (
            "investigation.completed",
            {"tool_calls": 2},
            "Investigation completed: 2 tool calls, ? sure of the cause",
        ),
        ("notification.failed", {"error": "HTTP 500"}, "Notification not sent (HTTP 500)"),
        (
            "postmortem.drafted",
            {"path": "a.md", "narrative": False},
            "Postmortem draft written to a.md, without the model's prose",
        ),
        ("postmortem.failed", {"error": "OSError"}, "Postmortem draft not written (OSError)"),
        ("feedback.recorded", {"vote": "down", "note": "no"}, "You said triage was wrong: no"),
        ("something.new", {}, "something.new"),
    ],
)
def test_describe(kind: str, payload: dict[str, Any], expected: str) -> None:
    event = Event(1, NOW, kind, None, None, payload)
    assert views.describe(event) == expected


def test_voices_and_tags() -> None:
    def row(kind: str) -> views.Row:
        return views.row_of(Event(1, NOW, kind, None, None, {}))

    assert (row("alert.firing").voice, row("alert.firing").tag) == ("in", "in")
    assert row("incident.opened").tag == "code"
    assert row("investigation.failed").voice == "code"
    assert row("triage.verdict").tag == "model"
    assert row("triage.verdict").pen
    assert row("tool.call").pen
    assert not row("model.call").pen  # a fact about a call, not the model's words
    failed = views.row_of(Event(1, NOW, "tool.call", None, None, {"call": "c1", "is_error": True}))
    assert failed.note == "the tool answered with an error"
    assert "error" not in failed.text
    assert (row("tool.call").voice, row("tool.call").tag) == ("model", "tool")
    assert row("digest.sent").tag == "sent"
    assert row("feedback.recorded").tag == "you"
    assert row("something.new").voice == "code"


def test_small_formats() -> None:
    assert views.money(0) == "0"
    assert views.money(0.0021) == "0.0021"
    assert views.money(1.5) == "1.50"
    assert views.span(timedelta(seconds=40)) == "40 s"
    assert views.span(timedelta(minutes=64)) == "64 min"
    assert views.span(timedelta(hours=5, minutes=7)) == "5 h 07"
    assert views.span(timedelta(days=3)) == "3 d"


def test_long_payload_is_cut(store: Store) -> None:
    event = Event(1, NOW, "tool.call", None, None, {"result": ["x" * 19_000] * 4})
    detail = views.row_of(event, detail=True).detail
    assert detail is not None
    assert detail.endswith("… cut")


def test_postmortem_cell_and_stage_names(store: Store, tmp_path: Path) -> None:
    incident_id = store.open_incident(
        title="t",
        severity=Severity.INFO,
        host=None,
        opened_at=NOW,
        maintenance=None,
        triage=StageState.DONE,
        investigation=StageState.DONE,
    )
    store.set_stage(incident_id, "postmortem", StageState.DONE)
    store.append("postmortem.drafted", {"path": "p.md", "narrative": True}, incident_id=incident_id)
    incident = store.incident(incident_id)
    assert incident is not None
    view = views.incident_view(store, incident, NOW)
    cells = {cell.label: cell.value for cell in view.strip.cells}
    # Stages marked done with no event behind them are shown as the state, not invented.
    assert cells["Triage"] == "done"
    assert cells["Investigation"] == "done"
    assert cells["Postmortem"] == "draft written"
    assert view.postmortem == "p.md"
    assert (
        "Postmortem draft written to p.md" in client(store).get(f"/ui/incidents/{incident_id}").text
    )
    rules = parse_rules({})
    assert views.stages(rules) == (
        ("Triage", "off"),
        ("Investigation", "off"),
        ("Postmortems", "off"),
        ("Actions", "none"),
    )


def test_proxy_secret_refuses_a_neighbour_that_only_knows_the_header(store: Store) -> None:
    base = load({"PAEON_INGEST_TOKEN": TOKEN})
    rules = parse_rules({"ui": {"enabled": True, "require_header": "X-User", "proxy_secret": {}}})
    secret = "s" * 32
    settings = Settings(
        db_path=base.db_path,
        host=base.host,
        port=base.port,
        ingest_token=TOKEN,
        openobserve=None,
        heartbeat_url=None,
        heartbeat_seconds=60,
        rules=rules,
        ui_proxy_secret=secret,
    )
    http = TestClient(create_app(settings, store))
    assert http.get("/ui", headers={"X-User": "me"}).status_code == 403
    assert http.get("/ui", headers={"X-User": "me", "X-Paeon-Proxy": "guess"}).status_code == 403
    assert http.get("/ui", headers={"X-User": "me", "X-Paeon-Proxy": secret}).status_code == 200
    assert http.get("/", headers={"X-User": "me"}, follow_redirects=False).status_code == 403


def test_proxy_secret_must_be_in_the_environment(tmp_path: Path) -> None:
    from paeon.config import ConfigError

    path = tmp_path / "paeon.yaml"
    path.write_text("ui:\n  enabled: true\n  proxy_secret: {}\n")
    env = {"PAEON_INGEST_TOKEN": TOKEN, "PAEON_CONFIG": str(path)}
    with pytest.raises(ConfigError, match="PAEON_UI_PROXY_SECRET"):
        load(env)
    with pytest.raises(ConfigError):
        load({**env, "PAEON_UI_PROXY_SECRET": "short"})
    assert load({**env, "PAEON_UI_PROXY_SECRET": "s" * 24}).ui_proxy_secret == "s" * 24
