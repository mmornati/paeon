"""Gotify: an application on a server of your own. Links are lines of the message."""

from __future__ import annotations

from typing import Any

import httpx2 as httpx

from paeon.notify import Button, Message, cut, post
from paeon.rules import GotifyChannel

_MAX_TITLE = 200


class GotifyNotifier:
    kind = "gotify"
    where = "gotify"

    def __init__(
        self,
        name: str,
        config: GotifyChannel,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self._url = f"{config.url.rstrip('/')}/message"
        self._headers = {"X-Gotify-Key": token}
        self._transport = transport

    def send(self, message: Message) -> None:
        # Gotify has no buttons. The links are text, and the message is plain: an alert's
        # words are not ours to render as markdown.
        lines = [message.text]
        if message.buttons:
            lines += ["", *(_line(button) for button in message.buttons)]
        body: dict[str, Any] = {
            "title": cut(message.title, _MAX_TITLE),
            "message": "\n".join(lines),
            "priority": message.priority * 2,  # 2 to 10 on Gotify's scale of 0 to 10
        }
        if message.click:
            body["extras"] = {"client::notification": {"click": {"url": message.click}}}
        post("gotify", self._url, body, headers=self._headers, transport=self._transport)


def _line(button: Button) -> str:
    return f"{button.label}: {button.url}"
