"""Channels added in the console: who may, where they are kept, and what they can never do."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, get_args

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from paeon.app import create_app
from paeon.channels import SERVICES, STAGES, Channels
from paeon.config import ConfigError, Settings, load
from paeon.notify import Message, deliver
from paeon.rules import Channel, RulesError, parse_rules
from paeon.store import Store
from paeon.ui.access import Gate, Sessions, role_from_groups
from paeon.vault import Vault, VaultError

TOKEN = "t" * 32
KEY = "k" * 40
PASSWORD = "correct horse battery"
PROXY = "p" * 32
SAME = {"Sec-Fetch-Site": "same-origin"}
ADMIN = {"X-Paeon-Proxy": PROXY, "X-authentik-groups": "family|paeon-admins", **SAME}
OPERATOR = {"X-Paeon-Proxy": PROXY, "X-authentik-groups": "paeon-ops", **SAME}
VIEWER = {"X-Paeon-Proxy": PROXY, "X-authentik-groups": "family", **SAME}
PROXIED_UI = {
    "enabled": True,
    "settings": True,
    "proxy_secret": {},
    "roles": {"admin": ["paeon-admins"], "operator": ["paeon-ops"]},
}
LOCAL_UI = {"enabled": True, "settings": True, "auth": "local"}


class Outbox:
    """Stands in for the services channels post to, and keeps what was posted."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json={"ok": True})


def app_for(
    store: Store, ui: dict[str, Any], outbox: Outbox | None = None, **rules: Any
) -> TestClient:
    base = load({"PAEON_INGEST_TOKEN": TOKEN})
    settings = Settings(
        db_path=base.db_path,
        host=base.host,
        port=base.port,
        ingest_token=TOKEN,
        openobserve=None,
        heartbeat_url=None,
        heartbeat_seconds=60,
        rules=parse_rules({"ui": ui, **rules}),
        ui_proxy_secret=PROXY,
        ui_admin_password=PASSWORD,
        secret_key=KEY,
    )
    transport = httpx.MockTransport(outbox or Outbox())
    return TestClient(
        create_app(settings, store, channel_transport=transport), follow_redirects=False
    )


NTFY = {
    "name": "phone",
    "url": "https://ntfy.example.org",
    "topic": "paeon",
    "token": "tk_very_secret_value",
    "carry_digest": "on",
    "then": "save",
}


# --- the rules ---------------------------------------------------------------------------------


def test_settings_need_someone_who_may_change_them() -> None:
    with pytest.raises(RulesError, match="settings needs an admin"):
        parse_rules({"ui": {"enabled": True, "settings": True}})
    with pytest.raises(RulesError, match="roles needs `proxy_secret`"):
        parse_rules({"ui": {"enabled": True, "roles": {"admin": ["a"]}}})
    with pytest.raises(RulesError, match="do not go with"):
        parse_rules({"ui": {"auth": "local", "proxy_secret": {}, "roles": {"admin": ["a"]}}})
    parse_rules({"ui": LOCAL_UI})
    parse_rules({"ui": PROXIED_UI})


def test_the_key_and_the_password_are_required(tmp_path: Path) -> None:
    rules = tmp_path / "paeon.yaml"
    rules.write_text("ui: {enabled: true, auth: local, settings: true}\n")
    env = {"PAEON_INGEST_TOKEN": TOKEN, "PAEON_CONFIG": str(rules)}
    with pytest.raises(ConfigError, match="PAEON_SECRET_KEY"):
        load(env)
    with pytest.raises(ConfigError, match="PAEON_UI_ADMIN_PASSWORD"):
        load({**env, "PAEON_SECRET_KEY": KEY, "PAEON_UI_ADMIN_PASSWORD": "short"})
    loaded = load({**env, "PAEON_SECRET_KEY": KEY, "PAEON_UI_ADMIN_PASSWORD": PASSWORD})
    assert loaded.secret_key == KEY


def test_every_service_has_a_form_for_each_of_its_keys() -> None:
    models = {get_args(model.model_fields["type"].annotation)[0]: model for model in _models()}
    assert set(SERVICES) == set(models)
    for kind, service in SERVICES.items():
        keys = set(models[kind].model_fields) - {"type"}
        offered = {spec.model_key for spec in service.fields}
        # A webhook's address is always kept as a secret: its plain `url` key is not offered.
        assert offered == keys - ({"url"} if kind == "webhook" else set()), kind


