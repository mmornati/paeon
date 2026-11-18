"""Replay: past incidents as a regression suite for prompts and models.

Every triage call recorded its context pack, and every investigation its pack, its tools and the
full result of each tool call. Replaying sends the same input to the model as it is configured
now, with the prompts as they are now, and compares:

- triage: the new verdict with the recorded one, and with what you said about it. A verdict you
  rated right that changes is a regression; a rated-right incident that would now be called
  noise is the one that must never happen.
- investigation: the model is given the recorded tool results and nothing else. A call it did
  not make at the time gets "not recorded". The new root cause and plan are shown next to the
  recorded ones; whether a cause is right is a person's call, so only two things are flagged:
  no findings at all, and a different typed plan where you had rated the cause right.

Nothing is written to the event log: a replay is not an incident, and it must not count
against the budgets. It does call the model, and it says what that cost. Run it against a
pinned model: with a gateway choosing the model, a prompt change and a routing change cannot be
told apart.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, TextIO

from paeon import triage
from paeon.investigation import Investigator, submit_spec
from paeon.models import ModelClient, ModelError, ToolSpec, complete, cost_of
from paeon.rules import ModelAlias, Rules
from paeon.schema import Severity, StageState
from paeon.store import Store

NOT_RECORDED = (
    "this call was not made in the recorded investigation: only what was read at the time"
    " can be read in a replay"
)


def _label(store: Store, incident_id: int, about: str) -> str | None:
    """What the owner last said about an incident's verdict or root cause: right or wrong."""
    for event in store.events(incident_id=incident_id, kind="feedback.recorded", newest_first=True):
        if (event.payload.get("about") or "triage") == about:
            return "right" if event.payload.get("vote") == "up" else "wrong"
    return None


# --- triage -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class TriageCase:
    incident_id: int
    title: str
    pack: Any
    verdict: str  # what the model said at the time
    label: str | None = None  # right, wrong, or not rated
    kind: str = "triage"


@dataclass(frozen=True)
class TriageResult:
    case: TriageCase
    verdict: str | None
    confidence: float | None = None
    error: str | None = None
    cost: float = 0.0

    @property
    def same(self) -> bool:
        return self.verdict == self.case.verdict

    @property
    def regression(self) -> bool:
        """It was rated right, and the answer is no longer that one (or there is none)."""
        return self.case.label == "right" and not self.same

    @property
    def missed(self) -> bool:
        """A verdict rated right that was not noise, and would now be called noise."""
        return self.regression and self.verdict == "noise"


def triage_cases(store: Store, *, since: datetime | None = None) -> list[TriageCase]:
    """One case per incident whose triage call was recorded with its input and its verdict."""
    cases = []
    for event in store.events(kind="triage.verdict", recorded_since=since):
        verdict = event.payload.get("verdict")
        if event.incident_id is None or not isinstance(verdict, str):
            continue  # failed open before any answer: nothing to compare with
        call = next(
            (
                c.payload
                for c in store.events(incident_id=event.incident_id, kind="model.call")
                if c.payload.get("stage") == "triage" and c.payload.get("input") is not None
            ),
            None,
        )
        incident = store.incident(event.incident_id)
        if call is None or incident is None:
            continue
        cases.append(
            TriageCase(
                incident_id=event.incident_id,
                title=incident.title,
                pack=call["input"],
                verdict=verdict,
                label=_label(store, event.incident_id, "triage"),
            )
        )
    return cases


def replay_triage(
    cases: Iterable[TriageCase], client: ModelClient, alias: ModelAlias
) -> list[TriageResult]:
    results = []
    for case in cases:
        user = json.dumps(case.pack, indent=1, sort_keys=True)
        cost = 0.0
        outcome = TriageResult(case, None, error="invalid_output")
        for _ in range(2):  # as triage does: one retry on an answer that is not a verdict
            try:
                reply = complete(client, system=triage.SYSTEM_PROMPT, user=user)
            except ModelError as exc:
                outcome = TriageResult(case, None, error=str(exc), cost=cost)
                break
            cost += cost_of(reply, alias)
            try:
                output = triage.parse_output(reply.text)
            except ValueError:
                outcome = TriageResult(case, None, error="invalid_output", cost=cost)
                continue
            outcome = TriageResult(case, output.verdict.value, output.confidence, cost=cost)
            break
        results.append(outcome)
    return results


