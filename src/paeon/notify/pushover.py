"""Pushover. It has no buttons: the links are in the message, as HTML it renders."""

from __future__ import annotations

import html
from typing import Any

import httpx2 as httpx

from paeon.notify import Message, cut, post
from paeon.rules import PushoverChannel

_URL = "https://api.pushover.net/1/messages.json"
_MAX_TITLE = 250
_MAX_MESSAGE = 1024  # markup included
_MAX_URL = 512
# Never 2 (emergency): it repeats until acknowledged and needs its own parameters.
_PRIORITY = {1: -2, 2: -1, 3: 0, 4: 1, 5: 1}


class PushoverNotifier:
    kind = "pushover"
    where = "pushover"

    def __init__(
        self,
        name: str,
        config: PushoverChannel,
        *,
        token: str,
        user: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self._base = {"token": token, "user": user}
        if config.device:
            self._base["device"] = config.device
        self._transport = transport

    def send(self, message: Message) -> None:
        anchors = "\n".join(
            f'<a href="{html.escape(b.url, quote=True)}">{html.escape(b.label)}</a>'
            for b in message.buttons
        )
        # The links must arrive whole: the text is what gives way.
        text = _fit(message.text, _MAX_MESSAGE - len(anchors) - 1)
        body: dict[str, Any] = {
            **self._base,
            "title": cut(message.title, _MAX_TITLE),
            "message": "\n".join(part for part in (text, anchors) if part) or message.title,
            "html": 1,
            "priority": _PRIORITY.get(message.priority, 0),
        }
        if message.click and len(message.click) <= _MAX_URL:
            body["url"] = message.click
            body["url_title"] = "Details"
        post("pushover", _URL, body, transport=self._transport)


def _fit(text: str, room: int) -> str:
    """The text escaped for HTML, cut so that the escaped form fits: never inside an entity."""
    kept = text
    while len(html.escape(kept)) > room and kept:
        kept = kept[: max(len(kept) - max(len(html.escape(kept)) - room, 1), 0)]
    if not kept:
        return ""
    return html.escape(kept) if kept == text else html.escape(kept[:-1]) + "…"
