"""The console: off by default, read-only, and showing nothing it did not redact."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from paeon.app import create_app
from paeon.config import Settings, load
from paeon.rules import RulesError, parse_rules
from paeon.schema import Severity, StageState, Status
from paeon.store import Event, Store
from paeon.ui import views
from paeon.ui.explorer import Query, explore
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


def strip(store: Store, incident_id: int) -> views.Strip:
    incident = store.incident(incident_id)
    assert incident is not None
    return views.strip_of(incident, list(store.events(incident_id=incident_id)), datetime.now(UTC))


def test_each_incident_says_where_it_stands(store: Store, seeded: dict[str, int]) -> None:
    page = explore(store, Query(), datetime.now(UTC), ZoneInfo("UTC"))
    rows = {row.incident.id: row for row in page.rows}
    assert [r.incident.id for r in page.rows if r.open] == sorted(
        (seeded["load"], seeded["vpn"], seeded["media"]),
        key=lambda i: rows[i].incident.last_activity_at,
        reverse=True,
    )
    assert {seeded["late"], seeded["stale"]} <= set(rows)
    assert rows[seeded["load"]].track[0].state == "waiting"  # waiting for triage
    assert rows[seeded["vpn"]].track[1].state == "waiting"  # being investigated
    assert rows[seeded["load"]].flags == ("flapping",)

    media = {cell.label: cell for cell in strip(store, seeded["media"]).cells}
    assert (media["Triage"].value, media["Triage"].pen) == ("real 86%", True)
    assert (media["Investigation"].value, media["Investigation"].full) == (
        "78%, 3 calls",
        "78% sure, 3 calls",
    )
    assert list(media) == ["Host", "Alerts", "Triage", "Investigation", "Postmortem", "Cost"]
    assert media["Alerts"].value == "2 of 2 firing"
    assert media["Cost"].value == "0.21"
    assert rows[seeded["media"]].cost == "0.21"

    vpn = {cell.label: cell for cell in strip(store, seeded["vpn"]).cells}
    assert (vpn["Triage"].value, vpn["Triage"].pen) == ("skipped", False)
    assert vpn["Triage"].full == "skipped (page level)"
    assert vpn["Investigation"].value == "running"

    cells = {cell.label: cell for cell in strip(store, seeded["stale"]).cells}
    assert cells["Triage"].value == "failed open"
    assert cells["Triage"].full == "real, failed open (low confidence)"
    assert cells["Triage"].pen is False  # code decided, not the model
    assert cells["Investigation"].full == "failed (model error)"
    assert rows[seeded["stale"]].verdict.value == "failed open"


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


def test_empty_pages_say_what_will_appear(store: Store) -> None:
    text = client(store).get("/ui").text
    assert "No incident yet: nothing has fired." in text
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


def test_channels_page_lists_each_channel_and_how_it_went(store: Store) -> None:
    assert "/ui/channels" not in client(store).get("/ui").text  # nothing sends: no page to offer
    topic = {"type": "ntfy", "url": "https://ntfy.example.org", "topic": "t"}
    document: dict[str, Any] = {
        "notifiers": {"chat": topic, "ops": topic},
        "digest": {"ntfy": {"url": "https://ntfy.example.org", "topic": "incidents"}},
        "investigation": {"notify": ["chat", "ops"]},
        "actions": {"notify": ["chat"]},
    }
    http = client(store, **document)
    sent = {"channel": "ntfy", "name": "chat", "to": "t", "about": "investigation"}
    store.append("notification.sent", sent)
    store.append("notification.failed", {**sent, "error": "ntfy answered HTTP 403"})
    store.append("notification.sent", {"channel": "ntfy", "about": "digest", "topic": "incidents"})
    store.append("notification.sent", {**sent, "name": "gone"})  # no longer in the rules
    rules = parse_rules({"ui": {"enabled": True}, **document})
    lines = {line.name: line for line in views.channels_view(store, rules, datetime.now(UTC))}
    assert list(lines) == ["ntfy", "chat", "ops"]
    chat = lines["chat"]
    assert (chat.kind, chat.carries, chat.sent, chat.failed) == (
        "ntfy",
        ("analysis", "approvals"),
        1,
        1,
    )
    assert (chat.state, chat.last_error) == ("failed", "ntfy answered HTTP 403")
    assert (lines["ntfy"].state, lines["ntfy"].sent) == ("sent", 1)  # an entry without a name
    assert lines["ops"].state == "unused"
    page = http.get("/ui/channels").text
    assert "ntfy answered HTTP 403" in page
    assert "nothing sent yet" in page
    assert 'href="/ui/channels" aria-current="page"' in page


def test_explorer_page_keeps_its_filters_in_every_link(
    store: Store, seeded: dict[str, int]
) -> None:
    http = client(store)
    page = http.get("/ui?severity=critical&range=all").text
    assert 'data-live="/ui/board?range=all&amp;severity=critical"' in page
    assert 'href="/ui?range=24h&amp;severity=critical"' in page  # the range keeps the filter
    assert 'aria-checked="true"' in page
    fragment = http.get("/ui/board?severity=critical&range=all").text
    assert f'data-id="{seeded["stale"]}"' in fragment  # critical
    assert f'data-id="{seeded["media"]}"' not in fragment  # warning
    assert "<html" not in fragment


def test_log_search_matches_what_is_shown_never_a_secret(store: Store) -> None:
    store.append("webhook.received", {"password": "swordfish", "title": "disk full"})
    store.append("digest.sent", {"incidents": 3})
    http = client(store)
    assert "swordfish" not in http.get("/ui/log").text
    # A guess at the secret finds nothing: the search reads the redacted line, not the payload.
    guessed = http.get("/ui/log?q=swordfish").text
    assert "No event says" in guessed
    found = http.get("/ui/log?q=webhook").text
    assert "webhook.received" in found
    assert "digest.sent" not in found


def test_log_can_be_narrowed_to_one_incident(store: Store, seeded: dict[str, int]) -> None:
    http = client(store)
    media = seeded["media"]
    page = http.get(f"/ui/log?incident={media}").text
    assert f"Incident #{media}" in page  # the filter, with a way to remove it
    assert f"Incident {seeded['vpn']}</a>" not in page
    assert f'href="/ui/log?incident={media}"' in http.get("/ui/log").text


def test_spend_chart_has_a_round_scale_and_a_period(store: Store, seeded: dict[str, int]) -> None:
    assert views.nice_ceiling(0) == 0
    assert views.nice_ceiling(0.21) == 0.5
    assert views.nice_ceiling(1.23) == 2
    assert views.nice_ceiling(3) == 5
    assert views.nice_ceiling(7.5) == 10
    view = views.ledger_view(store, parse_rules({}), datetime.now(UTC), days=7)
    assert len(view.stacks) == 7
    assert view.stages_seen[:2] == ("triage", "investigate")
    assert view.top >= max(sum(costs.values()) for _, costs in view.stacks)
    http = client(store)
    assert 'href="/ui/ledger?days=30"' in http.get("/ui/ledger").text
    assert "last 30 days" in http.get("/ui/ledger?days=30").text
    assert "last 14 days" in http.get("/ui/ledger?days=99").text  # an unknown period: the default
