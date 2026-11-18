"""The action catalog: the closed set of things Paeon can do, and what a plan is.

A plan is data: an ordered list of steps, each naming one action of this module and one target.
There is no free-form command anywhere. The rules file (`actions.catalog`) switches actions on
for the containers it names; it cannot add an action that is not written here.

Tiers follow the design (§4). Only tier 1 exists as code: reversible, small blast radius.
Anything that changes configuration, deletes data or touches security stays a sentence in a plan
for a person to carry out.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TARGET_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"


@dataclass(frozen=True)
class ActionType:
    name: str
    tier: int
    summary: str
    verb: str  # the Docker API verb: POST /containers/{id}/<verb>
    inverse: str | None = None  # the action that undoes it, where there is one
    proposable: bool = True  # False: exists only as the inverse of another action


ACTIONS: dict[str, ActionType] = {
    kind.name: kind
    for kind in (
        ActionType("restart_container", 1, "Restart a container", "restart"),
        ActionType(
            "start_container", 1, "Start a stopped container", "start", inverse="stop_container"
        ),
        ActionType(
            "unpause_container", 1, "Resume a paused container", "unpause", "pause_container"
        ),
        ActionType("stop_container", 1, "Stop a container", "stop", proposable=False),
        ActionType("pause_container", 1, "Pause a container", "pause", proposable=False),
    )
}


class Step(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str = Field(min_length=1, max_length=64)
    target: str = Field(pattern=TARGET_PATTERN)

    @property
    def label(self) -> str:
        return f"{self.action}({self.target})"


class Proposal(BaseModel):
    """What the core asks the executor to consider. Every field is checked again there."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_id: int = Field(ge=1)
    title: str = Field(max_length=300)
    origin: Literal["investigation", "runbook"]
    runbook: str | None = Field(default=None, max_length=100)
    mode: Literal["escalate", "sequence"] = "sequence"
    alerts: tuple[str, ...] = Field(
        min_length=1, max_length=50, description="The alert classes of the incident: its titles."
    )
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(default="", max_length=1500)
    steps: tuple[Step, ...] = Field(min_length=1, max_length=10)

    @property
    def hash(self) -> str:
        """What an approval is bound to: the incident, the steps in order, and how they run."""
        body = {
            "incident": self.incident_id,
            "mode": self.mode,
            "steps": [[step.action, step.target] for step in self.steps],
        }
        text = json.dumps(body, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()
