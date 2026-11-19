"""Discord: a channel's webhook. Buttons are link buttons, which need no application."""

from __future__ import annotations

import re
from typing import Any

import httpx2 as httpx

from paeon.notify import Message, cut, post
from paeon.rules import DiscordChannel

_MAX_TITLE = 256
_MAX_TEXT = 4096  # an embed's description
_MAX_LABEL = 80
_LINK_BUTTON = {"type": 2, "style": 5}  # a button that opens a URL: nothing is sent back
_MARKUP = re.compile(r"([\\*_~`>|\[\]()#])")
_COLOURS = {1: 0x8A8F98, 2: 0x8A8F98, 3: 0x3B82F6, 4: 0xF59E0B, 5: 0xDC2626}


def _plain(text: str) -> str:
    """The text with Discord's markdown switched off: an alert's words cannot draw a link
    labelled Go next to the real one, or hide part of the message."""
    return _MARKUP.sub(r"\\\1", text)


class DiscordNotifier:
    kind = "discord"
    where = "webhook"  # the URL names the channel and is the secret: it is not recorded

    def __init__(
        self,
        name: str,
        config: DiscordChannel,
        webhook: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self._username = config.username
        # A webhook that no application owns sends components only when asked to.
        self._url = webhook + ("&" if "?" in webhook else "?") + "with_components=true"
        self._transport = transport

    def send(self, message: Message) -> None:
        embed: dict[str, Any] = {
            "title": cut(_plain(message.title), _MAX_TITLE),
            "description": cut(_plain(message.text), _MAX_TEXT),
            "color": _COLOURS.get(message.priority, _COLOURS[3]),
        }
        if message.click:
            embed["url"] = message.click
        body: dict[str, Any] = {
            "embeds": [embed],
            # Text from a log must not ping anyone.
            "allowed_mentions": {"parse": []},
        }
        if self._username:
            body["username"] = self._username
        if message.buttons:
            body["components"] = [
                {
                    "type": 1,
                    "components": [
                        {**_LINK_BUTTON, "label": cut(b.label, _MAX_LABEL), "url": b.url}
                        for b in message.buttons[:5]
                    ],
                }
            ]
        post("discord", self._url, body, transport=self._transport)
