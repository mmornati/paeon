"""The executor as a service: what the core may ask, and the one path a phone reaches.

Two kinds of caller. The core, with a bearer token, proposes plans, cancels them, reads the
journal and may engage the kill switch (never release it). A phone, with no token at all, uses
`/act/<link>`: the link itself is the proof. A GET of a link only shows what it would do; a POST
applies it. `/act` is the only path of Paeon meant to be reachable without single sign-on.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import html
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import httpx2 as httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from paeon import notify
from paeon.actuators import CoolifyActuator, RepositoryActuator
from paeon.catalog import Proposal
from paeon.config import ExecutorSettings
from paeon.executor import DockerActuator, Executor, ExecutorStore
from paeon.guard import BodyGuard
from paeon.links import LinkError, Signer

log = logging.getLogger(__name__)

EXPIRY_TICK_SECONDS = 30
MAX_BODY_BYTES = 256_000  # a proposal is a few steps and two sentences
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self';"
    " base-uri 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><meta name="color-scheme" content="light dark">
<title>Paeon</title>
<style>body{{font:1.05rem/1.5 system-ui,sans-serif;max-width:32rem;margin:15vh auto;
padding:0 1.2rem}}
button{{font:inherit;padding:.7rem 1.4rem;border-radius:.4rem;border:1px solid;cursor:pointer}}
p.small{{font-size:.9rem;opacity:.75}}</style></head>
<body><h1>Paeon</h1><p>{text}</p>{form}</body></html>
"""
_FORM = (
    '<form method="post"><button>Confirm</button></form>'
    '<p class="small">Opening this page did nothing. It happens when you confirm.</p>'
)


class Cancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="cancelled by the core", max_length=200)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: Literal["go", "nogo"]
    by: str = Field(default="", max_length=80)


class VoteLinks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: int = Field(ge=1)
    about: Literal["triage", "root_cause"]


class _QuietAccess(logging.Filter):
    """Keeps the access log fit to keep: no link, and no line per poll.

    The path of a button is the link itself. It works once and expires, but a log is copied,
    shipped and kept: the link is cut out of it. The core's journal poll and the health check
    answer every few seconds and say nothing when they succeed.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5 or not isinstance(args[2], str):
            return True
        path = args[2]
        if args[4] == 200 and path.split("?")[0] in ("/journal", "/healthz"):
            return False
        if path.startswith("/act/"):
            record.args = (*args[:2], "/act/[link]", *args[3:])
        return True


def quiet_access_log() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(known, _QuietAccess) for known in access.filters):
        access.addFilter(_QuietAccess())


def _now() -> datetime:
    return datetime.now(UTC)


def _page(text: str, *, form: bool = False, code: int = 200) -> HTMLResponse:
    body = _PAGE.format(text=html.escape(text), form=_FORM if form else "")
    return HTMLResponse(body, status_code=code, headers=HEADERS)


def build_executor(settings: ExecutorSettings, store: ExecutorStore) -> Executor:
    actions = settings.rules.actions
    docker = None
    if actions.docker_url:
        read = actions.docker_read_url
        docker = DockerActuator(
            httpx.Client(base_url=actions.docker_url.rstrip("/"), timeout=30),
            None if read is None else httpx.Client(base_url=read.rstrip("/"), timeout=30),
        )
    coolify = None
    if actions.coolify is not None and settings.coolify_token:
        coolify = CoolifyActuator(
            httpx.Client(
                base_url=actions.coolify.url.rstrip("/"),
                headers={"Authorization": f"Bearer {settings.coolify_token}"},
                timeout=30,
            ),
            actions.coolify,
        )
    repository = None
    if actions.repository is not None:
        github = actions.repository.github
        repository = RepositoryActuator(
            actions.repository,
            token=settings.github_token,
            http=None if github is None else httpx.Client(base_url=github.api_url, timeout=30),
        )
    return Executor(
        store,
        settings.rules,
        signer=Signer(settings.signing_key) if settings.signing_key else None,
        docker=docker,
        coolify=coolify,
        repository=repository,
        notifiers=notify.for_stage(
            settings.rules,
            "actions",
            secrets=settings.notifier_secrets,
            legacy_token=settings.ntfy_token,
        ),
        kill_env=settings.kill_switch,
    )


def create_executor_app(settings: ExecutorSettings, executor: Executor) -> FastAPI:
    async def expire() -> None:
        while True:
            try:
                await asyncio.to_thread(executor.expire, _now())
            except Exception:
                log.exception("expiry failed")
            await asyncio.sleep(EXPIRY_TICK_SECONDS)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        interrupted = await asyncio.to_thread(executor.recover)
        if interrupted:
            log.warning("%d plans were interrupted by a restart and are closed", interrupted)
        task = asyncio.create_task(expire())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    quiet_access_log()
    app = FastAPI(
        title="Paeon executor", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )

    # Before any body is read: the core's token, and a size. A link is its own proof.
    app.add_middleware(
        BodyGuard, token=settings.token, max_bytes=MAX_BODY_BYTES, open_prefixes=("/act",)
    )

    def core(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {settings.token}".encode()
        if not hmac.compare_digest((authorization or "").encode(), expected):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"}
            )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/proposals", dependencies=[Depends(core)])
    def propose(proposal: Proposal) -> dict[str, Any]:
        return executor.propose(proposal, _now())

    @app.post("/proposals/{proposal_id}/cancel", dependencies=[Depends(core)])
    def cancel(proposal_id: int, body: Cancel) -> dict[str, bool]:
        return {"cancelled": executor.cancel(proposal_id, body.reason)}

    @app.get("/journal", dependencies=[Depends(core)])
    def journal(after: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
        return {"events": executor.journal(after), "head": executor.head()}

    @app.get("/state", dependencies=[Depends(core)])
    def state() -> dict[str, Any]:
        return executor.state(_now())

    @app.post("/kill-switch", dependencies=[Depends(core)])
    def kill() -> dict[str, bool]:
        """Engage only. Releasing takes `paeon resume`, next to the executor."""
        executor.engage_kill("core")
        return {"kill_switch": True}

    @app.post("/proposals/{proposal_id}/answer", dependencies=[Depends(core)])
    def answer(proposal_id: int, body: Answer) -> dict[str, Any]:
        """Go or No-go from the console. Refused unless this executor's rules allow it."""
        try:
            outcome = executor.answer(proposal_id, body.answer, by=body.by, now=_now())
        except LinkError as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": outcome.ok, "message": outcome.message}

    @app.post("/links/vote", dependencies=[Depends(core)])
    def vote_links(body: VoteLinks) -> dict[str, str]:
        return executor.vote_links(body.incident_id, body.about, _now())

    @app.get("/act/{token}", response_class=HTMLResponse)
    def show(token: str) -> HTMLResponse:
        try:
            return _page(executor.describe(token, _now()), form=True)
        except LinkError as exc:
            return _page(str(exc).capitalize() + ".", code=status.HTTP_410_GONE)

    @app.post("/act/{token}", response_class=HTMLResponse)
    def apply(token: str) -> HTMLResponse:
        outcome = executor.act(token, _now())
        if outcome.ok:
            return _page(outcome.message)
        return _page(outcome.message.capitalize() + ".", code=status.HTTP_410_GONE)

    app.state.executor = executor
    return app