def _models() -> tuple[Any, ...]:
    return get_args(get_args(Channel)[0])  # Annotated[A | B | ..., discriminator]


# --- the vault and the sessions ----------------------------------------------------------------


def test_a_sealed_secret_opens_only_with_its_key() -> None:
    sealed = Vault(KEY).seal("tk_secret")
    assert "tk_secret" not in sealed
    assert Vault(KEY).open(sealed) == "tk_secret"
    with pytest.raises(VaultError):
        Vault("another key " * 4).open(sealed)


def test_a_session_ends_with_time_or_with_the_password() -> None:
    now = [1000.0]
    sessions = Sessions(KEY, PASSWORD, clock=lambda: now[0])
    assert sessions.sign_in("wrong") is None
    cookie = sessions.sign_in(PASSWORD)
    assert cookie is not None
    assert sessions.valid(cookie)
    assert not sessions.valid(cookie[:-1] + ("0" if cookie[-1] != "0" else "1"))
    assert not sessions.valid("")
    assert not sessions.valid("a.b")
    assert not Sessions(KEY, "another password!").valid(cookie)
    now[0] += 15 * 24 * 3600
    assert not sessions.valid(cookie)


def test_wrong_passwords_close_the_gate_for_a_while() -> None:
    now = [0.0]
    gate = Gate(tries=3, wait=60, clock=lambda: now[0])
    for _ in range(3):
        assert gate.locked() == 0
        gate.failed()
    assert gate.locked() == 60
    now[0] += 61
    assert gate.locked() == 0


def test_groups_give_the_highest_role_they_name() -> None:
    roles = parse_rules({"ui": PROXIED_UI}).ui.roles
    assert roles is not None
    assert role_from_groups(roles, "family|paeon-admins") == "admin"
    assert role_from_groups(roles, " paeon-ops ") == "operator"
    assert role_from_groups(roles, "family") == "viewer"
    assert role_from_groups(roles, None) == "viewer"


# --- signing in --------------------------------------------------------------------------------


def test_local_console_is_closed_until_signed_in(store: Store) -> None:
    client = app_for(store, LOCAL_UI)
    answer = client.get("/ui/channels")
    assert answer.status_code == 303
    assert answer.headers["location"] == "/ui/signin?next=%2Fui%2Fchannels"
    assert client.get("/ui/board").status_code == 401  # the live refresh gets no sign-in page
    assert client.post("/ui/channels/add/ntfy", data=NTFY, headers=SAME).status_code == 401
    assert client.get("/ui/signin").status_code == 200

    wrong = client.post("/ui/signin", data={"password": "nope"}, headers=SAME)
    assert wrong.status_code == 401
    assert "not the password" in wrong.text
    assert client.post("/ui/signin", data={"password": PASSWORD}).status_code == 403  # cross-site

    signed = client.post(
        "/ui/signin", data={"password": PASSWORD, "next": "/ui/channels"}, headers=SAME
    )
    assert signed.status_code == 303
    assert signed.headers["location"] == "/ui/channels"
    cookie = signed.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/ui" in cookie
    assert client.get("/ui/channels").status_code == 200
    assert [e.kind for e in store.events(kinds=("console.signin",))] == ["console.signin"]

    client.post("/ui/signout", headers=SAME)
    assert client.get("/ui/channels").status_code == 303


def test_sign_in_goes_nowhere_but_the_console(store: Store) -> None:
    client = app_for(store, LOCAL_UI)
    for wanted in ("https://evil.example", "//evil.example", "/ui//evil.example", "/ingest/x"):
        signed = client.post(
            "/ui/signin", data={"password": PASSWORD, "next": wanted}, headers=SAME
        )
        assert signed.headers["location"] == "/ui"


def test_sign_in_pauses_after_wrong_passwords(store: Store) -> None:
    client = app_for(store, LOCAL_UI)
    for _ in range(5):
        client.post("/ui/signin", data={"password": "nope"}, headers=SAME)
    locked = client.post("/ui/signin", data={"password": PASSWORD}, headers=SAME)
    assert locked.status_code == 429
    assert "Too many wrong passwords" in locked.text
    assert len(list(store.events(kinds=("console.signin_locked",)))) == 1


