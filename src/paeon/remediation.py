"""The core's side of stages 3 and 4: it proposes, and it mirrors what the executor decided.

The core cannot act and cannot approve. From an investigation's findings, or from a runbook
that matched an alert, it builds a plan made of catalog actions and hands it to the executor,
which checks it again against its own rules and decides everything from there. Whatever the
executor then does comes back through its journal, which is copied into the event log so that
the incident's record is in one place.

When the executor cannot be reached nothing is lost and nothing is hidden: a runbook's incident
falls back to investigation, and the analysis of an investigation has already been sent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx2 as httpx

from paeon.catalog import Proposal, Step
from paeon.investigation import Extras, Findings
from paeon.rules import Rules
from paeon.schema import Incident, IncidentStatus, StageState
from paeon.store import Store

log = logging.getLogger(__name__)

RUNBOOK_CURSOR = "remediation.runbooks"
JOURNAL_CURSOR = "remediation.journal"
GIVE_UP = timedelta(minutes=10)
_CLOSED = ("approval.granted", "approval.declined", "approval.expired", "plan.cancelled")


def pending_approvals(store: Store, now: datetime) -> list[dict[str, Any]]:
    """The plans that wait for a Go or a No-go, from the mirrored journal."""
    waiting = []
    for asked in store.events(kind="approval.requested", recorded_since=now - timedelta(days=2)):
        proposal = asked.payload.get("proposal")
        answered = any(
            event.payload.get("proposal") == proposal
            for event in store.events(incident_id=asked.incident_id, kinds=_CLOSED)
        )
        if answered or asked.incident_id is None:
            continue
        plan = next(
            (
                event.payload
                for event in store.events(incident_id=asked.incident_id, kind="plan.proposed")
                if event.payload.get("proposal") == proposal
            ),
            {},
        )
        waiting.append(
            {
                "incident": asked.incident_id,
                "proposal": proposal,
                "steps": plan.get("steps") or [],
                "asked_at": asked.recorded_at.isoformat(),
                "expires_at": asked.payload.get("expires_at"),
            }
        )
    return waiting


class ExecutorError(Exception):
    """The executor did not answer, or refused the request. Carries no URL and no token."""


@dataclass(frozen=True)
class Journal:
    """A page of the executor's journal, and the id of its last entry."""

    events: list[dict[str, Any]]
    head: int


class ExecutorApi(Protocol):
    def propose(self, proposal: Proposal) -> dict[str, Any]: ...

    def cancel(self, proposal_id: int, reason: str) -> None: ...

    def journal(self, after: int) -> Journal: ...

    def kill(self) -> None: ...

    def vote_links(self, incident_id: int, about: str) -> dict[str, str]: ...


