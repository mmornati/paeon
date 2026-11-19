"""ntfy: a topic on a server. The one service whose buttons can send a link in the background."""

from __future__ import annotations

from typing import Any

import httpx2 as httpx

from paeon.notify import Button, Message, post
from paeon.rules import NtfyChannel

_MAX_TEXT = 3000  # ntfy refuses bodies over 4096 bytes, buttons included
_MAX_BUTTONS = 3  # ntfy's limit


def _action(button: Button) -> dict[str, Any]:
    if button.one_tap:
        # An `http` action: the phone POSTs the link itself, without showing anything.
        return {
            "action": "http",
            "label": button.label,
            "url": button.url,
            "method": "POST",
            "clear": True,
        }
    # A `view` action: the phone opens the link. That GET changes nothing, it shows the
    # page, and the page's Confirm sends the POST.
    return {"action": "view", "label": button.label, "url": button.url, "clear": True}


class NtfyNotifier:
    kind = "ntfy"

    def __init__(
        self,
        name: str,
        config: NtfyChannel,
        token: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self.where = config.topic
        self._url = config.url
        self._token = token
        self._transport = transport

    def send(self, message: Message) -> None:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        body: dict[str, Any] = {
            "topic": self.where,
            "title": message.title[:200],
            "message": message.text[:_MAX_TEXT],
            "priority": message.priority,
            "tags": list(message.tags),
        }
        if message.click:
            body["click"] = message.click
        if message.buttons:
            body["actions"] = [_action(button) for button in message.buttons[:_MAX_BUTTONS]]
        post("ntfy", self._url, body, headers=headers, transport=self._transport)
