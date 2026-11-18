"""The Alert schema: the contract every source adapter produces.

An Alert here is an *observation*: "this source says this alert is firing (or resolved) at this
time". Sources differ in how often they repeat themselves (OpenObserve logs one row per evaluation,
uptime-kuma can resend a DOWN notification), so adapters emit observations and the recorder
(`paeon.recorder`) turns them into state changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Status(StrEnum):
    FIRING = "firing"
    RESOLVED = "resolved"


class Severity(StrEnum):
    UNKNOWN = "unknown"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Alert(BaseModel):
    """One observation of one alert, normalized. Modelled on the Alertmanager webhook alert."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1, description="Adapter instance name, e.g. 'openobserve'.")
    fingerprint: str = Field(
        min_length=1,
        description="Stable identity of the alert within its source (not of one firing).",
    )
    status: Status
    severity: Severity = Severity.UNKNOWN
    title: str
    description: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    starts_at: AwareDatetime = Field(description="When the condition began, as the source saw it.")
    observed_at: AwareDatetime = Field(description="When the source made this observation.")
    source_url: str | None = None
    query: str | None = Field(default=None, description="What fired, when the source exposes it.")
    raw: dict[str, Any] = Field(default_factory=dict, description="The source's own payload.")


def from_micros(micros: int) -> datetime:
    """A UTC datetime from a Unix timestamp in microseconds (OpenObserve's unit)."""
    return datetime.fromtimestamp(micros / 1_000_000, tz=UTC)


def to_micros(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000)
