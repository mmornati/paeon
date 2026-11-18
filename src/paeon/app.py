"""The service: webhook endpoints, the OpenObserve poll loop, correlation, shadow triage, digest.

Phase 1 observes: it records, groups alerts into incidents and, when triage is enabled, asks a
model for a verdict that is recorded and never acted on. The only thing it can send is the
daily digest, and only if an ntfy topic is configured.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import httpx2 as httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from paeon import digest as digests
from paeon.config import Settings
from paeon.correlation import Correlator
from paeon.models import ModelClient, build_client
from paeon.recorder import Recorder
from paeon.schema import Change, IncidentStatus
from paeon.sources.openobserve import HttpSearchClient, OpenObserveSource
from paeon.sources.webhook import parse_alertmanager, parse_uptime_kuma
from paeon.store import Store
from paeon.triage import Triager

log = logging.getLogger(__name__)

TRIAGE_TICK_SECONDS = 15
DIGEST_TICK_SECONDS = 300


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
    triage_client: ModelClient | None = None,
) -> FastAPI:
    rules = settings.rules
    recorder = Recorder(store, Correlator(store, rules))
    own_model_client = None
    if rules.triage.enabled and triage_client is None:
        alias = rules.models[rules.triage.model]
        own_model_client = build_client(alias, settings.model_keys.get(rules.triage.model, ""))
        triage_client = own_model_client
    triager = (
        Triager(store, rules, triage_client)
        if rules.triage.enabled and triage_client is not None
        else None
    )
    client: HttpSearchClient | None = None
    if openobserve is None and settings.openobserve is not None:
        oo = settings.openobserve
        client = HttpSearchClient(oo.url, oo.org, oo.user, oo.password)
        openobserve = OpenObserveSource(client, store, recorder, base_url=oo.url)
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
            digests.publish_ntfy(
                ntfy, settings.ntfy_token, title="Paeon digest", message=digests.render(report)
            )
            store.append("digest.sent", {"date": today, "incidents": len(report.lines)})
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
        if rules.digest.ntfy is not None:
            tasks.append(asyncio.create_task(_every(DIGEST_TICK_SECONDS, "digest", run_digest)))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if client is not None:
                client.close()
            if own_model_client is not None:
                own_model_client.close()

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

    app.state.triager = triager
    app.state.send_digest = send_digest
    return app
