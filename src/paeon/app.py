"""The core: ingestion, correlation, shadow triage, investigation, postmortem drafts, digest.

The core observes and advises. It records, groups alerts into incidents, and when the stages
are enabled asks a model for a triage verdict (recorded, never acted on), investigates with
tools that only read, and drafts a postmortem. What it can send: the analysis of an
investigation and the daily digest, each only if an ntfy topic is configured for it.

It cannot act. With `actions.enabled` it hands plans made of catalog actions to the executor
(`paeon executor`, another process with other credentials), which decides and acts by itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import httpx2 as httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from paeon import autonomy
from paeon import digest as digests
from paeon import postmortem as postmortems
from paeon.config import Settings
from paeon.correlation import Correlator
from paeon.investigation import Investigator
from paeon.models import AnthropicClient, ModelClient, OpenAIClient, build_client
from paeon.postmortem import PostmortemWriter
from paeon.recorder import Recorder
from paeon.remediation import ExecutorApi, ExecutorClient, Remediator, pending_approvals
from paeon.schema import Change, IncidentStatus
from paeon.sources.openobserve import HttpSearchClient, OpenObserveSource
from paeon.sources.webhook import parse_alertmanager, parse_uptime_kuma
from paeon.store import Store
from paeon.telemetry import Exporter
from paeon.tools import (
    Tool,
    Toolbox,
    docker_tools,
    git_tools,
    openobserve_tools,
    paeon_tools,
    ssh_tools,
    uptime_kuma_tools,
)
from paeon.triage import Triager
from paeon.ui import mount_ui

log = logging.getLogger(__name__)

TRIAGE_TICK_SECONDS = 15
INVESTIGATION_TICK_SECONDS = 15
POSTMORTEM_TICK_SECONDS = 60
DIGEST_TICK_SECONDS = 300
REMEDIATION_TICK_SECONDS = 5


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vote: Literal["up", "down"]
    note: str = Field(default="", max_length=1000)


def _now() -> datetime:
    return datetime.now(UTC)


async def _every(seconds: float, name: str, job: Callable[[], Awaitable[None]]) -> None:
    """Run `job` forever. A failure is logged and retried at the next tick, never fatal."""
    while True:
        try:
            await job()
        except Exception:
            log.exception("%s failed", name)
        await asyncio.sleep(seconds)


def create_app(
    settings: Settings,
    store: Store,
    *,
    openobserve: OpenObserveSource | None = None,
    model_clients: Mapping[str, ModelClient] | None = None,
    toolbox: Toolbox | None = None,
    executor: ExecutorApi | None = None,
) -> FastAPI:
    """Build the app. `model_clients` (by alias), `toolbox` and `executor` replace the real
    ones in tests."""
    rules = settings.rules
    recorder = Recorder(store, Correlator(store, rules))
    closers: list[Callable[[], None]] = []
    built: dict[str, AnthropicClient | OpenAIClient] = {}

    def model_client(alias_name: str) -> ModelClient:
        if model_clients is not None and alias_name in model_clients:
            return model_clients[alias_name]
        if alias_name not in built:
            built[alias_name] = build_client(
                rules.models[alias_name], settings.model_keys.get(alias_name, "")
            )
            closers.append(built[alias_name].close)
        return built[alias_name]

    client: HttpSearchClient | None = None
    if settings.openobserve is not None and (
        openobserve is None or rules.investigation.tools.openobserve is not None
    ):
        oo = settings.openobserve
        client = HttpSearchClient(oo.url, oo.org, oo.user, oo.password)
        closers.append(client.close)
    if openobserve is None and settings.openobserve is not None and client is not None:
        openobserve = OpenObserveSource(
            client,
            store,
            recorder,
            base_url=settings.openobserve.url,
            definitions=client.alert_definition,
        )

    knowledge_paths = [*rules.investigation.tools.knowledge.paths]
    if rules.postmortems.enabled:
        knowledge_paths.append(rules.postmortems.dir)
    triager = (
        Triager(store, rules, model_client(rules.triage.model), knowledge_paths=knowledge_paths)
        if rules.triage.enabled
        else None
    )

    remediator: Remediator | None = None
    if rules.actions.enabled:
        if executor is None:
            remote = ExecutorClient(rules.actions.executor_url, settings.executor_token or "")
            closers.append(remote.close)
            executor = remote
        remediator = Remediator(store, rules, executor)

    investigator: Investigator | None = None
    if rules.investigation.enabled:
        if toolbox is None:
            wanted = rules.investigation.tools
            tools: list[Tool] = paeon_tools(store, knowledge_paths)
            if wanted.openobserve is not None and client is not None:
                tools += openobserve_tools(client, wanted.openobserve, client.alert_definition)
            if wanted.docker is not None:
                docker = httpx.Client(base_url=wanted.docker.url.rstrip("/"), timeout=20)
                closers.append(docker.close)
                tools += docker_tools(docker, wanted.docker)
            if wanted.uptime_kuma is not None:
                kuma = httpx.Client(base_url=wanted.uptime_kuma.url.rstrip("/"), timeout=20)
                closers.append(kuma.close)
                tools += uptime_kuma_tools(kuma, settings.uptime_kuma_key)
            if wanted.git is not None:
                tools += git_tools(wanted.git)
            if wanted.ssh is not None:
                tools += ssh_tools(wanted.ssh)
            toolbox = Toolbox(tools, max_result_chars=rules.investigation.max_result_chars)
        fallback = rules.investigation.fallback_model
        investigator = Investigator(
            store,
            rules,
            model_client(rules.investigation.model),
            toolbox,
            ntfy_token=settings.investigation_ntfy_token,
            fallback=None if fallback is None else model_client(fallback),
            extras=None if remediator is None else remediator.extras,
            after=None if remediator is None else remediator.on_findings,
        )
    writer = (
        PostmortemWriter(store, rules, model_client(rules.postmortems.model))
        if rules.postmortems.enabled
        else None
    )
    source = openobserve

    async def poll_openobserve() -> None:
        if source is None or settings.openobserve is None:  # pragma: no cover - not scheduled
            return
        try:
            result = await asyncio.to_thread(source.poll, _now())
            if result.changes:
                log.info("openobserve: %d rows, %d state changes", result.rows, result.changes)
        finally:
            # Also after a failed poll: an unreachable source is exactly what must be recorded.
            recorder.source_health(
                source.name,
                last_data_at=source.last_data_at(),
                now=_now(),
                stale_after=timedelta(seconds=settings.openobserve.stale_seconds),
            )

    async def heartbeat() -> None:
        if settings.heartbeat_url is None:  # pragma: no cover - not scheduled
            return
        await asyncio.to_thread(store.check)
        # The push URL carries the monitor's token: report the kind of failure, never the URL.
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                code = (await http.get(settings.heartbeat_url)).status_code
        except httpx.HTTPError as exc:
            raise RuntimeError(f"heartbeat request failed: {type(exc).__name__}") from None
        if code >= 400:
            raise RuntimeError(f"heartbeat answered HTTP {code}")

    async def run_triage() -> None:
        if triager is None:  # pragma: no cover - not scheduled
            return
        await asyncio.to_thread(triager.run_pending, _now())

    async def run_investigation() -> None:
        if investigator is None:  # pragma: no cover - not scheduled
            return
        await asyncio.to_thread(investigator.run_pending, _now())

    async def run_postmortems() -> None:
        if writer is None:  # pragma: no cover - not scheduled
            return
        await asyncio.to_thread(writer.run_pending, _now())

    exporter: Exporter | None = None
    if rules.telemetry is not None:
        exporter = Exporter(store, rules.telemetry, settings.telemetry_authorization)
        closers.append(exporter.close)

    async def run_telemetry() -> None:
        if exporter is None:  # pragma: no cover - not scheduled
            return
        await asyncio.to_thread(exporter.export)

    async def run_remediation() -> None:
        if remediator is None:  # pragma: no cover - not scheduled
            return
        await asyncio.to_thread(remediator.run_pending, _now())

    def send_digest(now: datetime) -> bool:
        ntfy = rules.digest.ntfy
        if ntfy is None:  # pragma: no cover - not scheduled
            return False
        local = now.astimezone(ZoneInfo(rules.digest.timezone))
        today = local.date().isoformat()
        if local.hour < rules.digest.hour or store.cursor("digest") == today:
            return False
        report = digests.build(store, since=now - timedelta(hours=24), until=now)
        if report.lines:  # a quiet day sends nothing
            base = rules.ui.public_url
            text = digests.render(report, console=base is not None)
            if base is None:
                digests.publish_ntfy(ntfy, settings.ntfy_token, title="Paeon digest", message=text)
            else:  # the notification opens the console: that is where feedback is given
                digests.publish_ntfy(
                    ntfy,
                    settings.ntfy_token,
                    title="Paeon digest",
                    message=text,
                    click=f"{base.rstrip('/')}/ui",
                )
            asked = 0
            if remediator is not None:
                # One short, quiet message per verdict: a notification holds three buttons.
                for line in digests.to_rate(report, rules.digest.feedback):
                    extras = remediator.buttons(line.incident.id, "triage", "Right", "Wrong")
                    if not extras.actions:
                        break  # the executor is not there to sign them
                    title, text = digests.question(line)
                    try:
                        digests.publish_ntfy(
                            ntfy,
                            settings.ntfy_token,
                            title=title,
                            message=text,
                            priority=1,
                            tags=("question",),
                            click=extras.click,
                            actions=extras.actions,
                        )
                    except RuntimeError:
                        break
                    asked += 1
            store.append(
                "digest.sent", {"date": today, "incidents": len(report.lines), "asked": asked}
            )
        store.set_cursor("digest", today)
        return bool(report.lines)

    async def run_digest() -> None:
        await asyncio.to_thread(send_digest, _now())

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        tasks: list[asyncio.Task[None]] = []
        if source is not None and settings.openobserve is not None:
            poll_seconds = settings.openobserve.poll_seconds
            tasks.append(
                asyncio.create_task(_every(poll_seconds, "openobserve poll", poll_openobserve))
            )
        if settings.heartbeat_url is not None:
            tasks.append(
                asyncio.create_task(_every(settings.heartbeat_seconds, "heartbeat", heartbeat))
            )
        if triager is not None:
            tasks.append(asyncio.create_task(_every(TRIAGE_TICK_SECONDS, "triage", run_triage)))
        if investigator is not None:
            tasks.append(
                asyncio.create_task(
                    _every(INVESTIGATION_TICK_SECONDS, "investigation", run_investigation)
                )
            )
        if writer is not None:
            tasks.append(
                asyncio.create_task(_every(POSTMORTEM_TICK_SECONDS, "postmortems", run_postmortems))
            )
        if rules.digest.ntfy is not None:
            tasks.append(asyncio.create_task(_every(DIGEST_TICK_SECONDS, "digest", run_digest)))
        if remediator is not None:
            tasks.append(
                asyncio.create_task(
                    _every(REMEDIATION_TICK_SECONDS, "remediation", run_remediation)
                )
            )
        if exporter is not None and rules.telemetry is not None:
            seconds = rules.telemetry.interval_seconds
            tasks.append(asyncio.create_task(_every(seconds, "telemetry", run_telemetry)))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for close in closers:
                close()

    app = FastAPI(title="Paeon", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def authorized(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {settings.ingest_token}".encode()
        given = (authorization or "").encode()
        if not hmac.compare_digest(given, expected):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"}
            )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        try:
            store.check()
        except Exception as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE) from exc
        return {"status": "ok"}

    @app.post("/ingest/uptime-kuma", dependencies=[Depends(authorized)])
    def ingest_uptime_kuma(payload: Annotated[dict[str, Any], Body()]) -> dict[str, int]:
        # uptime-kuma repeats itself and has states that are not verdicts: keep every call.
        store.append("webhook.received", payload, source="uptime-kuma")
        alert = parse_uptime_kuma(payload, now=_now())
        changed = alert is not None and recorder.observe(alert) is not None
        return {"alerts": int(alert is not None), "changes": int(changed)}

    @app.post("/ingest/alertmanager", dependencies=[Depends(authorized)])
    def ingest_alertmanager(payload: Annotated[dict[str, Any], Body()]) -> dict[str, int]:
        store.append("webhook.received", payload, source="alertmanager")
        alerts = parse_alertmanager(payload, now=_now())
        changes = sum(recorder.observe(alert) is not None for alert in alerts)
        return {"alerts": len(alerts), "changes": changes}

    @app.post("/ingest/change", dependencies=[Depends(authorized)])
    def ingest_change(change: Change) -> dict[str, int]:
        """The change calendar: a deploy, an update run, a backup, announced by whatever did it."""
        payload = change.model_dump(mode="json")
        payload["started_at"] = (change.started_at or _now()).isoformat()
        return {"event": store.append("change.recorded", payload, source="change")}

    @app.get("/incidents", dependencies=[Depends(authorized)])
    def list_incidents(
        state: Annotated[IncidentStatus | None, Query(alias="status")] = None,
    ) -> list[dict[str, Any]]:
        return [incident.model_dump(mode="json") for incident in store.incidents(status=state)]

    @app.get("/incidents/{incident_id}", dependencies=[Depends(authorized)])
    def get_incident(incident_id: int) -> dict[str, Any]:
        incident = store.incident(incident_id)
        if incident is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        events = [
            {
                "id": event.id,
                "recorded_at": event.recorded_at.isoformat(),
                "kind": event.kind,
                "payload": event.payload,
            }
            for event in store.events(incident_id=incident_id)
        ]
        return {"incident": incident.model_dump(mode="json"), "events": events}

    @app.post("/incidents/{incident_id}/feedback", dependencies=[Depends(authorized)])
    def give_feedback(incident_id: int, feedback: Feedback) -> dict[str, int]:
        if store.incident(incident_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        event = store.append(
            "feedback.recorded",
            {"about": "triage", "vote": feedback.vote, "note": feedback.note},
            incident_id=incident_id,
        )
        return {"event": event}

    @app.get("/digest", dependencies=[Depends(authorized)])
    def get_digest(hours: Annotated[int, Query(ge=1, le=24 * 31)] = 24) -> dict[str, Any]:
        now = _now()
        report = digests.build(store, since=now - timedelta(hours=hours), until=now)
        return {**report.as_dict(), "text": digests.render(report)}

    @app.get("/postmortems", dependencies=[Depends(authorized)])
    def list_postmortems() -> list[dict[str, Any]]:
        return [draft.as_dict() for draft in postmortems.drafts(rules.postmortems.dir)]

    @app.get("/autonomy", dependencies=[Depends(authorized)])
    def get_autonomy() -> dict[str, Any]:
        events = list(store.events(kinds=autonomy.KINDS))
        switch = store.events(kinds=("killswitch.engaged", "killswitch.released"))
        return {
            "kill_switch": autonomy.killed(switch),
            "tuples": [
                {
                    "alert": r.alert,
                    "action": r.action,
                    "target": r.target,
                    "policy": r.policy,
                    "effective": r.effective,
                    "demoted": r.demoted,
                    "runs": r.runs,
                    "verified": r.verified,
                    "failed": r.failed,
                    "undone": r.undone,
                    "wrong": r.wrong,
                    "streak": r.streak,
                    "proposal": r.proposal,
                }
                for r in autonomy.matrix(events, rules.actions, _now())
            ],
        }

    @app.get("/brief", dependencies=[Depends(authorized)])
    def brief() -> dict[str, Any]:
        """What a daily brief needs (the Horae source plugin reads this): what is open, what
        waits for an answer, what waits for a review."""
        files = postmortems.drafts(rules.postmortems.dir) if rules.postmortems.enabled else []
        return {
            "open_incidents": [
                {
                    "id": incident.id,
                    "title": incident.title,
                    "severity": incident.severity.value,
                    "host": incident.host,
                    "opened_at": incident.opened_at.isoformat(),
                }
                for incident in store.incidents(status=IncidentStatus.OPEN)
            ],
            "pending_approvals": pending_approvals(store, _now()),
            "postmortems_to_review": [d.as_dict() for d in files if d.status != "reviewed"],
            "open_action_items": [
                {"postmortem": d.name, "item": item} for d in files for item in d.open_items
            ],
        }

    if rules.ui.enabled:
        mount_ui(app, store, rules, now=_now, stop=None if remediator is None else remediator.stop)

    app.state.exporter = exporter
    app.state.remediator = remediator
    app.state.triager = triager
    app.state.investigator = investigator
    app.state.postmortems = writer
    app.state.send_digest = send_digest
    return app