def test_proxy_console_has_no_sign_in_page(store: Store) -> None:
    assert app_for(store, PROXIED_UI).get("/ui/signin", headers=ADMIN).status_code == 404


# --- roles -------------------------------------------------------------------------------------


def test_only_an_admin_changes_channels(store: Store) -> None:
    client = app_for(store, PROXIED_UI)
    for headers in (VIEWER, OPERATOR):
        assert client.get("/ui/channels", headers=headers).status_code == 200
        assert client.get("/ui/channels/add", headers=headers).status_code == 403
        assert client.get("/ui/channels/add/ntfy", headers=headers).status_code == 403
        posted = client.post("/ui/channels/add/ntfy", data=NTFY, headers=headers)
        assert posted.status_code == 403
        assert "Add a channel" not in client.get("/ui/channels", headers=headers).text
    assert store.console_channels() == []
    assert client.get("/ui/channels/add", headers=ADMIN).status_code == 200
    without_origin = {k: v for k, v in ADMIN.items() if k != "Sec-Fetch-Site"}
    assert (
        client.post("/ui/channels/add/ntfy", data=NTFY, headers=without_origin).status_code == 403
    )


def test_a_viewer_reads_and_does_nothing_else(store: Store) -> None:
    incident = store.open_incident
    del incident  # the routes are what is checked here, not an incident
    client = app_for(store, PROXIED_UI, actions={"enabled": False})
    feedback = client.post("/ui/incidents/1/feedback", data={"vote": "up"}, headers=VIEWER)
    assert feedback.status_code == 403
    # An operator passes the role check (and then finds no such incident).
    feedback = client.post("/ui/incidents/1/feedback", data={"vote": "up"}, headers=OPERATOR)
    assert feedback.status_code == 404
    assert "Your role is viewer" in client.get("/ui", headers=VIEWER).text


def test_without_roles_everyone_is_an_operator_as_before(store: Store) -> None:
    client = app_for(store, {"enabled": True})
    assert (
        client.post("/ui/incidents/1/feedback", data={"vote": "up"}, headers=SAME).status_code
        == 404
    )
    assert client.get("/ui/channels/add").status_code == 404  # settings are off: no such page


# --- adding, changing, testing, removing -------------------------------------------------------


def test_a_channel_added_in_the_console_is_sent_to_at_once(store: Store) -> None:
    outbox = Outbox()
    client = app_for(store, PROXIED_UI, outbox)
    saved = client.post("/ui/channels/add/ntfy", data={**NTFY, "then": "test"}, headers=ADMIN)
    assert saved.status_code == 303
    assert saved.headers["location"] == "/ui/channels?tested=phone&ok=1"
    assert len(outbox.requests) == 1
    assert outbox.requests[0].headers["authorization"] == "Bearer tk_very_secret_value"

    channels: Channels = client.app.state.channels  # type: ignore[attr-defined]
    assert [n.name for n in channels.rosters["digest"]] == ["phone"]
    assert list(channels.rosters["investigation"]) == []
    assert channels.rules.digest.notify == ("phone",)
    deliver(channels.rosters["digest"], Message(title="Digest", text="quiet day"))
    assert len(outbox.requests) == 2

    page = client.get("/ui/channels?tested=phone&ok=1", headers=ADMIN).text
    assert "A test was sent to" in page
    assert "phone" in page
    assert "digest" in page
    kinds = [e.kind for e in store.events()]
    assert kinds == ["console.channel.saved", "notification.sent"]


def test_no_secret_reaches_the_file_the_log_or_the_page(store: Store, tmp_path: Path) -> None:
    client = app_for(store, PROXIED_UI)
    data = {**NTFY, "url": "https://ntfy.example.org", "token": "tk_very_secret_value"}
    client.post("/ui/channels/add/ntfy", data=data, headers=ADMIN)
    database = sqlite3.connect(tmp_path / "paeon.sqlite")
    stored = json.dumps(database.execute("SELECT * FROM console_channels").fetchall())
    logged = json.dumps(database.execute("SELECT payload FROM events").fetchall())
    database.close()
    assert "tk_very_secret_value" not in stored + logged
    assert "ntfy.example.org" not in logged  # the log keeps no address: it could never forget it
    saved = next(iter(store.events(kinds=("console.channel.saved",))))
    assert saved.payload == {
        "name": "phone",
        "channel": "ntfy",
        "carries": ["digest"],
        "secrets": ["token"],
        "created": True,
        "by": "admin",
    }
    edit = client.get("/ui/channels/phone/edit", headers=ADMIN).text
    assert "tk_very_secret_value" not in edit
    assert "Kept. Type a new one to replace it." in edit
    assert 'value="https://ntfy.example.org"' in edit


