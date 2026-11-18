"""Stages 3 and 4: approval and execution. Plain code, no model, its own credentials.

The executor is a separate process (`paeon executor`). The core reads attacker-controlled text
and talks to a model, so the executor does not trust it: a proposal is only a request. Here it
is checked again, against this process's own copy of the catalog and of the policies:

- an action that is not in the catalog, or a target the catalog does not allow, is refused;
- the autonomy level comes from the policies of this process, never from the request;
- an approval is a signed link that only this process can mint, sent straight to the owner's
  phone: the core never sees it, so it cannot approve its own plan;
- before anything is applied, the world is checked again (does the container exist, is it in
  the state the action expects), under a lock that other repairers can share;
- after it is applied, the result is verified, rolled back where the action has an inverse,
  and the plan stops at the first failure.

Everything it decides is an entry in its own append-only journal, which the core mirrors into
the event log.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2 as httpx

from paeon import autonomy, digest
from paeon.catalog import ACTIONS, Proposal, Step
from paeon.links import LinkError, Signer
from paeon.rules import Level, Rules, Verify
from paeon.store import Store

log = logging.getLogger(__name__)

POLL_SECONDS = 5.0
RESULT_LINK_HOURS = 24
# A plan in one of these states was allowed to run: it counts against the limits.
_SPENT = ("approved", "running", "done", "failed", "undone")

_PROPOSALS = """
CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    hash        TEXT NOT NULL,
    status      TEXT NOT NULL,
    level       TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT
);
CREATE INDEX IF NOT EXISTS proposals_incident ON proposals (incident_id, id);
CREATE TABLE IF NOT EXISTS used_links (
    nonce   TEXT PRIMARY KEY,
    used_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Pending:
    id: int
    incident_id: int
    hash: str
    status: str
    level: str
    proposal: Proposal
    created_at: datetime
    expires_at: datetime | None


class ExecutorStore(Store):
    """The executor's own file: its journal (the `events` table) and the state of each plan."""

    def __init__(self, path: Path | str) -> None:
        super().__init__(path)
        with self._lock:
            self._db.executescript(_PROPOSALS)

    def add_proposal(
        self,
        proposal: Proposal,
        *,
        status: str,
        level: str,
        now: datetime,
        expires_at: datetime | None,
    ) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO proposals (incident_id, hash, status, level, body, created_at,"
                " expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    proposal.incident_id,
                    proposal.hash,
                    status,
                    level,
                    proposal.model_dump_json(),
                    now.isoformat(),
                    None if expires_at is None else expires_at.isoformat(),
                ),
            )
        return int(cursor.lastrowid or 0)

    def proposals(
        self,
        *,
        proposal_id: int | None = None,
        incident_id: int | None = None,
        status: str | None = None,
    ) -> list[Pending]:
        sql = (
            "SELECT id, incident_id, hash, status, level, body, created_at, expires_at"
            " FROM proposals WHERE 1 = 1"
        )
        params: list[Any] = []
        for clause, value in (
            (" AND id = ?", proposal_id),
            (" AND incident_id = ?", incident_id),
            (" AND status = ?", status),
        ):
            if value is not None:
                sql += clause
                params.append(value)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY id", params).fetchall()
        return [
            Pending(
                id=row[0],
                incident_id=row[1],
                hash=row[2],
                status=row[3],
                level=row[4],
                proposal=Proposal.model_validate_json(row[5]),
                created_at=datetime.fromisoformat(row[6]),
                expires_at=None if row[7] is None else datetime.fromisoformat(row[7]),
            )
            for row in rows
        ]

    def move(self, proposal_id: int, *, to: str, only_from: tuple[str, ...]) -> bool:
        """Change a plan's status if it is still in one of `only_from`. One caller wins."""
        marks = ", ".join("?" * len(only_from))
        with self._lock:
            cursor = self._db.execute(
                f"UPDATE proposals SET status = ? WHERE id = ? AND status IN ({marks})",  # noqa: S608
                (to, proposal_id, *only_from),
            )
        return cursor.rowcount == 1

    def use_link(self, nonce: str, now: datetime) -> bool:
        """Remember a link's nonce. False if it was used before."""
        with self._lock:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO used_links (nonce, used_at) VALUES (?, ?)",
                (nonce, now.isoformat()),
            )
        return cursor.rowcount == 1


