"""Each service's adapter: what it posts, what it does with a button, what it never repeats."""

from __future__ import annotations

import json
import smtplib
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from paeon import notify
from paeon.config import ConfigError, load
from paeon.notify import Button, Message, NotifyError
from paeon.notify.email import EmailNotifier
from paeon.rules import EmailChannel, RulesError, parse_rules

GO = "https://act.example.org/act/go-link"
NOGO = "https://act.example.org/act/nogo-link"
PAGE = "https://paeon.example.org/ui/incidents/7"
MESSAGE = Message(
    title="Incident #7: run this fix?",
    text="worker is down <@everyone> & co\nPlan:\n1. Restart a container (worker)",
    priority=4,
    tags=("wrench",),
    click=PAGE,
    buttons=(Button("Go", GO, one_tap=True), Button("No-go", NOGO, one_tap=True)),
)
QUIET = Message(title="Paeon digest", text="2 incidents", priority=2)

CHANNELS: dict[str, dict[str, Any]] = {
    "telegram": {"type": "telegram", "chat_id": "-100123", "token_env": "T"},
    "discord": {"type": "discord", "webhook_env": "T", "username": "Paeon"},
    "slack": {"type": "slack", "webhook_env": "T"},
    "webhook": {"type": "webhook", "url_env": "T", "authorization_env": "A"},
}
SECRETS = {
    "telegram": {"token": "123:not-a-real-token"},
    "discord": {"webhook": "https://discord.example.org/api/webhooks/1/not-a-real-secret"},
    "slack": {"webhook": "https://hooks.slack.example.org/services/not-a-real-secret"},
    "webhook": {
        "url": "https://hook.example.org/in/not-a-real-secret",
        "authorization": "Bearer x",
    },
}


def channel(kind: str, handler: Any) -> notify.Notifier:
    rules = parse_rules({"notifiers": {kind: CHANNELS[kind]}})
    return notify.build(
        kind, rules.notifiers[kind], SECRETS[kind], transport=httpx.MockTransport(handler)
    )


def sent_by(kind: str, message: Message) -> tuple[httpx.Request, dict[str, Any]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    channel(kind, handler).send(message)
    (request,) = seen
    return request, json.loads(request.content)


def test_telegram_posts_plain_text_with_a_keyboard_of_links() -> None:
    request, body = sent_by("telegram", MESSAGE)
    assert str(request.url) == "https://api.telegram.org/bot123:not-a-real-token/sendMessage"
    assert body["chat_id"] == "-100123"
    assert body["text"].startswith("Incident #7: run this fix?\n\nworker is down <@everyone>")
    assert "parse_mode" not in body  # an alert's text is never read as markup
    assert body["disable_notification"] is False
    assert body["reply_markup"]["inline_keyboard"] == [
        [{"text": "Go", "url": GO}, {"text": "No-go", "url": NOGO}],
        [{"text": "Details", "url": PAGE}],
    ]
    _, quiet = sent_by("telegram", QUIET)
    assert quiet["disable_notification"] is True
    assert "reply_markup" not in quiet
    _, long = sent_by("telegram", Message(title="t", text="x" * 9000))
    assert len(long["text"]) == 4096


def test_discord_posts_an_embed_with_link_buttons_and_pings_nobody() -> None:
    request, body = sent_by("discord", MESSAGE)
    assert request.url.params["with_components"] == "true"
    (embed,) = body["embeds"]
    assert (embed["title"], embed["url"]) == ("Incident #7: run this fix?", PAGE)
    assert "<@everyone>" in embed["description"]
    assert body["allowed_mentions"] == {"parse": []}
    assert body["username"] == "Paeon"
    (row,) = body["components"]
    assert [(b["type"], b["style"], b["label"], b["url"]) for b in row["components"]] == [
        (2, 5, "Go", GO),
        (2, 5, "No-go", NOGO),
    ]
    _, quiet = sent_by("discord", QUIET)
    assert "components" not in quiet


def test_slack_posts_blocks_with_the_links_as_text_and_escapes_the_rest() -> None:
    _, body = sent_by("slack", MESSAGE)
    header, text, links = body["blocks"]
    assert header["text"] == {"type": "plain_text", "text": "Incident #7: run this fix?"}
    assert "&lt;@everyone&gt; &amp; co" in text["text"]["text"]  # no mention, no link of its own
    assert links["text"]["text"] == f"<{GO}|Go>  ·  <{NOGO}|No-go>  ·  <{PAGE}|Details>"
    assert body["text"] == "Incident #7: run this fix?"
    _, quiet = sent_by("slack", QUIET)
    assert len(quiet["blocks"]) == 2


def test_the_webhook_posts_the_message_as_it_is() -> None:
    request, body = sent_by("webhook", MESSAGE)
    assert str(request.url) == "https://hook.example.org/in/not-a-real-secret"
    assert request.headers["authorization"] == "Bearer x"
    assert body == {
        "source": "paeon",
        "title": MESSAGE.title,
        "text": MESSAGE.text,
        "priority": 4,
        "tags": ["wrench"],
        "click": PAGE,
        "buttons": [{"label": "Go", "url": GO}, {"label": "No-go", "url": NOGO}],
    }


@pytest.mark.parametrize("kind", sorted(CHANNELS))
def test_a_failure_names_the_service_and_never_the_url(kind: str) -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=str(request.url))

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"no route to {request.url}", request=request)

    with pytest.raises(NotifyError) as status:
        channel(kind, refused).send(MESSAGE)
    assert str(status.value) == f"{kind} answered HTTP 403"
    with pytest.raises(NotifyError) as error:
        channel(kind, down).send(MESSAGE)
    assert str(error.value) == f"{kind} request failed: ConnectError"
    built = channel(kind, refused)
    assert "not-a-real" not in f"{built.where} {built.name} {built.kind}"