# --- investigation ----------------------------------------------------------------------------


@dataclass(frozen=True)
class InvestigationCase:
    incident_id: int
    title: str
    pack: Any
    specs: list[dict[str, Any]]  # the tools as they were offered
    calls: list[dict[str, Any]]  # name, input, result, is_error, in order
    findings: dict[str, Any]
    label: str | None = None
    kind: str = "investigation"


@dataclass(frozen=True)
class InvestigationResult:
    case: InvestigationCase
    findings: dict[str, Any] | None
    failure: str | None = None
    asked: int = 0  # tool calls the model made
    unrecorded: int = 0  # of which had no recorded result
    cost: float = 0.0

    @staticmethod
    def _typed(findings: dict[str, Any] | None) -> list[tuple[str, str]]:
        plan = (findings or {}).get("plan") or []
        return [
            (str(step.get("catalog_action")), str(step.get("target")))
            for step in plan
            if isinstance(step, dict) and step.get("catalog_action")
        ]

    @property
    def same_plan(self) -> bool:
        return self._typed(self.findings) == self._typed(self.case.findings)

    @property
    def regression(self) -> bool:
        if self.findings is None:
            return True
        return self.case.label == "right" and not self.same_plan

    @property
    def confidence(self) -> float | None:
        cause = (self.findings or {}).get("root_cause") or {}
        value = cause.get("confidence")
        return float(value) if isinstance(value, int | float) else None


def investigation_cases(
    store: Store, *, since: datetime | None = None
) -> tuple[list[InvestigationCase], int]:
    """The investigations that can be replayed, and how many cannot: those recorded before
    the tools' definitions were kept with them."""
    cases, skipped = [], 0
    for done in store.events(kind="investigation.completed", recorded_since=since):
        incident_id = done.incident_id
        findings = done.payload.get("findings")
        if incident_id is None or not isinstance(findings, dict):
            continue
        started = next(
            iter(
                store.events(
                    incident_id=incident_id,
                    kind="investigation.started",
                    before_id=done.id,
                    newest_first=True,
                    limit=1,
                )
            ),
            None,
        )
        incident = store.incident(incident_id)
        specs = None if started is None else started.payload.get("specs")
        if started is None or incident is None or not isinstance(specs, list):
            skipped += 1
            continue
        calls = [
            {key: call.payload.get(key) for key in ("name", "input", "result", "is_error")}
            for call in store.events(incident_id=incident_id, kind="tool.call", after_id=started.id)
            if call.id < done.id
        ]
        cases.append(
            InvestigationCase(
                incident_id=incident_id,
                title=incident.title,
                pack=started.payload.get("input"),
                specs=[spec for spec in specs if isinstance(spec, dict)],
                calls=calls,
                findings=findings,
                label=_label(store, incident_id, "root_cause"),
            )
        )
    return cases, skipped


def _key(name: str, args: Any) -> str:
    return name + json.dumps(args, sort_keys=True, default=str)


class RecordedToolbox:
    """Answers tool calls from what an investigation read at the time, and from nothing else."""

    def __init__(self, case: InvestigationCase) -> None:
        self.specs = [
            ToolSpec(
                str(s.get("name")), str(s.get("description")), dict(s.get("input_schema") or {})
            )
            for s in case.specs
        ]
        self._results: dict[str, list[tuple[Any, bool]]] = {}
        for call in case.calls:
            key = _key(str(call.get("name")), call.get("input"))
            self._results.setdefault(key, []).append(
                (call.get("result"), bool(call.get("is_error")))
            )
        self.asked = 0
        self.unrecorded = 0

    def call(self, name: str, args: dict[str, Any]) -> tuple[Any, bool]:
        self.asked += 1
        recorded = self._results.get(_key(name, args))
        if not recorded:
            self.unrecorded += 1
            return {"error": NOT_RECORDED}, True
        # The same call made twice at the time gave two results: give them in that order.
        return recorded.pop(0) if len(recorded) > 1 else recorded[0]


