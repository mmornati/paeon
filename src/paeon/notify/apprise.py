"""Apprise: one adapter for the services Paeon has none for. An optional install.

Apprise is a large library with its own code for each service. It is not a dependency of
Paeon: `pip install 'paeon[apprise]'` adds it, and it is imported only when a channel of this
type is configured.
"""

from __future__ import annotations

import importlib
from typing import Any

from paeon.notify import Message, NotifyError, with_links
from paeon.rules import AppriseChannel


class AppriseNotifier:
    kind = "apprise"
    where = "apprise"  # the URLs hold the services' secrets: they are not recorded

    def __init__(self, name: str, config: AppriseChannel, urls: str) -> None:
        self.name = name
        try:
            self._module: Any = importlib.import_module("apprise")
        except ImportError:
            raise NotifyError(
                f"notifiers.{name} is of type apprise, which is not installed:"
                " pip install 'paeon[apprise]'"
            ) from None
        self._client = self._module.Apprise()
        added = [self._client.add(url) for url in urls.split()]
        if not added or not all(added):
            raise NotifyError(f"notifiers.{name}: an Apprise URL was not understood")

    def send(self, message: Message) -> None:
        kinds = self._module.NotifyType
        kind = kinds.WARNING if message.priority >= 4 else kinds.INFO
        try:
            delivered = self._client.notify(
                title=message.title,
                body=with_links(message),
                notify_type=kind,
                # Said to be text, so that Apprise escapes it for a service that renders
                # markup: an alert's words must not become a link of their own.
                body_format=self._module.NotifyFormat.TEXT,
            )
        except Exception as exc:
            raise NotifyError(f"apprise failed: {type(exc).__name__}") from None
        if not delivered:
            raise NotifyError("apprise could not deliver")