class ExecutorClient:
    def __init__(
        self, url: str, token: str, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
            transport=transport,
        )

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ExecutorError(f"unreachable: {type(exc).__name__}") from None
        if response.status_code >= 400:
            raise ExecutorError(f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError:
            raise ExecutorError("the answer is not JSON") from None

    def propose(self, proposal: Proposal) -> dict[str, Any]:
        answer = self._call("POST", "/proposals", json=proposal.model_dump(mode="json"))
        return answer if isinstance(answer, dict) else {}

    def cancel(self, proposal_id: int, reason: str) -> None:
        self._call("POST", f"/proposals/{proposal_id}/cancel", json={"reason": reason})

    def journal(self, after: int) -> Journal:
        answer = self._call("GET", "/journal", params={"after": after})
        events = answer.get("events") if isinstance(answer, dict) else None
        head = answer.get("head") if isinstance(answer, dict) else None
        return Journal(
            events=[event for event in events or [] if isinstance(event, dict)],
            head=head if isinstance(head, int) else after,
        )

    def kill(self) -> None:
        self._call("POST", "/kill-switch")

    def vote_links(self, incident_id: int, about: str) -> dict[str, str]:
        answer = self._call(
            "POST", "/links/vote", json={"incident_id": incident_id, "about": about}
        )
        return {k: str(v) for k, v in answer.items()} if isinstance(answer, dict) else {}

    def close(self) -> None:
        self._http.close()


class Remediator:
    def __init__(self, store: Store, rules: Rules, executor: ExecutorApi) -> None:
        self._store = store
        self._rules = rules
        self._executor = executor

    # --- proposing ------------------------------------------------------------------------

    def _alerts(self, incident: Incident) -> tuple[str, ...]:
        return tuple(dict.fromkeys(alert.title for alert in incident.alerts)) or (incident.title,)

    def on_findings(self, incident: Incident, findings: Findings) -> None:
        """Hand the typed steps of an analysis to the executor. The rest stays advice.

        Configuration changes go first and on their own: they become a pull request, while
        the actions wait for a Go. The plan of actions is then the incident's latest plan.
        """
        steps = [
            Step(action=step.catalog_action, target=step.target, params=step.params)
            for step in findings.plan
            if step.catalog_action is not None and step.target is not None
        ]
        for group in (
            [step for step in steps if step.tier >= 2],
            [step for step in steps if step.tier < 2],
        ):
            if group:
                self._propose(
                    Proposal(
                        incident_id=incident.id,
                        title=incident.title[:300],
                        origin="investigation",
                        alerts=self._alerts(incident)[:50],
                        confidence=findings.root_cause.confidence,
                        summary=findings.summary[:1500],
                        steps=tuple(group[:10]),
                    )
                )

    def _propose(self, proposal: Proposal) -> dict[str, Any] | None:
        try:
            answer = self._executor.propose(proposal)
        except ExecutorError as exc:
            self._store.append(
                "plan.not_proposed",
                {"reason": str(exc), "steps": [step.label for step in proposal.steps]},
                incident_id=proposal.incident_id,
            )
            return None
        return answer

    def page(self, incident_id: int) -> str | None:
        base = self._rules.ui.public_url
        return None if base is None else f"{base.rstrip('/')}/ui/incidents/{incident_id}"

    def buttons(self, incident_id: int, about: str, right: str, wrong: str) -> Extras:
        """The incident's page, and two signed buttons that record a thumbs-up or down."""
        try:
            links = self._executor.vote_links(incident_id, about)
        except ExecutorError:
            links = {}
        actions = tuple(
            self._rules.actions.button(label, links[vote])
            for vote, label in (("up", right), ("down", wrong))
            if vote in links
        )
        return Extras(click=self.page(incident_id), actions=actions)

    def extras(self, incident: Incident) -> Extras:
        """What an analysis carries: was the root cause right?"""
        return self.buttons(incident.id, "root_cause", "Cause is right", "Cause is wrong")

    def stop(self) -> None:
        """Engage the kill switch. The core can stop automation; it cannot resume it."""
        self._executor.kill()

    # --- the tick -------------------------------------------------------------------------

    def run_pending(self, now: datetime) -> int:
        handled = self._runbooks(now)
        try:
            handled += self.mirror()
        except ExecutorError as exc:
            log.warning("executor journal not read (%s)", exc)
            return handled
        self._cancel_resolved(now)
        return handled

    def _runbooks(self, now: datetime) -> int:
        """Propose the plan of every runbook that matched, once its incident has settled."""
        settle = timedelta(seconds=self._rules.actions.settle_seconds)
        cursor = int(self._store.cursor(RUNBOOK_CURSOR) or 0)
        handled = 0
        for event in self._store.events(kind="runbook.matched", after_id=cursor):
            incident = (
                None if event.incident_id is None else self._store.incident(event.incident_id)
            )
            if incident is not None and now - incident.opened_at < settle:
                break  # in order: the next tick takes it
            if incident is not None and incident.status is IncidentStatus.RESOLVED:
                self._store.append(
                    "runbook.skipped", {"reason": "resolved by itself"}, incident_id=incident.id
                )
            elif incident is not None:
                book = next(
                    (b for b in self._rules.actions.runbooks if b.name == event.payload["runbook"]),
                    None,
                )
                answer = None
                if book is not None:
                    try:
                        answer = self._executor.propose(
                            Proposal(
                                incident_id=incident.id,
                                title=incident.title[:300],
                                origin="runbook",
                                runbook=book.name,
                                mode=book.mode,
                                alerts=self._alerts(incident)[:50],
                                confidence=1.0,
                                summary=f"Runbook {book.name} matched this alert.",
                                steps=tuple(
                                    Step(action=s.action, target=s.target) for s in book.steps
                                ),
                            )
                        )
                    except ExecutorError as exc:
                        if now - incident.opened_at < GIVE_UP:
                            break  # the executor may be restarting: try again
                        answer = {"status": "refused", "reason": str(exc)}
                if answer is None or answer.get("status") == "refused":
                    reason = "unknown runbook" if answer is None else answer.get("reason")
                    self._fall_back(incident, f"runbook not run: {reason}")
                elif answer.get("level") in ("observe", "suggest"):
                    # The plan is recorded and nothing will run: the alert is not handled, so
                    # the incident is looked at like any other.
                    self._fall_back(
                        incident,
                        f"its plan is at level {answer['level']}: recorded, not run",
                        kind="runbook.skipped",
                    )
            self._store.set_cursor(RUNBOOK_CURSOR, str(event.id))
            handled += 1
        return handled

    def _fall_back(self, incident: Incident, reason: str, *, kind: str = "runbook.failed") -> None:
        """A runbook that did not fix it hands the incident to the investigator."""
        self._store.append(kind, {"reason": reason}, incident_id=incident.id)
        current = self._store.incident(incident.id)
        if (
            self._rules.investigation.enabled
            and current is not None
            and current.status is IncidentStatus.OPEN
            and current.investigation is StageState.SKIPPED
        ):
            self._store.set_stage(incident.id, "investigation", StageState.PENDING)

    def mirror(self) -> int:
        """Copy the executor's new journal entries into the event log."""
        cursor = int(self._store.cursor(JOURNAL_CURSOR) or 0)
        copied = 0
        journal = self._executor.journal(cursor)
        if journal.head < cursor:
            # The executor's journal is shorter than what was already copied: its database was
            # replaced. Start again from its beginning rather than wait for ids to catch up.
            self._store.append("executor.journal_restarted", {"was": cursor, "now": journal.head})
            cursor = 0
            self._store.set_cursor(JOURNAL_CURSOR, "0")
            journal = self._executor.journal(0)
        for entry in journal.events:
            entry_id, kind = entry.get("id"), entry.get("kind")
            if not isinstance(entry_id, int) or not isinstance(kind, str) or entry_id <= cursor:
                continue
            payload = entry.get("payload")
            payload = dict(payload) if isinstance(payload, dict) else {}
            payload["executor_event"] = entry_id
            payload["executor_at"] = entry.get("at")
            incident_id = entry.get("incident_id")
            incident_id = incident_id if isinstance(incident_id, int) else None
            self._store.append(kind, payload, source="executor", incident_id=incident_id)
            cursor = entry_id
            self._store.set_cursor(JOURNAL_CURSOR, str(cursor))
            copied += 1
            if kind == "plan.completed" and incident_id is not None:
                self._completed(incident_id, payload)
        return copied

    def _completed(self, incident_id: int, payload: dict[str, Any]) -> None:
        incident = self._store.incident(incident_id)
        if incident is None:
            return
        if payload.get("outcome") != "verified" and payload.get("origin") == "runbook":
            self._fall_back(incident, "the runbook's fix did not verify")
        if self._rules.postmortems.enabled and incident.postmortem is StageState.OFF:
            # An incident Paeon acted on gets a postmortem, investigated or not.
            self._store.set_stage(incident_id, "postmortem", StageState.PENDING)

    def _cancel_resolved(self, now: datetime) -> None:
        """An approval for an incident that resolved by itself is dead: say so to the executor."""
        day_ago = now - timedelta(days=1)
        for asked in self._store.events(kind="approval.requested", recorded_since=day_ago):
            proposal = asked.payload.get("proposal")
            if asked.incident_id is None or not isinstance(proposal, int):
                continue
            incident = self._store.incident(asked.incident_id)
            if incident is None or incident.status is not IncidentStatus.RESOLVED:
                continue
            closed = any(
                event.payload.get("proposal") == proposal
                for event in self._store.events(
                    incident_id=asked.incident_id, kinds=_CLOSED, after_id=asked.id
                )
            )
            if not closed:
                try:
                    self._executor.cancel(proposal, "the incident resolved before an answer")
                except ExecutorError:
                    return