def replay_investigation(
    cases: Iterable[InvestigationCase], client: ModelClient, rules: Rules
) -> list[InvestigationResult]:
    results = []
    for case in cases:
        scratch = Store(":memory:")  # the loop records its calls: not in the real log
        try:
            toolbox = RecordedToolbox(case)
            incident_id = scratch.open_incident(
                title=case.title,
                severity=Severity.UNKNOWN,
                host=None,
                opened_at=datetime.fromisoformat("2000-01-01T00:00:00+00:00"),
                maintenance=None,
                triage=StageState.OFF,
            )
            incident = scratch.incident(incident_id)
            if incident is None:  # pragma: no cover - it was just inserted
                continue
            investigator = Investigator(scratch, rules, client, toolbox)  # type: ignore[arg-type]
            # The model must end with the tool it ended with then, as it is defined now.
            ending = submit_spec(dict(rules.actions.catalog) if rules.actions.enabled else {})
            specs = [spec for spec in toolbox.specs if spec.name != ending.name]
            findings, failure, _ = investigator._loop(
                incident,
                rules.investigation.model,
                client,
                case.pack,
                [*specs, ending],
                [],
                time.monotonic(),
            )
            cost = sum(
                float(event.payload.get("cost") or 0) for event in scratch.events(kind="model.call")
            )
            results.append(
                InvestigationResult(
                    case,
                    None if findings is None else findings.model_dump(mode="json"),
                    failure,
                    asked=toolbox.asked,
                    unrecorded=toolbox.unrecorded,
                    cost=round(cost, 6),
                )
            )
        finally:
            scratch.close()
    return results


# --- a corpus on disk, and the report ---------------------------------------------------------


def write_corpus(cases: Sequence[TriageCase | InvestigationCase], out: TextIO) -> int:
    """One JSON line per case: a corpus to keep in git, away from the live database."""
    for case in cases:
        out.write(json.dumps(asdict(case), sort_keys=True, default=str) + "\n")
    return len(cases)


def read_corpus(lines: Iterable[str]) -> tuple[list[TriageCase], list[InvestigationCase]]:
    triaged: list[TriageCase] = []
    investigated: list[InvestigationCase] = []
    for line in lines:
        if not line.strip():
            continue
        data = json.loads(line)
        if data.get("kind") == "investigation":
            investigated.append(InvestigationCase(**data))
        else:
            triaged.append(TriageCase(**data))
    return triaged, investigated


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    regressions: int = 0
    cost: float = 0.0

    @property
    def failed(self) -> bool:
        return self.regressions > 0


def report_triage(results: Sequence[TriageResult]) -> Report:
    report = Report(cost=round(sum(result.cost for result in results), 6))
    rated = [result for result in results if result.case.label == "right"]
    for result in results:
        case = result.case
        now = result.verdict or f"no verdict ({result.error})"
        mark = "MISSED" if result.missed else "REGRESSION" if result.regression else "ok"
        if not result.regression and not result.same:
            mark = "changed"
        rating = f", you said {case.label}" if case.label else ""
        report.lines.append(
            f"{mark:<10} #{case.incident_id} {case.title}: {case.verdict} -> {now}{rating}"
        )
    report.regressions = sum(1 for result in results if result.regression)
    missed = sum(1 for result in results if result.missed)
    same = sum(1 for result in results if result.same)
    report.lines += [
        "",
        f"{len(results)} verdicts replayed: {same} unchanged, {len(results) - same} changed.",
        f"Of the {len(rated)} you rated right, {len(rated) - report.regressions} still are;"
        f" {missed} would now be called noise.",
    ]
    return report


def report_investigation(results: Sequence[InvestigationResult], skipped: int = 0) -> Report:
    report = Report(cost=round(sum(result.cost for result in results), 6))
    for result in results:
        case = result.case
        mark = "REGRESSION" if result.regression else "ok" if result.same_plan else "changed"
        rating = f", you said the cause was {case.label}" if case.label else ""
        report.lines.append(f"{mark:<10} #{case.incident_id} {case.title}{rating}")
        was = (case.findings.get("root_cause") or {}).get("statement")
        report.lines.append(f"           was: {was}")
        if result.findings is None:
            report.lines.append(f"           now: no findings ({result.failure})")
        else:
            now = (result.findings.get("root_cause") or {}).get("statement")
            report.lines.append(f"           now: {now} ({result.confidence:.0%} sure)")
        report.lines.append(
            f"           {result.asked} tool calls, {result.unrecorded} not in the record"
        )
    report.regressions = sum(1 for result in results if result.regression)
    report.lines += ["", f"{len(results)} investigations replayed, {report.regressions} regressed."]
    if skipped:
        report.lines.append(
            f"{skipped} older ones cannot be replayed: their tools' definitions were not kept."
        )
    return report