def test_editing_keeps_a_secret_that_is_not_typed_again(store: Store) -> None:
    outbox = Outbox()
    client = app_for(store, PROXIED_UI, outbox)
    client.post("/ui/channels/add/ntfy", data=NTFY, headers=ADMIN)
    changed = {
        "url": "https://ntfy.example.org",
        "topic": "alerts",
        "carry_investigation": "on",
        "then": "test",
    }
    assert client.post("/ui/channels/phone/edit", data=changed, headers=ADMIN).status_code == 303
    assert outbox.requests[-1].headers["authorization"] == "Bearer tk_very_secret_value"
    assert json.loads(outbox.requests[-1].content)["topic"] == "alerts"
    row = store.console_channels()[0]
    assert row.stages == ("investigation",)
    assert row.config["topic"] == "alerts"

    client.post("/ui/channels/phone/edit", data={**changed, "forget_token": "on"}, headers=ADMIN)
    assert "authorization" not in outbox.requests[-1].headers
    assert store.console_channels()[0].secrets == {}


def test_what_is_wrong_is_said_on_the_form(store: Store) -> None:
    client = app_for(store, PROXIED_UI, notifiers={"house": {"type": "slack", "webhook_env": "X"}})
    bad_name = client.post(
        "/ui/channels/add/ntfy", data={**NTFY, "name": "My Phone"}, headers=ADMIN
    )
    assert bad_name.status_code == 422
    assert "lower-case letters" in bad_name.text
    assert 'value="https://ntfy.example.org"' in bad_name.text  # what was typed is kept
    assert "tk_very_secret_value" not in bad_name.text  # but never a secret

    taken = client.post("/ui/channels/add/ntfy", data={**NTFY, "name": "house"}, headers=ADMIN)
    assert "The rules file already defines a channel of this name." in taken.text

    no_token = client.post(
        "/ui/channels/add/telegram",
        data={"name": "tg", "chat_id": "42", "api_url": "https://api.telegram.org"},
        headers=ADMIN,
    )
    assert no_token.status_code == 422
    assert "Bot token is needed." in no_token.text

    not_a_url = client.post(
        "/ui/channels/add/gotify",
        data={"name": "g", "url": "gotify.local", "token": "x" * 12},
        headers=ADMIN,
    )
    assert "starts with http:// or https://" in not_a_url.text

    mail = {
        "name": "mail",
        "host": "smtp.example.org",
        "port": "587",
        "security": "starttls",
        "username": "paeon",
        "sender": "paeon@example.org",
        "to": "me@example.org, you@example.org",
    }
    half = client.post("/ui/channels/add/email", data=mail, headers=ADMIN)
    assert half.status_code == 422
    assert "Login and password go together" in half.text
    assert store.console_channels() == []

    client.post("/ui/channels/add/ntfy", data=NTFY, headers=ADMIN)
    twice = client.post("/ui/channels/add/ntfy", data=NTFY, headers=ADMIN)
    assert "A channel of this name already exists." in twice.text


def test_a_failed_test_says_so_and_keeps_the_channel(store: Store) -> None:
    outbox = Outbox()
    outbox.status = 403
    client = app_for(store, PROXIED_UI, outbox)
    saved = client.post("/ui/channels/add/ntfy", data={**NTFY, "then": "test"}, headers=ADMIN)
    assert saved.headers["location"] == "/ui/channels?tested=phone&ok=0"
    page = client.get(saved.headers["location"], headers=ADMIN).text
    assert "was not delivered" in page
    assert "ntfy answered HTTP 403" in page
    assert [row.name for row in store.console_channels()] == ["phone"]


