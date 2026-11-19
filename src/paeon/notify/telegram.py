"""Telegram: a bot posts in a chat. Buttons are an inline keyboard of links."""

from __future__ import annotations

from typing import Any

import httpx2 as httpx

from paeon.notify import Message, cut, post
from paeon.rules import TelegramChannel

_MAX_TEXT = 4096  # Telegram's limit for one message
_QUIET = 2  # at this priority and under, the message arrives without a sound


class TelegramNotifier:
    kind = "telegram"

    def __init__(
        self,
        name: str,
        config: TelegramChannel,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self.where = config.chat_id
        # The token is part of the URL: no error of this channel ever carries the URL.
        self._url = f"{config.api_url.rstrip('/')}/bot{token}/sendMessage"
        self._transport = transport

    def send(self, message: Message) -> None:
        # Plain text, no parse mode: an alert's title is not ours to interpret as markup.
        body: dict[str, Any] = {
            "chat_id": self.where,
            "text": cut(f"{message.title}\n\n{message.text}", _MAX_TEXT),
            "disable_notification": message.priority <= _QUIET,
            "link_preview_options": {"is_disabled": True},
        }
        rows: list[list[dict[str, str]]] = []
        if message.buttons:
            rows.append([{"text": b.label, "url": b.url} for b in message.buttons])
        if message.click:
            rows.append([{"text": "Details", "url": message.click}])
        if rows:
            body["reply_markup"] = {"inline_keyboard": rows}
        post("telegram", self._url, body, transport=self._transport)