class DockerActuator:
    """The few Docker calls that change something, through a proxy that allows only those."""

    def __init__(self, http: httpx.Client, read: httpx.Client | None = None) -> None:
        # Two endpoints when the proxy that may act allows nothing else: with the usual socket
        # proxies, "POST on /containers" also means create, delete and exec.
        self._http = http
        self._read = read or http

    def close(self) -> None:
        self._http.close()
        self._read.close()

    def find(self, name: str) -> tuple[str, dict[str, Any]] | None:
        """The id and state of the container with exactly this name."""
        listing = self._read.get("/containers/json", params={"all": "true"})
        if listing.status_code >= 400:
            raise RuntimeError(f"docker answered HTTP {listing.status_code} to the listing")
        for entry in listing.json():
            names = [str(n).lstrip("/") for n in entry.get("Names") or []]
            container_id = entry.get("Id")
            if name in names and isinstance(container_id, str) and container_id.isalnum():
                return container_id, self.state(container_id)
        return None

    def state(self, container_id: str) -> dict[str, Any]:
        data = self._read.get(f"/containers/{container_id}/json").json()
        state = data.get("State") or {}
        return {
            "running": bool(state.get("Running")),
            "paused": bool(state.get("Paused")),
            "restarting": bool(state.get("Restarting")),
            "health": (state.get("Health") or {}).get("Status"),
            "restart_count": data.get("RestartCount"),
        }

    def apply(self, container_id: str, verb: str) -> None:
        params = {"t": "10"} if verb in ("restart", "stop") else {}
        response = self._http.post(f"/containers/{container_id}/{verb}", params=params)
        if response.status_code >= 400:
            raise RuntimeError(f"docker answered HTTP {response.status_code}")


