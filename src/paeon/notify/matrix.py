"""Matrix: a message in a room. Matrix has no buttons; the links are in the message."""

from __future__ import annotations

import html
import uuid
from urllib.parse import quote

import httpx2 as httpx

from paeon.notify import Message, links, post, with_links
from paeon.rules import MatrixChannel


class MatrixNotifier:
    kind = "matrix"

    def __init__(
        self,
        name: str,
        config: MatrixChannel,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self.where = config.room_id
        room = quote(config.room_id, safe="")
        self._base = f"{config.homeserver.rstrip('/')}/_matrix/client/v3/rooms/{room}/send"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._transport = transport

    def send(self, message: Message) -> None:
        page = f"<strong>{html.escape(message.title)}</strong><br>"
        page += html.escape(message.text).replace("\n", "<br>")
        found = links(message)
        if found:
            page += "<br><br>" + " · ".join(
                f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
                for label, url in found
            )
        body = {
            "msgtype": "m.text",
            "body": f"{message.title}\n\n{with_links(message)}",
            "format": "org.matrix.custom.html",
            "formatted_body": page,
        }
        # The transaction id makes a retry of the same request harmless.
        url = f"{self._base}/m.room.message/{uuid.uuid4().hex}"
        post("matrix", url, body, headers=self._headers, transport=self._transport, method="PUT")
