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


SEVERITY_RANK = {Severity.UNKNOWN: 0, Severity.INFO: 1, Severity.WARNING: 2, Severity.CRITICAL: 3}


class IncidentStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class StageState(StrEnum):
    """Where an incident stands in one stage (triage, investigation, postmortem)."""

    OFF = "off"  # the stage was not enabled when the incident opened
    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


TriageState = StageState


class IncidentAlert(BaseModel):
    """One alert attached to an incident, with its last known status."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    fingerprint: str
    title: str
    status: Status


class Incident(BaseModel):
    """A group of correlated alerts: the unit that is triaged, investigated and reviewed.

    The row is a projection kept for queries; the record is the incident's events in the log.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    status: IncidentStatus
    title: str
    severity: Severity
    host: str | None = None
    opened_at: AwareDatetime
    last_activity_at: AwareDatetime
    resolved_at: AwareDatetime | None = None
    flapping: bool = False
    maintenance: str | None = Field(
        default=None, description="Name of the maintenance window it opened in, if any."
    )
    triage: StageState = StageState.OFF
    investigation: StageState = StageState.OFF
    postmortem: StageState = StageState.OFF
    alerts: tuple[IncidentAlert, ...] = ()


class Change(BaseModel):
    """One entry of the change calendar: something that was done to the system on purpose."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9._:-]{1,128}$",
        description="A name of yours for this change, to end it later with"
        " `POST /ingest/change/<id>/end`. A change sent again with the same id replaces it.",
    )
    kind: str = Field(min_length=1, max_length=64, description="deploy, update, backup, restart…")
    title: str = Field(min_length=1, max_length=300)
    host: str | None = Field(default=None, max_length=200)
    labels: dict[str, str] = Field(default_factory=dict)
    started_at: AwareDatetime | None = Field(
        default=None, description="Defaults to the time the change is recorded."
    )
    ended_at: AwareDatetime | None = None


class ChangeEnd(BaseModel):
    """The end of a change announced earlier with an id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ended_at: AwareDatetime | None = Field(
        default=None, description="Defaults to the time the end is recorded."
    )
