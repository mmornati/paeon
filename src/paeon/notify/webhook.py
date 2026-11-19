"""A plain webhook: the message as JSON, for a receiver of your own."""

from __future__ import annotations

import httpx2 as httpx

from paeon.notify import Message, post
from paeon.rules import WebhookChannel


class WebhookNotifier:
    kind = "webhook"
    where = "webhook"  # the URL may hold a secret: it is not recorded

    def __init__(
        self,
        name: str,
        config: WebhookChannel,
        *,
        url: str,
        authorization: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self._url = url
        self._headers = {"Authorization": authorization} if authorization else {}
        self._transport = transport

    def send(self, message: Message) -> None:
        body = {
            "source": "paeon",
            "title": message.title,
            "text": message.text,
            "priority": message.priority,
            "tags": list(message.tags),
            "click": message.click,
            "buttons": [{"label": b.label, "url": b.url} for b in message.buttons],
        }
        post("webhook", self._url, body, headers=self._headers, transport=self._transport)
