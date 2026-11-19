"""E-mail through an SMTP server, with the standard library. Links are in the body."""

from __future__ import annotations

import html
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from paeon.notify import Message, NotifyError, links
from paeon.rules import EmailChannel

_TIMEOUT = 20
_URGENT = 4  # from this priority, the mail is flagged as important


def _one_line(text: str) -> str:
    """A header's value: an alert's title cannot start a header of its own."""
    return " ".join(text.split())[:200]


def _connect(config: EmailChannel) -> smtplib.SMTP:
    if config.security == "tls":
        return smtplib.SMTP_SSL(
            config.host, config.port, timeout=_TIMEOUT, context=ssl.create_default_context()
        )
    server = smtplib.SMTP(config.host, config.port, timeout=_TIMEOUT)
    if config.security == "starttls":
        server.starttls(context=ssl.create_default_context())
    return server


class EmailNotifier:
    kind = "email"

    def __init__(
        self,
        name: str,
        config: EmailChannel,
        password: str | None,
        *,
        connect: Callable[[EmailChannel], smtplib.SMTP] = _connect,
    ) -> None:
        self.name = name
        self.where = ", ".join(config.to)
        self._config = config
        self._password = password
        self._connect = connect

    def compose(self, message: Message) -> EmailMessage:
        config = self._config
        mail = EmailMessage()
        mail["Subject"] = _one_line(message.title)
        mail["From"] = config.sender
        mail["To"] = ", ".join(config.to)
        mail["Date"] = formatdate(localtime=False)
        mail["Message-ID"] = make_msgid(domain=config.sender.rsplit("@", 1)[-1])
        mail["Auto-Submitted"] = "auto-generated"
        if message.priority >= _URGENT:
            mail["X-Priority"] = "1"
            mail["Importance"] = "high"
        found = links(message)
        plain = message.text
        if found:
            plain += "\n\n" + "\n".join(f"{label}: {url}" for label, url in found)
        mail.set_content(plain)
        # The same again as HTML, so that a link is one word to tap. Everything is escaped.
        page = f'<pre style="font:inherit;white-space:pre-wrap">{html.escape(message.text)}</pre>'
        if found:
            page += (
                "<p>"
                + " &nbsp;·&nbsp; ".join(
                    f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
                    for label, url in found
                )
                + "</p>"
            )
        mail.add_alternative(f"<html><body>{page}</body></html>", subtype="html")
        return mail

    def send(self, message: Message) -> None:
        config = self._config
        mail = self.compose(message)
        try:
            with self._connect(config) as server:
                if config.username and self._password:
                    server.login(config.username, self._password)
                server.send_message(mail)
        except (smtplib.SMTPException, OSError) as exc:
            # The server's own words may repeat an address or a login: the type says enough.
            raise NotifyError(f"email failed: {type(exc).__name__}") from None
