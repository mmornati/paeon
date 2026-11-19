"""Slack: an incoming webhook. Links are in the text.

A Slack button, even one that only opens a URL, sends the click to the application and shows
a warning when nothing answers. A webhook has no application behind it: the links are text.
"""

from __future__ import annotations

from typing import Any

import httpx2 as httpx

from paeon.notify import Message, cut, links, post
from paeon.rules import SlackChannel

_MAX_HEADER = 150
_MAX_SECTION = 3000


def _escape(text: str) -> str:
    """Slack's three control characters: an alert's text cannot make a link or a mention."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SlackNotifier:
    kind = "slack"
    where = "webhook"  # the URL names the channel and is the secret: it is not recorded

    def __init__(
        self,
        name: str,
        config: SlackChannel,
        webhook: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self._url = webhook
        self._transport = transport

    def send(self, message: Message) -> None:
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": cut(message.title, _MAX_HEADER)},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": cut(_escape(message.text), _MAX_SECTION)},
            },
        ]
        found = links(message)
        if found:
            line = "  ·  ".join(f"<{url}|{_escape(label)}>" for label, url in found)
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": line}})
        # `text` is what a notification and a client without blocks show.
        body = {"text": cut(message.title, _MAX_HEADER), "blocks": blocks}
        post("slack", self._url, body, transport=self._transport)