@contextlib.contextmanager
def target_lock(directory: str, target: str) -> Iterator[bool]:
    """One writer per container. The lock is a file other repair scripts can `flock` too."""
    Path(directory).mkdir(parents=True, exist_ok=True)
    descriptor = os.open(Path(directory) / f"{target}.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        os.close(descriptor)


def _http_ok(url: str) -> bool:
    try:
        return httpx.get(url, timeout=10, follow_redirects=False).status_code < 400
    except httpx.HTTPError:
        return False


@dataclass(frozen=True)
class Outcome:
    """What a link did, for the page that answers it."""

    ok: bool
    message: str


class Executor:
    def __init__(
        self,
        store: ExecutorStore,
        rules: Rules,
        *,
        signer: Signer | None = None,
        docker: DockerActuator | None = None,
        ntfy_token: str | None = None,
        kill_env: bool = False,
        publish: Callable[..., None] = digest.publish_ntfy,
        sleep: Callable[[float], None] = time.sleep,
        http_ok: Callable[[str], bool] = _http_ok,
        spawn: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._store = store
        self._rules = rules
        self._actions = rules.actions
        self._signer = signer
        self._docker = docker
        self._ntfy_token = ntfy_token
        self._kill_env = kill_env
        self._publish = publish
        self._sleep = sleep
        self._http_ok = http_ok
        self._spawn = spawn or self._thread
        self._lock = threading.Lock()

    @staticmethod
    def _thread(job: Callable[[], None]) -> None:
        threading.Thread(target=job, daemon=True, name="paeon-execute").start()

    def _journal(self, kind: str, payload: dict[str, Any], incident_id: int | None) -> None:
        self._store.append(kind, payload, incident_id=incident_id)

    # --- state ----------------------------------------------------------------------------

    def killed(self) -> bool:
        """The kill switch: with it on, every plan is recorded and nothing else happens."""
        if self._kill_env:
            return True
        return autonomy.killed(
            self._store.events(kinds=("killswitch.engaged", "killswitch.released"))
        )

    def can_ask(self) -> bool:
        actions = self._actions
        return self._signer is not None and bool(actions.public_url) and actions.ntfy is not None

    def state(self, now: datetime) -> dict[str, Any]:
        return {
            "kill_switch": self.killed(),
            "kill_switch_env": self._kill_env,
            "can_ask": self.can_ask(),
            "can_act": self._docker is not None,
            "pending": [
                {
                    "proposal": p.id,
                    "incident": p.incident_id,
                    "steps": [step.label for step in p.proposal.steps],
                    "expires_at": None if p.expires_at is None else p.expires_at.isoformat(),
                }
                for p in self._store.proposals(status="pending")
            ],
        }

    def head(self) -> int:
        """The id of the last journal entry, so that the core notices a journal that restarted."""
        return next((e.id for e in self._store.events(newest_first=True, limit=1)), 0)

    def recover(self) -> int:
        """Close the plans a restart of this process interrupted. Nothing is resumed: a plan
        that was half-way is a person's to look at."""
        interrupted = [
            plan
            for status in ("approved", "running")
            for plan in self._store.proposals(status=status)
        ]
        for plan in interrupted:
            if not self._store.move(plan.id, to="failed", only_from=("approved", "running")):
                continue  # pragma: no cover - nothing else runs at start
            reason = "the executor restarted while the plan was running"
            self._journal(
                "plan.completed",
                {
                    "proposal": plan.id,
                    "outcome": "failed",
                    "level": plan.level,
                    "origin": plan.proposal.origin,
                    "steps": [step.label for step in plan.proposal.steps],
                    "tuples": self._tuples(plan.proposal, plan.proposal.steps),
                    "rolled_back": False,
                    "reason": reason,
                },
                plan.incident_id,
            )
            self._demote(plan.proposal, plan.proposal.steps, reason)
            self._report(plan, plan.level, False, [], list(plan.proposal.steps), reason)
        return len(interrupted)

    def journal(self, after: int, limit: int = 500) -> list[dict[str, Any]]:
        return [
            {
                "id": event.id,
                "at": event.recorded_at.isoformat(),
                "kind": event.kind,
                "incident_id": event.incident_id,
                "payload": event.payload,
            }
            for event in self._store.events(after_id=after, limit=limit)
        ]

    # --- a proposal comes in --------------------------------------------------------------

    def propose(self, proposal: Proposal, now: datetime) -> dict[str, Any]:
        with self._lock:
            return self._propose(proposal, now)

    def _propose(self, proposal: Proposal, now: datetime) -> dict[str, Any]:
        incident = proposal.incident_id
        refusal = self._refusal(proposal)
        if refusal is not None:
            self._journal(
                "plan.refused",
                {"reason": refusal, "steps": [s.label for s in proposal.steps]},
                incident,
            )
            return {"status": "refused", "reason": refusal}
        earlier = self._store.proposals(incident_id=incident)
        for known in earlier:
            if known.hash == proposal.hash and known.status not in ("cancelled", "expired"):
                # The core retries: the same plan for the same incident is the same proposal.
                return {"status": known.status, "proposal": known.id, "level": known.level}
        level, reasons = self._level(proposal, earlier, now)
        for known in earlier:
            if known.status == "pending" and self._store.move(
                known.id, to="cancelled", only_from=("pending",)
            ):
                self._journal(
                    "plan.cancelled", {"proposal": known.id, "reason": "superseded"}, incident
                )
        waits = level == "approve"
        expires = now + timedelta(minutes=self._actions.approval_minutes) if waits else None
        status = {"approve": "pending", "auto": "approved"}.get(level, "recorded")
        proposal_id = self._store.add_proposal(
            proposal, status=status, level=level, now=now, expires_at=expires
        )
        self._journal(
            "plan.proposed",
            {
                "proposal": proposal_id,
                "hash": proposal.hash,
                "origin": proposal.origin,
                "runbook": proposal.runbook,
                "mode": proposal.mode,
                "level": level,
                "held_back_by": reasons,
                "steps": [step.label for step in proposal.steps],
                "tuples": self._tuples(proposal, proposal.steps),
                "confidence": proposal.confidence,
            },
            incident,
        )
        if level == "approve" and expires is not None:
            self._ask(proposal_id, proposal, expires, now)
        elif level == "auto":
            self._spawn(lambda: self.execute(proposal_id, "auto"))
        return {"status": status, "proposal": proposal_id, "level": level}

    def _refusal(self, proposal: Proposal) -> str | None:
        """Why a plan cannot even be considered. Checked against this process's catalog."""
        for step in proposal.steps:
            kind = ACTIONS.get(step.action)
            entry = self._actions.catalog.get(step.action)
            if kind is None or not kind.proposable or entry is None:
                return f"{step.action} is not in the catalog"
            if kind.tier != 1:  # pragma: no cover - every action in code is tier 1 today
                return f"{step.action} is not a tier 1 action"
            if not entry.allows(step.target):
                return f"{step.action} is not allowed on {step.target}"
        if proposal.origin == "runbook":
            steps = [(step.action, step.target) for step in proposal.steps]
            known = any(
                book.name == proposal.runbook
                and [(s.action, s.target) for s in book.steps] == steps
                and book.mode == proposal.mode
                for book in self._actions.runbooks
            )
            if not known:
                return "this is not a runbook of the executor's rules file"
        return None

    def _tuples(self, proposal: Proposal, steps: Any) -> list[list[str]]:
        return [
            [alert, step.action, step.target]
            for step in steps
            for alert in dict.fromkeys(proposal.alerts)
        ]

    def _level(
        self, proposal: Proposal, earlier: list[Pending], now: datetime
    ) -> tuple[Level, list[str]]:
        """The level the policies give this plan, and what held it under that."""
        actions = self._actions
        if not actions.enabled:
            return "observe", ["actions are not enabled"]
        if self.killed():
            return "observe", ["the kill switch is engaged"]
        demoted = autonomy.demotions(
            self._store.events(kinds=("autonomy.demoted", "autonomy.reinstated"))
        )
        levels: list[Level] = []
        reasons: list[str] = []
        for alert, action, target in self._tuples(proposal, proposal.steps):
            level = actions.level_for(alert, action, target)
            if level == "auto" and (alert, action, target) in demoted:
                level = "approve"
                reasons.append(f"{action}({target}) is demoted: {demoted[(alert, action, target)]}")
            levels.append(level)
        level = autonomy.lowest(levels)

        def cap(to: Level, why: str) -> None:
            nonlocal level
            if autonomy.RANK[level] > autonomy.RANK[to]:
                level = to
                reasons.append(why)

        if self._docker is None:
            cap("suggest", "the executor has no Docker endpoint")
        # Counted from the plans themselves, not from their completion: a burst of proposals
        # must hit the limit before the first of them has finished running.
        hour_ago = now - timedelta(hours=1)
        ran = sum(
            1
            for known in self._store.proposals()
            if known.status in _SPENT and known.created_at >= hour_ago
        )
        if ran >= actions.max_per_hour:
            cap("suggest", "too many plans ran in the last hour")
        if len(set(proposal.alerts)) > 1:
            cap("approve", "the incident has more than one alert class")
        if proposal.confidence < actions.autonomy.min_confidence:
            cap("approve", "the confidence is under the threshold")
        if any(known.status in _SPENT for known in earlier):
            cap("approve", "a plan already ran for this incident")
        if level == "approve" and not self.can_ask():
            cap("suggest", "there is no approval channel (signing key, public_url, ntfy)")
        return level, reasons

    # --- approval -------------------------------------------------------------------------

    def _url(self, kind: str, expires_at: datetime, **data: Any) -> str:
        if self._signer is None or not self._actions.public_url:  # pragma: no cover - guarded
            raise RuntimeError("links cannot be minted")
        token = self._signer.mint(kind, expires_at=expires_at, **data)
        return f"{self._actions.public_url.rstrip('/')}/act/{token}"

    def _button(self, label: str, url: str) -> dict[str, Any]:
        # An `http` action: the phone POSTs the link. Opening it (a GET) only shows a page.
        return {"action": "http", "label": label, "url": url, "method": "POST", "clear": True}

    def _details(self, incident_id: int) -> str | None:
        base = self._rules.ui.public_url
        return None if base is None else f"{base.rstrip('/')}/ui/incidents/{incident_id}"

    def _notify(
        self,
        incident_id: int,
        about: str,
        *,
        title: str,
        message: str,
        actions: list[dict[str, Any]],
        priority: int = 4,
        tags: tuple[str, ...] = ("wrench",),
    ) -> None:
        ntfy = self._actions.ntfy
        if ntfy is None:
            return
        try:
            self._publish(
                ntfy,
                self._ntfy_token,
                title=title,
                message=message,
                priority=priority,
                tags=tags,
                click=self._details(incident_id),
                actions=actions,
            )
        except RuntimeError as exc:
            self._journal(
                "notification.failed",
                {"channel": "ntfy", "about": about, "error": str(exc)},
                incident_id,
            )
            return
        self._journal(
            "notification.sent",
            {"channel": "ntfy", "about": about, "topic": ntfy.topic},
            incident_id,
        )

    def _ask(self, proposal_id: int, proposal: Proposal, expires: datetime, now: datetime) -> None:
        bound = {"p": proposal_id, "h": proposal.hash}
        buttons = [
            self._button("Go", self._url("go", expires, **bound)),
            self._button("No-go", self._url("nogo", expires, **bound)),
        ]
        self._journal(
            "approval.requested",
            {"proposal": proposal_id, "expires_at": expires.isoformat()},
            proposal.incident_id,
        )
        minutes = round(self._actions.approval_minutes)
        self._notify(
            proposal.incident_id,
            "approval",
            title=f"Incident #{proposal.incident_id}: run this fix?",
            message="\n".join(
                [
                    proposal.title,
                    proposal.summary,
                    "",
                    "Plan"
                    + (", stopping at the first step that fixes it" if _escalates(proposal) else "")
                    + ":",
                    *_plain(proposal.steps),
                    "",
                    f"Nothing runs without Go. The buttons work once, for {minutes} minutes.",
                ]
            ),
            actions=buttons,
        )

    def describe(self, token: str, now: datetime) -> str:
        """What a link would do, for the page a GET shows. Uses nothing up."""
        payload = self._read(token, now)
        kind = payload.get("k")
        if kind == "vote":
            right = "right" if payload.get("v") == "up" else "wrong"
            return f"Record that the {_about(payload)} of incident #{payload.get('i')} was {right}."
        if kind == "kill":
            return "Stop automation: every plan is only recorded until you resume."
        plan = self._plan(payload)
        steps = ", ".join(step.label for step in plan.proposal.steps)
        verb = {
            "go": "Run",
            "nogo": "Decline",
            "undo": "Undo",
            "wrong": "Record as wrong, and demote,",
        }[str(kind)]
        return f"{verb} the plan of incident #{plan.incident_id}: {steps}."

    def _read(self, token: str, now: datetime) -> dict[str, Any]:
        if self._signer is None:
            raise LinkError("this executor has no signing key")
        payload = self._signer.read(token, now)
        if payload.get("k") not in ("go", "nogo", "undo", "wrong", "kill", "vote"):
            raise LinkError("this link is not valid")
        return payload

    def _plan(self, payload: dict[str, Any]) -> Pending:
        found = self._store.proposals(proposal_id=int(payload.get("p") or 0))
        if not found or found[0].hash != payload.get("h"):
            raise LinkError("this link is for a plan that no longer exists")
        return found[0]

    def act(self, token: str, now: datetime) -> Outcome:
        """Apply a link. Only a POST reaches this."""
        try:
            with self._lock:
                return self._act(token, now)
        except LinkError as exc:
            return Outcome(False, str(exc))

    def _act(self, token: str, now: datetime) -> Outcome:
        payload = self._read(token, now)
        kind = str(payload["k"])
        plan = self._plan(payload) if kind in ("go", "nogo", "undo", "wrong") else None
        if not self._store.use_link(str(payload.get("n")), now):
            raise LinkError("this link was already used")
        if kind == "vote":
            self._journal(
                "feedback.recorded",
                {"about": payload.get("a"), "vote": payload.get("v"), "note": "", "via": "link"},
                int(payload.get("i") or 0),
            )
            return Outcome(True, "Recorded. Thank you.")
        if kind == "kill":
            self.engage_kill("link")
            return Outcome(True, "Automation is stopped. Plans are only recorded.")
        if plan is None:  # pragma: no cover - the kinds above are exhaustive
            raise LinkError("this link is not valid")
        incident = plan.incident_id
        if kind == "go":
            if self.killed():
                raise LinkError("automation is stopped: nothing was run")
            if not self._store.move(plan.id, to="approved", only_from=("pending",)):
                raise LinkError(f"this plan is no longer waiting (it is {plan.status})")
            self._journal("approval.granted", {"proposal": plan.id, "via": "link"}, incident)
            self._spawn(lambda: self.execute(plan.id, "approve"))
            return Outcome(True, "Approved. The plan is running; the result follows.")
        if kind == "nogo":
            if not self._store.move(plan.id, to="declined", only_from=("pending",)):
                raise LinkError(f"this plan is no longer waiting (it is {plan.status})")
            self._journal("approval.declined", {"proposal": plan.id, "via": "link"}, incident)
            return Outcome(True, "Declined. Nothing was run.")
        if plan.status not in ("done", "failed"):
            raise LinkError(f"this plan did not run (it is {plan.status})")
        if kind == "undo":
            return self._undo(plan)
        self._journal(
            "feedback.recorded",
            {
                "about": "action",
                "vote": "down",
                "note": "",
                "via": "link",
                "proposal": plan.id,
                "tuples": self._tuples(plan.proposal, plan.proposal.steps),
            },
            incident,
        )
        self._demote(plan.proposal, plan.proposal.steps, "you said the action was wrong")
        return Outcome(True, "Recorded as wrong. These actions ask for approval from now on.")

    def cancel(self, proposal_id: int, reason: str) -> bool:
        found = self._store.proposals(proposal_id=proposal_id)
        if not found or not self._store.move(proposal_id, to="cancelled", only_from=("pending",)):
            return False
        self._journal(
            "plan.cancelled",
            {"proposal": proposal_id, "reason": reason[:200]},
            found[0].incident_id,
        )
        return True

    def expire(self, now: datetime) -> int:
        """Close the approvals nobody answered. Nothing happens; one reminder says so."""
        closed = 0
        for plan in self._store.proposals(status="pending"):
            if plan.expires_at is None or plan.expires_at > now:
                continue
            if not self._store.move(plan.id, to="expired", only_from=("pending",)):
                continue  # pragma: no cover - answered at this very moment
            closed += 1
            self._journal("approval.expired", {"proposal": plan.id}, plan.incident_id)
            self._notify(
                plan.incident_id,
                "expiry",
                title=f"Incident #{plan.incident_id}: no answer, nothing was run",
                message="The approval expired. The incident is still yours to handle:\n"
                + "\n".join(_plain(plan.proposal.steps)),
                actions=[],
                priority=3,
                tags=("hourglass",),
            )
        return closed

    def engage_kill(self, via: str) -> None:
        self._journal("killswitch.engaged", {"via": via}, None)
        for plan in self._store.proposals(status="pending"):
            self.cancel(plan.id, "the kill switch was engaged")

    def vote_links(self, incident_id: int, about: str, now: datetime) -> dict[str, str]:
        """Two links that record a thumbs-up or a thumbs-down on an incident."""
        if not self.can_ask():
            return {}
        expires = now + timedelta(days=7)
        return {
            vote: self._url("vote", expires, i=incident_id, a=about, v=vote)
            for vote in ("up", "down")
        }

    # --- execution ------------------------------------------------------------------------

    def execute(self, proposal_id: int, level: str) -> None:
        try:
            self._execute(proposal_id, level)
        except Exception:
            log.exception("plan %d: execution crashed", proposal_id)
            self._store.move(proposal_id, to="failed", only_from=("running",))

    def _execute(self, proposal_id: int, level: str) -> None:
        plan = self._store.proposals(proposal_id=proposal_id)[0]
        proposal = plan.proposal
        incident = plan.incident_id
        if self.killed():
            if self._store.move(proposal_id, to="cancelled", only_from=("approved",)):
                self._journal(
                    "plan.cancelled",
                    {"proposal": proposal_id, "reason": "the kill switch was engaged"},
                    incident,
                )
            return
        if not self._store.move(proposal_id, to="running", only_from=("approved",)):
            return  # another thread took it, or it was cancelled
        ran: list[Step] = []
        fixed: list[Step] = []
        failure: str | None = None
        rolled_back = False
        for step in proposal.steps:
            ran.append(step)
            self._journal("action.started", {"proposal": proposal_id, "step": step.label}, incident)
            ok, reason, undone = self._apply(step)
            if ok:
                fixed.append(step)
                self._journal(
                    "action.verified", {"proposal": proposal_id, "step": step.label}, incident
                )
                if proposal.mode == "escalate":
                    failure = None
                    break
                continue
            failure = reason
            self._journal(
                "action.failed",
                {"proposal": proposal_id, "step": step.label, "reason": reason},
                incident,
            )
            if undone:
                rolled_back = True
                self._journal(
                    "action.rolled_back", {"proposal": proposal_id, "step": step.label}, incident
                )
            if proposal.mode == "sequence":
                break  # the plan stops at the first failure
        good = failure is None and bool(fixed)
        self._store.move(proposal_id, to="done" if good else "failed", only_from=("running",))
        self._journal(
            "plan.completed",
            {
                "proposal": proposal_id,
                "outcome": "verified" if good else "failed",
                "level": level,
                "origin": proposal.origin,
                "steps": [step.label for step in ran],
                "tuples": self._tuples(proposal, ran),
                "rolled_back": rolled_back,
                "reason": failure,
            },
            incident,
        )
        if not good:
            self._demote(proposal, ran, "a verification failed")
        self._report(plan, level, good, fixed, ran, failure)

    def _apply(self, step: Step) -> tuple[bool, str, bool]:
        """Pre-check, apply, verify, roll back. Returns (worked, why not, rolled back)."""
        kind = ACTIONS[step.action]
        entry = self._actions.catalog.get(step.action)
        docker = self._docker
        if entry is None or docker is None or not entry.allows(step.target):
            return False, "not allowed by the catalog", False  # pragma: no cover - checked before
        with target_lock(self._actions.lock_dir, step.target) as held:
            if not held:
                return False, "another repair holds this container's lock", False
            try:
                found = docker.find(step.target)
                if found is None:
                    return False, "no such container", False
                container_id, before = found
                # Is the world still as the plan assumed?
                if step.action == "start_container" and before["running"]:
                    return False, "it is already running", False
                if step.action == "unpause_container" and not before["paused"]:
                    return False, "it is not paused", False
                docker.apply(container_id, kind.verb)
                ok, reason = self._verify(docker, container_id, entry.verify)
                if ok or kind.inverse is None:
                    return ok, reason, False
                docker.apply(container_id, ACTIONS[kind.inverse].verb)
                return False, reason, True
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                return False, f"docker: {detail}", False

    def _verify(
        self, docker: DockerActuator, container_id: str, verify: Verify
    ) -> tuple[bool, str]:
        """The action's own health check: the container is up, healthy, and the URL answers."""
        self._sleep(verify.wait_seconds)
        reason = "not checked"
        for _ in range(max(int(verify.timeout_seconds // POLL_SECONDS), 1)):
            state = docker.state(container_id)
            if not state["running"] or state["restarting"] or state["paused"]:
                reason = "the container is not running"
            elif state["health"] == "starting":
                reason = (
                    "its health check had not answered yet"
                    f" (verify.timeout_seconds is {verify.timeout_seconds:g})"
                )
            elif state["health"] not in (None, "", "healthy"):
                reason = f"its health check says {state['health']}"
            elif verify.http is not None and not self._http_ok(verify.http):
                reason = "the health URL does not answer"
            else:
                return True, ""
            self._sleep(POLL_SECONDS)
        return False, reason

    def _undo(self, plan: Pending) -> Outcome:
        docker = self._docker
        steps = [step for step in plan.proposal.steps if ACTIONS[step.action].inverse]
        if docker is None or not steps or plan.status != "done":
            raise LinkError("this plan has nothing that can be undone")
        if not self._store.move(plan.id, to="undone", only_from=("done",)):
            raise LinkError("this plan was already undone")  # pragma: no cover - a race
        for step in reversed(steps):
            inverse = ACTIONS[str(ACTIONS[step.action].inverse)]
            with target_lock(self._actions.lock_dir, step.target) as held:
                found = docker.find(step.target) if held else None
                if found is not None:
                    docker.apply(found[0], inverse.verb)
            self._journal(
                "action.undone",
                {
                    "proposal": plan.id,
                    "step": step.label,
                    "applied": found is not None,
                    "tuples": self._tuples(plan.proposal, [step]),
                },
                plan.incident_id,
            )
        self._demote(plan.proposal, steps, "you undid the action")
        return Outcome(True, "Undone. These actions ask for approval from now on.")

    def _demote(self, proposal: Proposal, steps: Any, reason: str) -> None:
        """Send every `auto` tuple of these steps back to `approve`."""
        for alert, action, target in self._tuples(proposal, steps):
            if self._actions.level_for(alert, action, target) == "auto":
                self._journal(
                    "autonomy.demoted",
                    {"alert": alert, "action": action, "target": target, "reason": reason},
                    proposal.incident_id,
                )

    def _report(
        self,
        plan: Pending,
        level: str,
        good: bool,
        fixed: list[Step],
        ran: list[Step],
        failure: str | None,
    ) -> None:
        """Tell the owner what was done. After `auto` this is the first they hear of it."""
        buttons: list[dict[str, Any]] = []
        if self.can_ask():
            expires = datetime.now(UTC) + timedelta(hours=RESULT_LINK_HOURS)
            bound = {"p": plan.id, "h": plan.hash}
            if good and any(ACTIONS[step.action].inverse for step in fixed):
                buttons.append(self._button("Undo", self._url("undo", expires, **bound)))
            buttons.append(self._button("This was wrong", self._url("wrong", expires, **bound)))
            buttons.append(self._button("Stop automation", self._url("kill", expires)))
        how = "without asking (auto)" if level == "auto" else "after your Go"
        lines = [plan.proposal.title, "", f"Ran {how}:", *_plain(ran), ""]
        lines.append(
            "Verified: the container is up and healthy. The alert itself may take a while to clear."
            if good
            else f"It did not work: {failure}. Nothing more will be tried; this one is yours."
        )
        self._notify(
            plan.incident_id,
            "result",
            title=f"Incident #{plan.incident_id}: " + ("fix applied" if good else "fix failed"),
            message="\n".join(lines),
            actions=buttons,
            priority=3 if good else 5,
            tags=("white_check_mark",) if good else ("rotating_light",),
        )


def _plain(steps: Any) -> list[str]:
    return [
        f"{number}. {ACTIONS[step.action].summary} ({step.target})"
        for number, step in enumerate(steps, 1)
    ]


def _escalates(proposal: Proposal) -> bool:
    return proposal.mode == "escalate" and len(proposal.steps) > 1


def _about(payload: dict[str, Any]) -> str:
    return "root cause" if payload.get("a") == "root_cause" else "triage verdict"