# --- e-mail -----------------------------------------------------------------------------------

MAIL = {
    "type": "email",
    "host": "smtp.example.org",
    "username": "paeon",
    "password_env": "T",
    "sender": "paeon@example.org",
    "to": ["me@example.org", "you@example.org"],
}


class FakeSmtp:
    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.logins: list[tuple[str, str]] = []
        self.mails: list[Any] = []

    def __enter__(self) -> FakeSmtp:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        self.logins.append((user, password))

    def send_message(self, mail: Any) -> None:
        if self.fail is not None:
            raise self.fail
        self.mails.append(mail)


def mailer(server: FakeSmtp, **changes: Any) -> EmailNotifier:
    config = parse_rules({"notifiers": {"mail": {**MAIL, **changes}}}).notifiers["mail"]
    assert isinstance(config, EmailChannel)
    password = "not-a-real-password" if config.password_env else None
    return EmailNotifier("mail", config, password, connect=lambda _: server)  # type: ignore[arg-type,return-value]


def test_email_carries_the_links_in_both_bodies_and_logs_in() -> None:
    server = FakeSmtp()
    channel = mailer(server)
    assert channel.where == "me@example.org, you@example.org"
    channel.send(MESSAGE)
    assert server.logins == [("paeon", "not-a-real-password")]
    (mail,) = server.mails
    assert (mail["Subject"], mail["From"]) == ("Incident #7: run this fix?", "paeon@example.org")
    assert mail["To"] == "me@example.org, you@example.org"
    assert (mail["X-Priority"], mail["Importance"]) == ("1", "high")
    plain = mail.get_body(("plain",)).get_content()
    assert f"Go: {GO}\nNo-go: {NOGO}\nDetails: {PAGE}" in plain
    page = mail.get_body(("html",)).get_content()
    assert f'<a href="{GO}">Go</a>' in page
    assert "&lt;@everyone&gt; &amp; co" in page  # the text is escaped: it cannot be markup

    relay = FakeSmtp()
    mailer(relay, username=None, password_env=None, security="none", port=25).send(QUIET)
    assert relay.logins == []
    assert relay.mails[0]["X-Priority"] is None