def test_removing_a_channel_stops_it_and_forgets_its_secrets(store: Store) -> None:
    client = app_for(store, PROXIED_UI)
    client.post("/ui/channels/add/ntfy", data=NTFY, headers=ADMIN)
    assert client.post("/ui/channels/phone/remove", headers=OPERATOR).status_code == 403
    removed = client.post("/ui/channels/phone/remove", headers=ADMIN)
    assert removed.headers["location"] == "/ui/channels?removed=phone"
    assert store.console_channels() == []
    channels: Channels = client.app.state.channels  # type: ignore[attr-defined]
    assert list(channels.rosters["digest"]) == []
    assert client.post("/ui/channels/phone/remove", headers=ADMIN).status_code == 404
    assert client.get("/ui/channels/phone/edit", headers=ADMIN).status_code == 404
    last = list(store.events())[-1]
    assert last.kind == "console.channel.removed"
    assert last.payload["name"] == "phone"


# --- next to the rules file --------------------------------------------------------------------


def test_file_channels_are_shown_locked_and_stay_first(store: Store) -> None:
    outbox = Outbox()
    client = app_for(
        store,
        PROXIED_UI,
        outbox,
        notifiers={"house": {"type": "ntfy", "url": "https://ntfy.example.org", "topic": "h"}},
        digest={"notify": ["house"]},
    )
    client.post("/ui/channels/add/ntfy", data=NTFY, headers=ADMIN)
    channels: Channels = client.app.state.channels  # type: ignore[attr-defined]
    assert [n.name for n in channels.rosters["digest"]] == ["house", "phone"]
    page = client.get("/ui/channels", headers=ADMIN).text
    assert page.index("house") < page.index("phone")
    assert "rules file</span>" in page
    assert "/ui/channels/phone/edit" in page
    assert "/ui/channels/house/edit" not in page
    assert client.get("/ui/channels/house/edit", headers=ADMIN).status_code == 404
    # A file channel can be tested from the console, and nothing else.
    assert client.post("/ui/channels/house/test", headers=ADMIN).status_code == 303
    assert client.post("/ui/channels/house/remove", headers=ADMIN).status_code == 404


def test_a_console_channel_never_carries_approvals(store: Store) -> None:
    assert "actions" not in STAGES
    client = app_for(store, PROXIED_UI)
    client.post("/ui/channels/add/ntfy", data={**NTFY, "carry_actions": "on"}, headers=ADMIN)
    channels: Channels = client.app.state.channels  # type: ignore[attr-defined]
    assert channels.rules.actions.notify == ()
    assert store.console_channels()[0].stages == ("digest",)


def test_with_settings_off_the_file_is_all_there_is(store: Store) -> None:
    client = app_for(store, PROXIED_UI)
    client.post("/ui/channels/add/ntfy", data=NTFY, headers=ADMIN)
    off = app_for(store, {"enabled": True})
    assert off.app.state.channels is None  # type: ignore[attr-defined]
    assert "phone" not in off.get("/ui/channels").text


