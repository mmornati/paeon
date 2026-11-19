"""Microsoft Teams: an Adaptive Card posted to a Workflows webhook. Buttons open the link."""

from __future__ import annotations

from typing import Any

import httpx2 as httpx

from paeon.notify import Message, links, post
from paeon.rules import TeamsChannel


class TeamsNotifier:
    kind = "teams"
    where = "webhook"  # the URL is the secret: it is not recorded

    def __init__(
        self,
        name: str,
        config: TeamsChannel,
        webhook: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self._url = webhook
        self._transport = transport

    def send(self, message: Message) -> None:
        card: dict[str, Any] = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            # Text runs, not text blocks: a block reads its text as markdown, and an alert's
            # words must not be able to draw a link next to the real ones.
            "body": [
                _run(message.title, weight="Bolder"),
                _run(message.text),
            ],
            "actions": [
                {"type": "Action.OpenUrl", "title": label, "url": url}
                for label, url in links(message)
            ],
        }
        body = {
            "type": "message",
            "attachments": [
                {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}
            ],
        }
        post("teams", self._url, body, transport=self._transport)


def _run(text: str, **style: str) -> dict[str, Any]:
    return {"type": "RichTextBlock", "inlines": [{"type": "TextRun", "text": text, **style}]}
