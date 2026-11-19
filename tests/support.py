"""Helpers shared by the tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from paeon.notify import Message, NotifyError
from paeon.rules import Rules
from paeon.schema import Alert, Severity, Status

T0 = datetime(2026, 11, 18, 8, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"


def alert(
    status: Status,
    minutes: float,
    fingerprint: str = "a",
    *,
    source: str = "test",
    title: str | None = None,
    severity: Severity = Severity.WARNING,
    labels: dict[str, str] | None = None,
    description: str = "",
) -> Alert:
    at = T0 + timedelta(minutes=minutes)
    return Alert(
        source=source,
        fingerprint=fingerprint,
        status=status,
        severity=severity,
        title=title or f"alert {fingerprint}",
        description=description,
        labels=labels or {},
        starts_at=at,
        observed_at=at,
    )


class Recording:
    """A channel that keeps what it was asked to send, in the shape of an ntfy body."""

    kind = "ntfy"

    def __init__(self, sink: list[dict[str, Any]], *, name: str = "ntfy", where: str = "incidents"):
        self.name = name
        self.where = where
        self.sink = sink
        self.fail: str | None = None  # an error to answer every message with

    def send(self, message: Message) -> None:
        if self.fail is not None:
            raise NotifyError(self.fail)
        self.sink.append(
            {
                "topic": self.where,
                "title": message.title,
                "message": message.text,
                "priority": message.priority,
                "tags": message.tags,
                "click": message.click,
                "actions": [
                    {"action": "http", "label": b.label, "url": b.url, "method": "POST"}
                    if b.one_tap
                    else {"action": "view", "label": b.label, "url": b.url}
                    for b in message.buttons
                ],
            }
        )


def recording(rules: Rules, stage: str, sink: list[dict[str, Any]]) -> list[Recording]:
    """One recording channel, if the stage has any channel in these rules."""
    return [Recording(sink)] if rules.channels(stage) else []