def test_a_channel_that_can_no_longer_be_read_is_set_aside(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app_for(store, PROXIED_UI)
    client.post("/ui/channels/add/ntfy", data=NTFY, headers=ADMIN)
    client.post("/ui/channels/add/ntfy", data={**NTFY, "name": "open", "token": ""}, headers=ADMIN)
    rules = parse_rules({"ui": PROXIED_UI})
    other = Channels(store, rules, Vault("a different key " * 3), base={})
    assert [n.name for n in other.rosters["digest"]] == ["open"]
    shown = {one.name: one.broken for one in other.shown()}
    assert shown["open"] is None
    assert shown["phone"] == "its secrets cannot be read with this PAEON_SECRET_KEY"

    claimed = parse_rules(
        {
            "ui": PROXIED_UI,
            "notifiers": {"open": {"type": "slack", "webhook_env": "X"}},
        }
    )
    again = Channels(store, claimed, Vault(KEY), base={})
    assert [n.name for n in again.rosters["digest"]] == ["phone"]
    assert again.find("open").broken == "the rules file now defines a channel of this name"  # type: ignore[union-attr]


def test_the_log_says_who_changed_what(store: Store) -> None:
    ui = {**PROXIED_UI, "require_header": "X-authentik-username"}
    client = app_for(store, ui)
    headers = {**ADMIN, "X-authentik-username": "marco"}
    client.post("/ui/channels/add/ntfy", data=NTFY, headers=headers)
    log = client.get("/ui/log", headers=headers).text
    assert "Channel phone (ntfy) added in the console by marco: carries digest" in log


@pytest.mark.parametrize("kind", sorted(SERVICES))
def test_each_service_form_renders(store: Store, kind: str) -> None:
    def page() -> str:
        return app_for(store, PROXIED_UI).get(f"/ui/channels/add/{kind}", headers=ADMIN).text

    text = page()
    for spec in SERVICES[kind].fields:
        assert f'name="{spec.key}"' in text
    assert "Approvals are not on this list." in text


def test_a_kept_secret_does_not_follow_a_channel_to_a_new_address(store: Store) -> None:
    outbox = Outbox()
    client = app_for(store, PROXIED_UI, outbox)
    client.post("/ui/channels/add/ntfy", data=NTFY, headers=ADMIN)
    moved = {"url": "https://collector.example.net", "topic": "paeon", "then": "test"}
    refused = client.post("/ui/channels/phone/edit", data=moved, headers=ADMIN)
    assert refused.status_code == 422
    assert "Type it again. Where it is sent changed" in refused.text
    assert outbox.requests == []
    assert store.console_channels()[0].config["url"] == "https://ntfy.example.org"

    # Typed again, or given up, the channel moves.
    typed = client.post(
        "/ui/channels/phone/edit", data={**moved, "token": "tk_another_value"}, headers=ADMIN
    )
    assert typed.status_code == 303
    assert outbox.requests[-1].url.host == "collector.example.net"
    assert outbox.requests[-1].headers["authorization"] == "Bearer tk_another_value"
    back = {**moved, "url": "https://ntfy.example.org", "forget_token": "on"}
    assert client.post("/ui/channels/phone/edit", data=back, headers=ADMIN).status_code == 303
    assert "authorization" not in outbox.requests[-1].headers


def test_wrong_passwords_from_one_address_do_not_keep_another_out() -> None:
    sessions = Sessions(KEY, PASSWORD)
    for _ in range(5):
        assert sessions.sign_in("nope", "203.0.113.9") is None
    assert sessions.locked("203.0.113.9") > 0
    assert sessions.sign_in(PASSWORD, "203.0.113.9") is None
    assert sessions.locked("192.0.2.4") == 0
    assert sessions.sign_in(PASSWORD, "192.0.2.4") is not None


def test_a_retyped_webhook_address_takes_no_kept_secret_along(store: Store) -> None:
    outbox = Outbox()
    client = app_for(store, PROXIED_UI, outbox)
    hook = {
        "name": "hook",
        "url": "https://n8n.example.org/hook/abc",
        "authorization": "Bearer kept_secret_value",
        "then": "save",
    }
    assert client.post("/ui/channels/add/webhook", data=hook, headers=ADMIN).status_code == 303
    moved = {"url": "https://collector.example.net/x", "then": "test"}
    refused = client.post("/ui/channels/hook/edit", data=moved, headers=ADMIN)
    assert refused.status_code == 422
    assert "Type it again. Where it is sent changed" in refused.text
    assert outbox.requests == []
    # Nothing typed: nothing moved, and the kept secrets still go where they went.
    same = client.post("/ui/channels/hook/edit", data={"then": "test"}, headers=ADMIN)
    assert same.status_code == 303
    assert outbox.requests[-1].url.host == "n8n.example.org"
    assert outbox.requests[-1].headers["authorization"] == "Bearer kept_secret_value"


def test_many_addresses_do_not_get_five_tries_each() -> None:
    sessions = Sessions(KEY, PASSWORD)
    for number in range(50):
        assert sessions.sign_in("nope", f"198.51.100.{number}") is None
    assert sessions.locked("192.0.2.4") > 0
    assert sessions.sign_in(PASSWORD, "192.0.2.4") is None


def test_one_ipv6_network_is_one_client() -> None:
    sessions = Sessions(KEY, PASSWORD)
    for number in range(5):
        sessions.sign_in("nope", f"2a00:1450:1:2::{number + 1}")
    assert sessions.locked("2a00:1450:1:2:ffff::9") > 0
    assert sessions.locked("2a00:1450:1:3::1") == 0


def test_an_ipv4_client_on_a_dual_stack_socket_is_itself() -> None:
    sessions = Sessions(KEY, PASSWORD)
    for _ in range(5):
        sessions.sign_in("nope", "::ffff:203.0.113.9")
    assert sessions.locked("203.0.113.9") > 0
    assert sessions.locked("::ffff:203.0.113.10") == 0