def test_a_title_cannot_add_a_header_and_a_failure_says_only_its_kind() -> None:
    server = FakeSmtp()
    mailer(server).send(Message(title="Disk full\r\nBcc: thief@example.org", text="m"))
    (mail,) = server.mails
    assert mail["Subject"] == "Disk full Bcc: thief@example.org"
    assert mail["Bcc"] is None

    refused = FakeSmtp(smtplib.SMTPRecipientsRefused({"me@example.org": (550, b"no")}))
    with pytest.raises(NotifyError) as caught:
        mailer(refused).send(MESSAGE)
    assert str(caught.value) == "email failed: SMTPRecipientsRefused"


# --- the rules and the secrets ----------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "telegram", "chat_id": "1"},  # no token
        {"type": "discord"},  # no webhook
        {"type": "slack", "webhook_env": "T", "channel": "#ops"},  # not a key
        {"type": "webhook"},  # neither url
        {"type": "webhook", "url": "https://x.example.org", "url_env": "T"},  # both
        {"type": "webhook", "url": "ftp://x.example.org"},
        {**MAIL, "to": []},
        {**MAIL, "to": ["not an address"]},
        {**MAIL, "sender": "Paeon <paeon@example.org>"},
        {**MAIL, "password_env": None},  # a user without a password
        {**MAIL, "security": "ssl"},
    ],
)
def test_a_channel_that_cannot_work_is_refused_at_load(bad: dict[str, Any]) -> None:
    with pytest.raises(RulesError):
        parse_rules({"notifiers": {"x": bad}})


def test_a_listed_channel_without_its_secret_stops_the_start(tmp_path: Path) -> None:
    path = tmp_path / "paeon.yaml"
    path.write_text(
        "notifiers:\n"
        "  chat: {type: telegram, chat_id: '1', token_env: T_CHAT}\n"
        "  unused: {type: slack, webhook_env: T_UNUSED}\n"
        "investigation: {notify: [chat]}\n"
    )
    env = {"PAEON_INGEST_TOKEN": "t" * 32, "PAEON_CONFIG": str(path)}
    with pytest.raises(ConfigError, match=r"notifiers\.chat: T_CHAT"):
        load(env)
    # A channel nothing lists needs nothing.
    settings = load({**env, "T_CHAT": "123:not-a-real-token"})
    assert settings.notifier_secrets == {"chat": {"token": "123:not-a-real-token"}}
    (built,) = notify.for_stage(settings.rules, "investigation", secrets=settings.notifier_secrets)
    assert (built.kind, built.where) == ("telegram", "1")


def test_email_connects_the_way_security_says(monkeypatch: pytest.MonkeyPatch) -> None:
    from paeon.notify import email as module

    opened: list[tuple[str, str, int]] = []

    class Plain(FakeSmtp):
        def __init__(self, host: str, port: int, **options: Any) -> None:
            super().__init__()
            opened.append(("plain", host, port))

        def starttls(self, **options: Any) -> None:
            opened.append(("starttls", "", 0))

    class Tls(Plain):
        def __init__(self, host: str, port: int, **options: Any) -> None:
            FakeSmtp.__init__(self)
            opened.append(("tls", host, port))

    monkeypatch.setattr(module.smtplib, "SMTP", Plain)
    monkeypatch.setattr(module.smtplib, "SMTP_SSL", Tls)
    for security, port in (("starttls", 587), ("tls", 465), ("none", 25)):
        rules = parse_rules({"notifiers": {"mail": {**MAIL, "security": security, "port": port}}})
        built = notify.build("mail", rules.notifiers["mail"], {"password": "not-a-real-password"})
        built.send(QUIET)
    assert opened == [
        ("plain", "smtp.example.org", 587),
        ("starttls", "", 0),
        ("tls", "smtp.example.org", 465),
        ("plain", "smtp.example.org", 25),
    ]
