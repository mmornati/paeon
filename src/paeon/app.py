"""The phase 0 service: webhook endpoints, the OpenObserve poll loop and the heartbeat.

It records and does nothing else: no model call, no notification, no action.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx2 as httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, status

from paeon.config import Settings
from paeon.recorder import Recorder
from paeon.sources.openobserve import HttpSearchClient, OpenObserveSource
from paeon.sources.webhook import parse_alertmanager, parse_uptime_kuma
from paeon.store import Store

log = logging.getLogger(__name__)


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
    settings: Settings, store: Store, *, openobserve: OpenObserveSource | None = None
) -> FastAPI:
    recorder = Recorder(store)
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
        async with httpx.AsyncClient(timeout=10) as http:
            (await http.get(settings.heartbeat_url)).raise_for_status()

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
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if client is not None:
                client.close()

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

    return app
