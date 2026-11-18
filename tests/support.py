"""Helpers shared by the tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

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
