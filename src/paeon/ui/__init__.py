"""The console under `/ui`: the event log rendered for a person.

It reads, and records what a person decides. An operator says whether triage was right,
re-checks or closes an incident, reviews and edits postmortems, and stops automation (the kill
switch, which the console can engage and cannot release). An admin, when `ui.settings` is on,
adds sources and channels. A plan is answered here only when the executor's own rules allow it
(`actions.console_approval`), and only one it already waits on; no channel added here carries
an approval.

It is mounted only when `ui.enabled` is set. By default it has no login and expects a reverse
proxy that authenticates in front of it (Traefik with forward-auth, for example);
`ui.require_header` refuses requests the proxy did not mark, and `ui.roles` reads who may do
what from the groups the proxy names. With `ui.auth: local` it asks for one password itself
(see `access`). Pages make no request to any other host and are served with a policy that
forbids it, so that nothing a log line contains can load or run.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from paeon import __version__
from paeon import postmortem as postmortems
from paeon.channels import SERVICES, STAGES, ChannelError, Channels, Service, read_form
from paeon.console_sources import SOURCE_SERVICES, Sources
from paeon.operations import Incidents, OperationError
from paeon.redact import redact_text
from paeon.rules import Rules
from paeon.store import Store
from paeon.ui import markdown, views
from paeon.ui.access import COOKIE, SESSION_SECONDS, Role, Sessions, allows, role_from_groups

_HERE = Path(__file__).parent
_log = logging.getLogger(__name__)
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


async def _fields(request: Request) -> dict[str, str]:
    """A posted form as text fields. A file, or a field given twice, is not something a
    console form sends."""
    form = await request.form()
    return {key: value for key, value in form.items() if isinstance(value, str)}


def _client(request: Request) -> str:
    """The address the connection comes from. Not a forwarded header: anyone can write one."""
    return request.client.host if request.client is not None else ""


def _next(value: str | None) -> str:
    """Where to go after signing in: a page of the console, or its first one."""
    if value and re.fullmatch(r"/ui(/[A-Za-z0-9/_.?=&%-]*)?", value) and "//" not in value:
        return value
    return "/ui"


def mount_ui(
    app: FastAPI,
    store: Store,
    rules: Rules,
    *,
    now: Callable[[], datetime] | None = None,
    stop: Callable[[], None] | None = None,
    proxy_secret: str | None = None,
    sources: Sequence[views.SourceShown] | Callable[[], Sequence[views.SourceShown]] = (),
    channels: Channels | None = None,
    source_book: Sources | None = None,
    sessions: Sessions | None = None,
    writer: postmortems.PostmortemWriter | None = None,
    incidents: Incidents | None = None,
    answer: Callable[[int, str, str], tuple[bool, str]] | None = None,
) -> None:
    local = rules.ui.auth == "local"
    if local and sessions is None:
        # Fail closed: with no proxy in front, a console without its login would be open.
        raise RuntimeError("ui.auth: local needs PAEON_SECRET_KEY and PAEON_UI_ADMIN_PASSWORD")
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
        "notifies": channels is not None
        or any(rules.channels(stage) for stage in ("digest", "investigation", "actions")),
        "local": local,
        "settings": channels is not None or source_book is not None,
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

    def known(request: Request) -> None:
        """Who is asking. Leaves the role on the request, or sends them to sign in."""
        proxied(request)
        role: Role
        if sessions is not None:
            if not sessions.valid(request.cookies.get(COOKIE)):
                if request.method != "GET" or request.url.path == "/ui/board":
                    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "signed out")
                if not sessions.passwords.ready():
                    raise HTTPException(
                        status.HTTP_303_SEE_OTHER, headers={**HEADERS, "Location": "/ui/setup"}
                    )
                here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
                raise HTTPException(
                    status.HTTP_303_SEE_OTHER,
                    headers={**HEADERS, "Location": f"/ui/signin?next={quote(here, safe='')}"},
                )
            role = "admin"
        elif rules.ui.roles is not None:
            role = role_from_groups(rules.ui.roles, request.headers.get(rules.ui.roles.header))
        else:
            role = "operator"  # as before roles existed: the proxy decides who comes in
        request.state.role = role

    def need(least: Role) -> Callable[[Request], None]:
        def check(request: Request) -> None:
            if not allows(request.state.role, least):
                raise HTTPException(status.HTTP_403_FORBIDDEN, f"this needs the {least} role")

        return check

    def who(request: Request) -> str:
        """The name the log gives whoever changed something: the proxy's, or the role."""
        header = rules.ui.require_header
        named = request.headers.get(header, "").strip() if header else ""
        return named[:80] or str(request.state.role)

    router = APIRouter(prefix="/ui", dependencies=[Depends(known)], include_in_schema=False)
    door = APIRouter(prefix="/ui", dependencies=[Depends(proxied)], include_in_schema=False)

    def page(
        request: Request, template: str, /, status_code: int = 200, **context: object
    ) -> Response:
        role = getattr(request.state, "role", "viewer")
        context |= {
            "role": role,
            "can_operate": allows(role, "operator"),
            "can_admin": allows(role, "admin"),
            # What waits for a person, in the navigation: drafts nobody has reviewed yet.
            "to_review": sum(1 for d in views.postmortems_view(rules) if d.status == "draft")
            if keeps
            else 0,
        }
        return templates.TemplateResponse(
            request, template, context, status_code=status_code, headers=HEADERS
        )

    def back(where: str) -> Response:
        return RedirectResponse(where, status.HTTP_303_SEE_OTHER, headers=HEADERS)

    if sessions is not None:
        gate = sessions

        def signed_in(request: Request, cookie: str, where: str) -> Response:
            response = back(where)
            secure = "https" in (request.url.scheme, request.headers.get("x-forwarded-proto"))
            response.set_cookie(
                COOKIE,
                cookie,
                max_age=SESSION_SECONDS,
                path="/ui",
                httponly=True,
                samesite="strict",
                secure=secure,
            )
            return response

        # No password yet: it is chosen in the browser, by whoever can read Paeon's log. The
        # code is new at each start and stops working once a password exists.
        setup_code = "" if gate.passwords.ready() else secrets.token_urlsafe(9)
        if setup_code:
            _log.warning(
                "The console has no admin password yet. Open /ui/setup and enter this"
                " code to choose one: %s",
                setup_code,
            )

        def setup_page(request: Request, problem: str = "", status_code: int = 200) -> Response:
            wait = gate.locked(_client(request))
            return page(request, "setup.html", status_code, wait=wait, problem=problem)

        @door.get("/setup", response_class=HTMLResponse)
        def setup(request: Request) -> Response:
            if gate.passwords.ready():
                return back("/ui/signin")
            return setup_page(request)

        @door.post("/setup", dependencies=[Depends(_same_origin)])
        def setup_post(
            request: Request,
            code: Annotated[str, Form(max_length=200)] = "",
            password: Annotated[str, Form(max_length=1024)] = "",
            again: Annotated[str, Form(max_length=1024)] = "",
        ) -> Response:
            if gate.passwords.ready() or not setup_code:
                return back("/ui/signin")
            if len(password) < 12:
                return setup_page(request, "Choose a password of 12 characters or more.", 422)
            if password != again:
                return setup_page(request, "The two passwords are not the same.", 422)
            given = code.strip().encode()
            if not gate.attempt(
                _client(request), lambda: hmac.compare_digest(given, setup_code.encode())
            ):
                wait = gate.locked(_client(request))
                return setup_page(
                    request,
                    "" if wait else "That is not the code Paeon printed when it started.",
                    status.HTTP_429_TOO_MANY_REQUESTS if wait else status.HTTP_401_UNAUTHORIZED,
                )
            gate.passwords.choose(password)
            store.append("console.setup", {"role": "admin"})
            return signed_in(request, gate.issue(), "/ui")

        @door.get("/signin", response_class=HTMLResponse)
        def signin(request: Request, next: Annotated[str | None, Query()] = None) -> Response:
            if not gate.passwords.ready():
                return back("/ui/setup")
            if gate.valid(request.cookies.get(COOKIE)):
                return back(_next(next))
            wait = gate.locked(_client(request))
            return page(request, "signin.html", next=_next(next), wait=wait, wrong=False)

        @door.post("/signin", dependencies=[Depends(_same_origin)])
        def signin_post(
            request: Request,
            password: Annotated[str, Form(max_length=1024)] = "",
            next: Annotated[str, Form(max_length=2048)] = "/ui",
        ) -> Response:
            client = _client(request)
            was_locked = gate.locked(client)
            cookie = gate.sign_in(password, client)
            if cookie is None:
                wait = gate.locked(client)
                if wait and not was_locked:
                    store.append("console.signin_locked", {"seconds": wait})
                return page(
                    request,
                    "signin.html",
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS
                    if wait
                    else status.HTTP_401_UNAUTHORIZED,
                    next=_next(next),
                    wait=wait,
                    wrong=not was_locked,
                )
            store.append("console.signin", {"role": "admin"})
            return signed_in(request, cookie, _next(next))

        @door.post("/signout", dependencies=[Depends(_same_origin)])
        def signout() -> Response:
            response = back("/ui/signin")
            response.delete_cookie(COOKIE, path="/ui")
            return response

    @router.get("", response_class=HTMLResponse)
    def board(request: Request) -> Response:
        bays = views.board(store, clock(), acting=acting)
        steps = None
        if (channels is not None or source_book is not None) and allows(
            request.state.role, "admin"
        ):
            current = rules if channels is None else channels.rules
            steps = views.setup_steps(store, current, channels, source_book)
        return page(request, "board.html", bays=bays, here="board", steps=steps)

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
        askable = (
            {}
            if incidents is None
            else {a.source: incidents.can_check(a.source) for a in found.alerts}
        )
        return page(
            request,
            "incident.html",
            view=view,
            here="board",
            askable=askable,
            operable=incidents is not None,
            answerable=answer is not None and rules.actions.console_approval,
            done=request.query_params.get("done"),
        )

    def asked(request: Request, incident_id: int, what: str) -> Response:
        return back(f"/ui/incidents/{incident_id}?done={what}#status")

    if incidents is not None:
        book_of_incidents = incidents

        @router.post(
            "/incidents/{incident_id}/recheck",
            dependencies=[Depends(_same_origin), Depends(need("operator"))],
        )
        def incident_recheck(request: Request, incident_id: int) -> Response:
            try:
                book_of_incidents.recheck(incident_id, by=who(request))
            except OperationError as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
            return asked(request, incident_id, "rechecked")

        @router.post(
            "/incidents/{incident_id}/close",
            dependencies=[Depends(_same_origin), Depends(need("operator"))],
        )
        def incident_close(
            request: Request,
            incident_id: int,
            note: Annotated[str, Form(min_length=1, max_length=1000)],
        ) -> Response:
            try:
                book_of_incidents.close(incident_id, by=who(request), note=note)
            except OperationError as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
            return asked(request, incident_id, "closed")

    if answer is not None and rules.actions.console_approval:
        answering = answer

        @router.post(
            "/incidents/{incident_id}/answer",
            dependencies=[Depends(_same_origin), Depends(need("operator"))],
        )
        def incident_answer(
            request: Request,
            incident_id: int,
            proposal: Annotated[int, Form(ge=1)],
            reply: Annotated[Literal["go", "nogo"], Form()],
        ) -> Response:
            found = store.incident(incident_id)
            plan = views.plan_of(list(store.events(incident_id=incident_id)))
            if found is None or plan is None or plan.proposal != proposal or not plan.waiting:
                raise HTTPException(status.HTTP_409_CONFLICT, "this plan is not waiting for a Go")
            ok, message = answering(proposal, reply, who(request))
            what = ("go" if reply == "go" else "nogo") if ok else "refused"
            if not ok:
                store.append(
                    "approval.refused",
                    {"proposal": proposal, "by": who(request), "reason": message[:300]},
                    incident_id=incident_id,
                )
            return asked(request, incident_id, what)

    @router.post(
        "/incidents/{incident_id}/feedback",
        dependencies=[Depends(_same_origin), Depends(need("operator"))],
    )
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

    @router.get("/channels", response_class=HTMLResponse)
    def channels_page(
        request: Request,
        saved: Annotated[str | None, Query(max_length=40)] = None,
        removed: Annotated[str | None, Query(max_length=40)] = None,
        tested: Annotated[str | None, Query(max_length=40)] = None,
        ok: Annotated[str | None, Query(max_length=1)] = None,
    ) -> Response:
        current = rules if channels is None else channels.rules
        lines = views.channels_view(store, current, clock(), console=channels)
        return page(
            request,
            "channels.html",
            lines=lines,
            days=30,
            here="channels",
            saved=saved,
            removed=removed,
            tested=tested,
            delivered=ok == "1",
        )

    if channels is not None:
        book = channels
        admin = [Depends(need("admin"))]
        change = [Depends(_same_origin), Depends(need("admin"))]

        def form_page(
            request: Request,
            service: Service,
            *,
            name: str,
            values: dict[str, Any],
            stages: Sequence[str],
            kept: frozenset[str] = frozenset(),
            problems: dict[str, str] | None = None,
            editing: bool,
            broken: str | None = None,
        ) -> Response:
            # Only what is not a secret goes back to the page, whatever was posted.
            plain = {spec.key for spec in service.fields if not spec.secret}
            shown_values = {
                key: ", ".join(value) if isinstance(value, list | tuple) else str(value)
                for key, value in values.items()
                if key in plain
            }
            return page(
                request,
                "channel_form.html",
                status_code=422 if problems else 200,
                service=service,
                name=name,
                values=shown_values,
                carries=tuple(stages),
                stage_on={
                    "digest": True,
                    "investigation": rules.investigation.enabled,
                },
                kept=kept,
                problems=problems or {},
                editing=editing,
                broken=broken,
                here="channels",
            )

        def submitted(
            service: Service, fields: dict[str, str]
        ) -> tuple[dict[str, Any], dict[str, str], dict[str, str], list[str], list[str]]:
            config, secrets, problems = read_form(service, fields)
            stages = [stage for stage in STAGES if fields.get(f"carry_{stage}")]
            forget = [spec.key for spec in service.fields if fields.get(f"forget_{spec.key}")]
            return config, secrets, problems, stages, forget

        def after_save(request: Request, name: str, fields: dict[str, str]) -> Response:
            if fields.get("then") != "test":
                return back(f"/ui/channels?saved={quote(name)}")
            return send_test(name)

        def send_test(name: str) -> Response:
            base = rules.ui.public_url
            click = None if base is None else f"{base.rstrip('/')}/ui/channels"
            delivery = book.test(name, click=click)
            if delivery is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            return back(f"/ui/channels?tested={quote(name)}&ok={int(delivery.ok)}")

        @router.get("/channels/add", response_class=HTMLResponse, dependencies=admin)
        def channel_pick(request: Request) -> Response:
            return page(request, "channel_new.html", services=SERVICES.values(), here="channels")

        @router.get("/channels/{name}/edit", response_class=HTMLResponse, dependencies=admin)
        def channel_edit(request: Request, name: str) -> Response:
            found = book.find(name)
            if found is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            return form_page(
                request,
                found.service,
                name=name,
                values=found.config,
                stages=found.stages,
                kept=found.kept,
                editing=True,
                broken=found.broken,
            )

        @router.post("/channels/{name}/edit", dependencies=change)
        def channel_update(
            request: Request, name: str, fields: Annotated[dict[str, str], Depends(_fields)]
        ) -> Response:
            found = book.find(name)
            if found is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            config, secrets, problems, stages, forget = submitted(found.service, fields)
            if not problems:
                try:
                    book.save(
                        name,
                        found.service,
                        config,
                        secrets,
                        stages=stages,
                        forget=forget,
                        creating=False,
                        by=who(request),
                        now=clock(),
                    )
                except ChannelError as exc:
                    problems = exc.problems
            if problems:
                return form_page(
                    request,
                    found.service,
                    name=name,
                    values=fields,
                    stages=stages,
                    kept=found.kept,
                    problems=problems,
                    editing=True,
                )
            return after_save(request, name, fields)

        @router.post("/channels/{name}/test", dependencies=change)
        def channel_test(name: str) -> Response:
            return send_test(name)

        @router.post("/channels/{name}/remove", dependencies=change)
        def channel_remove(request: Request, name: str) -> Response:
            if not book.remove(name, by=who(request), now=clock()):
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            return back(f"/ui/channels?removed={quote(name)}")

        @router.get("/channels/add/{kind}", response_class=HTMLResponse, dependencies=admin)
        def channel_new(request: Request, kind: str) -> Response:
            service = SERVICES.get(kind)
            if service is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            defaults = {spec.key: spec.default for spec in service.fields if spec.default}
            return form_page(
                request, service, name="", values=defaults, stages=tuple(STAGES), editing=False
            )

        @router.post("/channels/add/{kind}", dependencies=change)
        def channel_create(
            request: Request, kind: str, fields: Annotated[dict[str, str], Depends(_fields)]
        ) -> Response:
            service = SERVICES.get(kind)
            if service is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            name = fields.get("name", "").strip()
            config, secrets, problems, stages, _ = submitted(service, fields)
            if not problems:
                try:
                    book.save(
                        name,
                        service,
                        config,
                        secrets,
                        stages=stages,
                        creating=True,
                        by=who(request),
                        now=clock(),
                    )
                except ChannelError as exc:
                    problems = exc.problems
            if problems:
                return form_page(
                    request,
                    service,
                    name=name,
                    values=fields,
                    stages=stages,
                    problems=problems,
                    editing=False,
                )
            return after_save(request, name, fields)

    @router.get("/sources", response_class=HTMLResponse)
    def sources_page(
        request: Request,
        saved: Annotated[str | None, Query(max_length=40)] = None,
        removed: Annotated[str | None, Query(max_length=40)] = None,
        tested: Annotated[str | None, Query(max_length=40)] = None,
        ok: Annotated[str | None, Query(max_length=1)] = None,
    ) -> Response:
        current = sources() if callable(sources) else sources
        lines = views.sources_view(store, current, clock(), console=source_book)
        return page(
            request,
            "sources.html",
            lines=lines,
            days=30,
            here="sources",
            saved=saved,
            removed=removed,
            tested=tested,
            answered=ok == "1",
        )

    if source_book is not None:
        connections = source_book
        source_admin = [Depends(need("admin"))]
        source_change = [Depends(_same_origin), Depends(need("admin"))]

        def source_form(
            request: Request,
            service: Service,
            *,
            name: str,
            values: dict[str, Any],
            kept: frozenset[str] = frozenset(),
            problems: dict[str, str] | None = None,
            editing: bool,
            broken: str | None = None,
        ) -> Response:
            plain = {spec.key for spec in service.fields if not spec.secret}
            shown_values: dict[str, Any] = {}
            for key, value in values.items():
                if key not in plain:
                    continue  # only what is not a secret goes back to the page
                if isinstance(value, dict):
                    shown_values[key] = "\n".join(f"{k}: {v}" for k, v in value.items())
                elif isinstance(value, list | tuple):
                    shown_values[key] = ", ".join(map(str, value))
                elif isinstance(value, bool):
                    shown_values[key] = value
                else:
                    shown_values[key] = str(value)
            return page(
                request,
                "source_form.html",
                status_code=422 if problems else 200,
                service=service,
                name=name,
                values=shown_values,
                kept=kept,
                problems=problems or {},
                editing=editing,
                broken=broken,
                here="sources",
            )

        def source_saved(name: str, fields: dict[str, str]) -> Response:
            if fields.get("then") == "test" and connections.can_test(name):
                return source_tested(name)
            return back(f"/ui/sources?saved={quote(name)}")

        def source_tested(name: str) -> Response:
            answer = connections.test(name)
            if answer is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            return back(f"/ui/sources?tested={quote(name)}&ok={int(answer[0])}")

        @router.get("/sources/add", response_class=HTMLResponse, dependencies=source_admin)
        def source_pick(request: Request) -> Response:
            return page(
                request, "source_new.html", services=SOURCE_SERVICES.values(), here="sources"
            )

        @router.get("/sources/{name}/edit", response_class=HTMLResponse, dependencies=source_admin)
        def source_edit(request: Request, name: str) -> Response:
            found = connections.find(name)
            if found is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            defaults = {
                spec.key: spec.default == "on" if spec.kind == "bool" else spec.default
                for spec in found.service.fields
                if spec.default and not spec.secret
            }
            return source_form(
                request,
                found.service,
                name=name,
                values={**defaults, **found.config},
                kept=found.kept,
                editing=True,
                broken=found.broken,
            )

        @router.post("/sources/{name}/edit", dependencies=source_change)
        def source_update(
            request: Request, name: str, fields: Annotated[dict[str, str], Depends(_fields)]
        ) -> Response:
            found = connections.find(name)
            if found is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            config, secrets, problems = read_form(found.service, fields)
            forget = [s.key for s in found.service.fields if fields.get(f"forget_{s.key}")]
            if not problems:
                try:
                    connections.save(
                        name,
                        found.service,
                        config,
                        secrets,
                        forget=forget,
                        creating=False,
                        by=who(request),
                        now=clock(),
                    )
                except ChannelError as exc:
                    problems = exc.problems
            if problems:
                return source_form(
                    request,
                    found.service,
                    name=name,
                    values={**fields, **{k: v for k, v in config.items() if isinstance(v, bool)}},
                    kept=found.kept,
                    problems=problems,
                    editing=True,
                )
            return source_saved(name, fields)

        @router.post("/sources/{name}/test", dependencies=source_change)
        def source_test(name: str) -> Response:
            return source_tested(name)

        @router.post("/sources/{name}/remove", dependencies=source_change)
        def source_remove(request: Request, name: str) -> Response:
            if not connections.remove(name, by=who(request), now=clock()):
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            return back(f"/ui/sources?removed={quote(name)}")

        @router.get("/sources/add/{kind}", response_class=HTMLResponse, dependencies=source_admin)
        def source_new(request: Request, kind: str) -> Response:
            service = SOURCE_SERVICES.get(kind)
            if service is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            defaults = {
                spec.key: spec.default == "on" if spec.kind == "bool" else spec.default
                for spec in service.fields
                if spec.default
            }
            return source_form(request, service, name="", values=defaults, editing=False)

        @router.post("/sources/add/{kind}", dependencies=source_change)
        def source_create(
            request: Request, kind: str, fields: Annotated[dict[str, str], Depends(_fields)]
        ) -> Response:
            service = SOURCE_SERVICES.get(kind)
            if service is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            name = fields.get("name", "").strip()
            config, secrets, problems = read_form(service, fields)
            if not problems:
                try:
                    connections.save(
                        name, service, config, secrets, creating=True, by=who(request), now=clock()
                    )
                except ChannelError as exc:
                    problems = exc.problems
            if problems:
                return source_form(
                    request,
                    service,
                    name=name,
                    values={**fields, **{k: v for k, v in config.items() if isinstance(v, bool)}},
                    problems=problems,
                    editing=False,
                )
            return source_saved(name, fields)

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

    @router.post("/actions/stop", dependencies=[Depends(_same_origin), Depends(need("operator"))])
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

    def find_draft(name: str) -> tuple[postmortems.Draft, str]:
        found = postmortems.read_draft(rules.postmortems.dir, name) if keeps else None
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        return found

    @router.get("/postmortems/{name}", response_class=HTMLResponse)
    def postmortem(
        request: Request,
        name: str,
        done: Annotated[str | None, Query(max_length=20)] = None,
    ) -> Response:
        draft, raw = find_draft(name)
        # Masked before it is rendered: the rendered page does not pass through `shown`.
        text = redact_text(raw, max_length=views.DETAIL_LIMIT * 30)
        body = markdown.render(text)
        diagrams = markdown.DIAGRAM in body
        role = request.state.role
        return templates.TemplateResponse(
            request,
            "postmortem.html",
            {
                "draft": draft,
                "text": text,
                "body": body,
                "diagrams": diagrams,
                "here": "postmortems",
                "role": role,
                "can_operate": allows(role, "operator"),
                "can_admin": allows(role, "admin"),
                "to_review": 0,
                "editable": text == raw,
                "can_rewrite": writer is not None
                and draft.status == "draft"
                and draft.incident is not None
                and postmortems.missing_prose(raw),
                "done": done,
            },
            headers=DIAGRAM_HEADERS if diagrams else HEADERS,
        )

    operate = [Depends(_same_origin), Depends(need("operator"))]

    def committed(draft: postmortems.Draft, message: str) -> None:
        if writer is not None:
            path = Path(rules.postmortems.dir) / draft.name
            writer.commit(path, message, draft.incident)

    @router.post("/postmortems/{name}/status", dependencies=operate)
    def postmortem_status(
        request: Request,
        name: str,
        to: Annotated[Literal["draft", "reviewed"], Form()],
    ) -> Response:
        find_draft(name)
        try:
            draft = postmortems.set_status(rules.postmortems.dir, name, to)
        except postmortems.DraftError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
        store.append(
            "postmortem.reviewed",
            {"path": name, "status": to, "by": who(request)},
            incident_id=draft.incident,
        )
        committed(draft, f"Postmortem {to}: {name}")
        return back(f"/ui/postmortems/{quote(name)}?done={to}")

    @router.get(
        "/postmortems/{name}/edit",
        response_class=HTMLResponse,
        dependencies=[Depends(need("operator"))],
    )
    def postmortem_edit(request: Request, name: str) -> Response:
        draft, raw = find_draft(name)
        if redact_text(raw, max_length=views.DETAIL_LIMIT * 30) != raw:
            # The page would have to show something the console masks: edit the file itself.
            raise HTTPException(
                status.HTTP_409_CONFLICT, "this file holds what looks like a secret"
            )
        return page(
            request, "postmortem_edit.html", draft=draft, text=raw, problem="", here="postmortems"
        )

    @router.post("/postmortems/{name}/edit", dependencies=operate)
    def postmortem_save(
        request: Request,
        name: str,
        text: Annotated[str, Form(max_length=postmortems.MAX_BYTES)],
    ) -> Response:
        draft, raw = find_draft(name)
        if redact_text(raw, max_length=views.DETAIL_LIMIT * 30) != raw:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "this file holds what looks like a secret"
            )
        try:
            saved = postmortems.save_text(rules.postmortems.dir, name, text)
        except postmortems.DraftError as exc:
            return page(
                request,
                "postmortem_edit.html",
                422,
                draft=draft,
                text=text,
                problem=str(exc),
                here="postmortems",
            )
        store.append(
            "postmortem.edited",
            {"path": name, "status": saved.status, "by": who(request)},
            incident_id=saved.incident,
        )
        committed(saved, f"Postmortem edited: {name}")
        return back(f"/ui/postmortems/{quote(name)}?done=saved")

    @router.post("/postmortems/{name}/rewrite", dependencies=operate)
    def postmortem_rewrite(request: Request, name: str) -> Response:
        draft, raw = find_draft(name)
        incident = None if draft.incident is None else store.incident(draft.incident)
        if (
            writer is None
            or incident is None
            or draft.status != "draft"
            or not postmortems.missing_prose(raw)
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "only a draft without its prose is written again"
            )
        path = Path(rules.postmortems.dir) / name
        writer.draft(incident, clock(), replace=path, by=who(request))
        again = postmortems.read_draft(rules.postmortems.dir, name)
        done = "rewritten" if again and not postmortems.missing_prose(again[1]) else "still-missing"
        return back(f"/ui/postmortems/{quote(name)}?done={done}")

    @app.get("/", include_in_schema=False, dependencies=[Depends(proxied)])
    def home() -> Response:
        return RedirectResponse("/ui", status.HTTP_307_TEMPORARY_REDIRECT)

    app.include_router(door)
    app.include_router(router)
    app.mount("/ui/static", StaticFiles(directory=_HERE / "static"), name="ui-static")
