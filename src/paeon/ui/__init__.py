"""The console under `/ui`: the event log rendered for a person.

Read-only, apart from two things: saying whether triage was right, and stopping automation
(the kill switch, which the console can engage and cannot release). It shows plans, approvals
and what the executor did; it cannot approve anything: a Go is a signed link on your phone.

It has no login. It is mounted only when `ui.enabled` is set, and it expects a reverse proxy
that authenticates in front of it (Traefik with forward-auth, for example); `ui.require_header`
refuses requests the proxy did not mark. Pages make no request to any other host and are served
with a policy that forbids it, so that nothing a log line contains can load or run.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from paeon import __version__
from paeon import postmortem as postmortems
from paeon.redact import redact_text
from paeon.rules import Rules
from paeon.store import Store
from paeon.ui import markdown, views

_HERE = Path(__file__).parent
PAGE = 100
HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self';"
        " connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

# A postmortem with a diagram: the library that draws it styles its drawing inline, which the
# policy above refuses. Styles only, and on that page only. Scripts stay same-origin: nothing
# the file contains becomes markup (see `markdown`), so there is no inline style to inject.
DIAGRAM_HEADERS = HEADERS | {
    "Content-Security-Policy": HEADERS["Content-Security-Policy"].replace(
        "style-src 'self'", "style-src 'self' 'unsafe-inline'"
    )
}


def _same_origin(request: Request) -> None:
    """Refuse a form posted from another site: the proxy's session cookie would ride along."""
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        if site != "same-origin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-site request")
        return
    origin = urlsplit(request.headers.get("origin", "")).netloc
    if not origin or origin != request.headers.get("host"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-site request")


def mount_ui(
    app: FastAPI,
    store: Store,
    rules: Rules,
    *,
    now: Callable[[], datetime] | None = None,
    stop: Callable[[], None] | None = None,
    proxy_secret: str | None = None,
) -> None:
    acting = rules.actions.enabled
    keeps = rules.postmortems.enabled
    clock = now or (lambda: datetime.now(UTC))
    zone = ZoneInfo(rules.digest.timezone)
    templates = Jinja2Templates(directory=_HERE / "templates")
    env = templates.env

    def shown(value: object) -> object:
        """The last net: every string a template prints goes through the secret patterns.

        The views redact what they build from payloads; this covers what they pass through as
        it is (an alert title, a host, a reason code, a model name). Markup is a macro's own
        output, made of values that already came through here.
        """
        if isinstance(value, str) and not isinstance(value, Markup):
            return redact_text(value, max_length=views.DETAIL_LIMIT * 3)
        return value

    env.finalize = shown
    env.filters["clock"] = lambda moment: moment.astimezone(zone).strftime("%H:%M:%S")
    env.filters["day"] = lambda moment: moment.astimezone(zone).strftime("%a %d %b")
    env.filters["stamp"] = lambda moment: moment.astimezone(zone).strftime("%d %b %H:%M")

    def brief(moment: datetime) -> str:
        """The time alone when it is today: a board strip has one line for it."""
        local = moment.astimezone(zone)
        today = local.date() == clock().astimezone(zone).date()
        return local.strftime("%H:%M" if today else "%d %b %H:%M")

    env.filters["brief"] = brief
    env.filters["money"] = views.money
    env.filters["percent"] = lambda value: (
        f"{float(value):.0%}" if isinstance(value, int | float) else "?"
    )
    env.globals |= {
        "version": __version__,
        "zone": rules.digest.timezone,
        "stages": views.stages(rules),
        "filters": views.FILTERS,
        "acting": acting,
        "keeps_postmortems": rules.postmortems.enabled,
    }

    def proxied(request: Request) -> None:
        header = rules.ui.require_header
        if header is not None and not request.headers.get(header):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not behind the proxy")
        secret = rules.ui.proxy_secret
        if secret is not None:
            # Presence proves nothing to a neighbour on the same network; this value does.
            given = request.headers.get(secret.header, "").encode()
            if not proxy_secret or not hmac.compare_digest(given, proxy_secret.encode()):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "not behind the proxy")

    router = APIRouter(prefix="/ui", dependencies=[Depends(proxied)], include_in_schema=False)

    def page(request: Request, name: str, **context: object) -> Response:
        return templates.TemplateResponse(request, name, context, headers=HEADERS)

    @router.get("", response_class=HTMLResponse)
    def board(request: Request) -> Response:
        bays = views.board(store, clock(), acting=acting)
        return page(request, "board.html", bays=bays, here="board")

    @router.get("/board", response_class=HTMLResponse)
    def board_fragment(request: Request) -> Response:
        """The bays alone, for the live refresh."""
        return page(request, "_bays.html", bays=views.board(store, clock(), acting=acting))

    @router.get("/incidents/{incident_id}", response_class=HTMLResponse)
    def incident(request: Request, incident_id: int) -> Response:
        found = store.incident(incident_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        view = views.incident_view(store, found, clock(), acting=acting)
        return page(request, "incident.html", view=view, here="board")

    @router.post("/incidents/{incident_id}/feedback", dependencies=[Depends(_same_origin)])
    def feedback(
        incident_id: int,
        vote: Annotated[Literal["up", "down"], Form()],
        note: Annotated[str, Form(max_length=1000)] = "",
    ) -> Response:
        if store.incident(incident_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        store.append(
            "feedback.recorded",
            {"about": "triage", "vote": vote, "note": note.strip()},
            incident_id=incident_id,
        )
        return RedirectResponse(
            f"/ui/incidents/{incident_id}#triage", status.HTTP_303_SEE_OTHER, headers=HEADERS
        )

    @router.get("/log", response_class=HTMLResponse)
    def log(
        request: Request,
        show: Annotated[str | None, Query()] = None,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> Response:
        chosen = show if show in views.FILTERS else None
        kinds = None if chosen is None else views.FILTERS[chosen][1]
        events = list(
            store.events(kinds=kinds, before_id=before, newest_first=True, limit=PAGE + 1)
        )
        rows = [views.row_of(event) for event in events[:PAGE]]
        older = rows[-1].id if len(events) > PAGE else None
        return page(
            request, "log.html", rows=rows, show=chosen, older=older, before=before, here="log"
        )

    @router.get("/events/{event_id}", response_class=HTMLResponse)
    def event(request: Request, event_id: int) -> Response:
        found = next(iter(store.events(after_id=event_id - 1, limit=1)), None)
        if found is None or found.id != event_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        row = views.row_of(found, detail=True)
        return page(request, "event.html", row=row, source=found.source, here="log")

    @router.get("/ledger", response_class=HTMLResponse)
    def ledger(request: Request) -> Response:
        view = views.ledger_view(store, rules, clock())
        return page(request, "ledger.html", ledger=view, here="ledger")

    @router.get("/actions", response_class=HTMLResponse)
    def actions(request: Request, stopped: Annotated[str | None, Query()] = None) -> Response:
        view = views.actions_view(store, rules, clock())
        return page(
            request,
            "actions.html",
            view=view,
            catalog=rules.actions.catalog,
            policies=rules.actions.policies,
            default_level=rules.actions.default_level,
            runbooks=rules.actions.runbooks,
            can_stop=stop is not None,
            stopped=stopped,
            here="actions",
        )

    @router.post("/actions/stop", dependencies=[Depends(_same_origin)])
    def stop_automation() -> Response:
        """Engage the kill switch. There is no way back from here: that takes the owner."""
        if stop is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        try:
            stop()
        except Exception:
            outcome = "failed"
        else:
            outcome = "yes"
        return RedirectResponse(
            f"/ui/actions?stopped={outcome}", status.HTTP_303_SEE_OTHER, headers=HEADERS
        )

    @router.get("/autonomy", response_class=HTMLResponse)
    def autonomy_page(request: Request) -> Response:
        view = views.autonomy_view(store, rules, clock())
        return page(request, "autonomy.html", view=view, here="autonomy")

    @router.get("/postmortems", response_class=HTMLResponse)
    def postmortem_list(request: Request) -> Response:
        drafts = views.postmortems_view(rules)
        return page(request, "postmortems.html", drafts=drafts, here="postmortems")

    @router.get("/postmortems/{name}", response_class=HTMLResponse)
    def postmortem(request: Request, name: str) -> Response:
        found = postmortems.read_draft(rules.postmortems.dir, name) if keeps else None
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        # Masked before it is rendered: the rendered page does not pass through `shown`.
        text = redact_text(found[1], max_length=views.DETAIL_LIMIT * 30)
        body = markdown.render(text)
        diagrams = markdown.DIAGRAM in body
        return templates.TemplateResponse(
            request,
            "postmortem.html",
            {
                "draft": found[0],
                "text": text,
                "body": body,
                "diagrams": diagrams,
                "here": "postmortems",
            },
            headers=DIAGRAM_HEADERS if diagrams else HEADERS,
        )

    @app.get("/", include_in_schema=False, dependencies=[Depends(proxied)])
    def home() -> Response:
        return RedirectResponse("/ui", status.HTTP_307_TEMPORARY_REDIRECT)

    app.include_router(router)
    app.mount("/ui/static", StaticFiles(directory=_HERE / "static"), name="ui-static")
