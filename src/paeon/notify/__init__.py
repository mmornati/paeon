"""Notifications: one message, sent by each channel the rules file names.

A channel is a small adapter (`Notifier`) around one service: ntfy, a chat, a mailbox. Paeon
writes a `Message` once and every channel renders it as well as its service allows. A button is
only a label and a link: the approval is in the link (signed by the executor, single use,
expiring), not in the service, so any channel that can show a link can carry a Go.

The set of channels is closed and lives in this package. Nothing is loaded from outside: the
executor sends approvals itself, next to its signing key, and runs no code it did not ship.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx2 as httpx

from paeon.rules import (
    Channel,
    DiscordChannel,
    EmailChannel,
    NtfyChannel,
    Rules,
    SlackChannel,
    TelegramChannel,
)

log = logging.getLogger(__name__)

Stage = Literal["digest", "investigation", "actions"]


class NotifyError(RuntimeError):
    """A message was not delivered. The text is safe to record: no URL, no secret."""


@dataclass(frozen=True)
class Button:
    """A link with a label. `one_tap`: the link may be sent in the background by a service
    that can (ntfy); everywhere else it opens the page the link points to."""

    label: str
    url: str
    one_tap: bool = False


@dataclass(frozen=True)
class Message:
    title: str
    text: str
    priority: int = 3  # 1 (quiet) to 5 (urgent), ntfy's scale
    tags: tuple[str, ...] = ()  # emoji short names; a channel without them ignores them
    click: str | None = None  # the page a tap on the notification opens
    buttons: tuple[Button, ...] = ()


class Notifier(Protocol):
    name: str  # the channel's name in the rules file
    kind: str  # the service: "ntfy", "telegram", ...
    where: str  # what the journal says it was sent to: a topic, a chat. Never a secret.

    def send(self, message: Message) -> None:
        """Deliver, or raise NotifyError."""


@dataclass(frozen=True)
class Delivery:
    """What became of a message on one channel, for the journal."""

    name: str
    kind: str
    where: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def record(self, about: str) -> tuple[str, dict[str, str]]:
        """The journal entry: its kind and its payload."""
        payload = {"channel": self.kind, "name": self.name, "to": self.where, "about": about}
        if self.error is None:
            return "notification.sent", payload
        return "notification.failed", {**payload, "error": self.error}


def post(
    kind: str,
    url: str,
    body: object,
    *,
    headers: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """POST a JSON body. The error says the service and the status: the URL is often a secret."""
    try:
        with httpx.Client(timeout=15, transport=transport) as http:
            status = http.post(url, json=body, headers=dict(headers or {})).status_code
    except httpx.HTTPError as exc:
        raise NotifyError(f"{kind} request failed: {type(exc).__name__}") from None
    if status >= 400:
        raise NotifyError(f"{kind} answered HTTP {status}")


def cut(text: str, limit: int) -> str:
    """The text within a service's limit, with a mark where it was cut."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def links(message: Message) -> list[tuple[str, str]]:
    """Every link of a message, for a service that only has text: the buttons, then the page."""
    found = [(button.label, button.url) for button in message.buttons]
    if message.click:
        found.append(("Details", message.click))
    return found


def deliver(notifiers: Sequence[Notifier], message: Message) -> list[Delivery]:
    """Send to every channel. One that fails does not keep the others from trying."""
    done: list[Delivery] = []
    for notifier in notifiers:
        error: str | None = None
        try:
            notifier.send(message)
        except NotifyError as exc:
            error = str(exc)
        except Exception as exc:  # a channel's bug is that channel's failure, no more
            log.exception("channel %s failed", notifier.name)
            error = f"{notifier.kind} failed: {type(exc).__name__}"
        done.append(Delivery(notifier.name, notifier.kind, notifier.where, error))
    return done


def build(
    name: str,
    config: Channel,
    secrets: Mapping[str, str],
    *,
    transport: httpx.BaseTransport | None = None,
) -> Notifier:
    """The adapter for one channel of the rules file. Each is imported when it is used."""
    if isinstance(config, NtfyChannel):
        from paeon.notify.ntfy import NtfyNotifier

        return NtfyNotifier(name, config, secrets.get("token"), transport=transport)
    if isinstance(config, TelegramChannel):
        from paeon.notify.telegram import TelegramNotifier

        return TelegramNotifier(name, config, secrets["token"], transport=transport)
    if isinstance(config, DiscordChannel):
        from paeon.notify.discord import DiscordNotifier

        return DiscordNotifier(name, config, secrets["webhook"], transport=transport)
    if isinstance(config, SlackChannel):
        from paeon.notify.slack import SlackNotifier

        return SlackNotifier(name, config, secrets["webhook"], transport=transport)
    if isinstance(config, EmailChannel):
        from paeon.notify.email import EmailNotifier

        return EmailNotifier(name, config, secrets.get("password"))
    from paeon.notify.webhook import WebhookNotifier

    return WebhookNotifier(
        name,
        config,
        url=config.url or secrets["url"],
        authorization=secrets.get("authorization"),
        transport=transport,
    )


def for_stage(
    rules: Rules,
    stage: Stage,
    *,
    secrets: Mapping[str, Mapping[str, str]],
    legacy_token: str | None = None,
) -> list[Notifier]:
    """The channels a stage sends to: its `ntfy:` block, if it still has one, then `notify`."""
    found: list[Notifier] = []
    for name, config in rules.channels(stage):
        if name == rules.LEGACY:
            found.append(build(name, config, {"token": legacy_token} if legacy_token else {}))
        else:
            found.append(build(name, config, secrets.get(name, {})))
    return found
