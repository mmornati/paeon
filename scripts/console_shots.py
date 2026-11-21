"""Take the screenshots of docs/console.md from the real console, on synthetic data only.

    uv run --with playwright python scripts/console_shots.py [out-dir]

The store is filled by `tests/ui_data.py` (`populate`, `populate_actions`, `populate_history`)
plus one quiet incident written here; channels and a source are typed into the console's own
forms and answered by a mock transport; a stand-in executor takes the Go. Nothing reaches a
model, a server or a phone. Each page is shot headless with Playwright, converted with cwebp,
and gets a `.json` sidecar that says how it was made. Needs `cwebp` on the PATH and a
Playwright Chromium (`uv run --with playwright playwright install chromium`).
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2 as httpx
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from paeon.app import create_app  # noqa: E402
from paeon.config import Settings  # noqa: E402
from paeon.correlation import Correlator  # noqa: E402
from paeon.recorder import Recorder  # noqa: E402
from paeon.remediation import Journal  # noqa: E402
from paeon.rules import parse_rules  # noqa: E402
from paeon.schema import Alert, Severity, Status  # noqa: E402
from paeon.store import Store  # noqa: E402
from ui_data import ACTING_RULES, populate, populate_actions, populate_history  # noqa: E402

PASSWORD = "synthetic-demo-password"  # noqa: S105 - a fictional console, on a temporary database
SECRET = "s" * 40
TOKEN = "t" * 32
DESK = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
STAMP = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Executor:
    """Stands for the executor: takes a Go and says it runs."""

    def kill(self) -> None:
        return None

    def journal(self, after: int) -> Journal:
        return Journal([], after)

    def propose(self, proposal: Any) -> dict[str, Any]:
        return {}

    def cancel(self, proposal_id: int, reason: str) -> None:
        return None

    def vote_links(self, incident_id: int, about: str) -> dict[str, str]:
        return {}

    def answer(self, proposal_id: int, answer: str, by: str) -> dict[str, Any]:
        return {"ok": True, "message": "Approved. The plan is running; the result follows."}


def answered(request: httpx.Request) -> httpx.Response:
    """What every service a channel or a source reaches answers: fine, and nothing firing."""
    if request.url.path.endswith("/alerts"):
        return httpx.Response(200, json=[])
    if "query" in request.url.path:
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})
    return httpx.Response(200, json={"status": "success", "data": {}})


def rules(postmortems: Path, *, full: bool) -> dict[str, Any]:
    if not full:  # a new install: PAEON_CONSOLE=local and nothing else
        return {"ui": {"enabled": True, "auth": "local", "settings": True}}
    price = {"input_per_mtok": 3, "output_per_mtok": 15}
    return {
        **ACTING_RULES,
        "actions": {**ACTING_RULES["actions"], "console_approval": True},
        "models": {
            "triage": {"backend": "anthropic", "model": "small-model", "price": price},
            "investigate": {"backend": "anthropic", "model": "large-model", "price": price},
            "postmortem": {"backend": "anthropic", "model": "large-model", "price": price},
        },
        "triage": {"enabled": True, "daily_budget": 0.5},
        "investigation": {"enabled": True, "daily_budget": 2.0},
        "postmortems": {"enabled": True, "dir": str(postmortems), "daily_budget": 0.5},
        "notifiers": {"phone": {"type": "ntfy", "url": "https://ntfy.example", "topic": "paeon"}},
        "sources": {
            "alertmanager-home": {"type": "alertmanager", "url": "http://alertmanager.example:9093"}
        },
        "ui": {"enabled": True, "auth": "local", "settings": True},
    }


def quiet_incident(store: Store, now: datetime) -> int:
    """An incident whose source never sent the resolution: last seen three days ago."""
    opened = now - timedelta(days=3, hours=2)
    recorder = Recorder(store, Correlator(store, parse_rules({})))
    recorder.observe(
        Alert(
            source="alertmanager-home",
            fingerprint="nas1",
            status=Status.FIRING,
            severity=Severity.WARNING,
            title="nas_unreachable",
            labels={"host": "nas-1"},
            starts_at=opened,
            observed_at=opened,
        )
    )
    found = store.incident_of("alertmanager-home", "nas1")
    if found is None:
        raise SystemExit("the quiet incident was not opened")
    return found


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def serve(store: Store, document: dict[str, Any], *, password: str | None) -> Iterator[str]:
    settings = Settings(
        db_path=Path("paeon.db"),
        host="127.0.0.1",
        port=0,
        ingest_token=TOKEN,
        openobserve=None,
        heartbeat_url=None,
        heartbeat_seconds=60,
        rules=parse_rules(document),
        executor_token=TOKEN,
        secret_key=SECRET,
        ui_admin_password=password,
    )
    mock = httpx.MockTransport(answered)
    app = create_app(
        settings,
        store,
        executor=Executor(),
        source_transport=mock,
        channel_transport=mock,
        model_clients={},
    )
    port = free_port()
    # No lifespan: the stages that would call a model never start.
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join()


ORIGIN = (
    "ORIGIN: not generated imagery. A screenshot of Paeon's own console (src/paeon/ui): {what}."
    " Rendered from the synthetic demo data in tests/ui_data.py (populate, populate_actions,"
    " populate_history) and fictional channels and sources typed in by scripts/console_shots.py,"
    " answered by a mock transport, with a stand-in executor; no real server, address, token,"
    " alert or model answer is shown. Captured headless with Playwright ({view}),"
    " converted with cwebp."
)


class Shooter:
    def __init__(self, out: Path, scratch: Path) -> None:
        self.out = out
        self.scratch = scratch
        self.made: list[str] = []

    def take(self, page: Any, name: str, what: str, *, full: bool = False) -> None:
        png = self.scratch / f"{name}.png"
        page.wait_for_timeout(250)
        page.screenshot(path=str(png), full_page=full)
        webp = self.out / f"{name}.webp"
        subprocess.run(  # noqa: S603
            ["cwebp", "-quiet", "-q", "82", str(png), "-o", str(webp)],  # noqa: S607
            check=True,
        )
        size = page.viewport_size
        phone = size["width"] < 600
        view = (
            f"phone viewport, {size['width']}x{size['height']} at 3x"
            if phone
            else f"desktop viewport, {size['width']}x{size['height']} at 4/3x, stored at 1920x1200"
        )
        scheme = page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
        what = what + (", dark scheme" if scheme else "")
        sidecar = {"prompt": ORIGIN.format(what=what, view=view), "createdAt": STAMP}
        (self.out / f"{name}.webp.json").write_text(json.dumps(sidecar, indent=2) + "\n")
        self.made.append(name)
        print(name)


def show(page: Any, selector: str) -> None:
    """Scroll a section to the top, below the sticky bars."""
    page.evaluate(
        "s => { const e = document.querySelector(s);"
        " window.scrollTo(0, e.getBoundingClientRect().top + window.scrollY - 110); }",
        selector,
    )


def sign_in(page: Any, base: str) -> None:
    page.goto(f"{base}/ui/signin")
    page.fill("#f-password", PASSWORD)
    page.locator("form button").last.click()
    page.wait_for_url(f"{base}/ui**")


def main(out: Path) -> None:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="paeon-shots-"))
    shot = Shooter(out, scratch)
    now = datetime.now(UTC)

    full = Store(str(scratch / "full.db"))
    ids = populate(full, now)
    populate_actions(full, ids, scratch / "postmortems")
    # The test draft carries two probes for the renderer's escaping; a reader needs neither.
    draft = scratch / "postmortems" / "2026-11-17-worker-stalled.md"
    text = draft.read_text(encoding="utf-8")
    for probe in (", not [this](javascript:alert(1))", "<script>alert(1)</script>\n"):
        text = text.replace(probe, "")
    draft.write_text(text, encoding="utf-8")
    populate_history(full, 80, now)
    quiet = quiet_incident(full, now)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        def desk(**more: Any) -> Any:
            context = browser.new_context(
                viewport=DESK, device_scale_factor=4 / 3, **{"color_scheme": "light", **more}
            )
            return context.new_page()

        def phone(**more: Any) -> Any:
            context = browser.new_context(
                viewport=PHONE,
                device_scale_factor=3,
                is_mobile=True,
                has_touch=True,
                **{"color_scheme": "light", **more},
            )
            return context.new_page()

        # --- a new install: the first-run page, then what is left to set up ----------------
        fresh = Store(str(scratch / "fresh.db"))
        with serve(fresh, rules(scratch / "pm0", full=False), password=None) as base:
            page = desk()
            page.goto(f"{base}/ui/setup")
            shot.take(page, "setup", "the first-run page: the code from the log, then the password")
        with serve(fresh, rules(scratch / "pm0", full=False), password=PASSWORD) as base:
            page = desk()
            sign_in(page, base)
            page.goto(f"{base}/ui")
            shot.take(
                page,
                "getting-started",
                "the getting-started list on the incidents page of a new install",
            )
            small = phone()
            sign_in(small, base)
            small.goto(f"{base}/ui")
            shot.take(
                small,
                "phone-getting-started",
                "the getting-started list of a new install, on a phone",
            )

        with serve(full, rules(scratch / "postmortems", full=True), password=PASSWORD) as base:
            page = desk()
            sign_in(page, base)
            state = scratch / "state.json"
            page.context.storage_state(path=str(state))

            # --- channels and sources, typed in -----------------------------------------
            page.goto(f"{base}/ui/channels/add")
            shot.take(
                page,
                "channel-services",
                "the list of services to choose from when adding a channel",
            )
            page.goto(f"{base}/ui/channels/add/ntfy")
            page.fill("#f-name", "family")
            page.fill("#f-url", "https://ntfy.example")
            page.fill("#f-topic", "family-alerts")
            page.fill("#f-token", "tk_fictional_token_for_the_demo")
            page.get_by_role("button", name="Save and send a test").click()
            page.wait_for_load_state()
            page.goto(f"{base}/ui/channels")
            shot.take(
                page,
                "channels",
                "the Channels page, with a channel added in the console and tested",
            )
            page.goto(f"{base}/ui/channels/family/edit")
            shot.take(
                page, "channel-edit", "the form of a channel added in the console, its token kept"
            )

            page.goto(f"{base}/ui/sources/add/prometheus")
            page.fill("#f-name", "prometheus")
            page.fill("#f-url", "http://prometheus.example:9090")
            page.fill("#f-metrics", "node_*\nup")
            page.locator("form.form button.btn-primary").first.click()
            page.wait_for_load_state()
            page.goto(f"{base}/ui/sources")
            shot.take(
                page,
                "sources",
                "the Sources page: a source of the rules file, Paeon's own webhooks,"
                " and a Prometheus added in the console",
            )

            # --- the incident explorer ----------------------------------------------------
            page.goto(f"{base}/ui")
            shot.take(
                page,
                "explorer",
                "the incident explorer: status tabs, range, search, the chart of incidents per day,"
                " filters and the table",
            )
            page.goto(f"{base}/ui?range=30d&severity=critical&host=server-1")
            shot.take(
                page,
                "explorer-filtered",
                "the incident explorer over 30 days, filtered on critical incidents of one host",
            )
            page.goto(f"{base}/ui?status=needs")
            shot.take(page, "explorer-needs-you", "the Needs you tab of the incident explorer")
            page.goto(f"{base}/ui?q=nas")
            shot.take(
                page,
                "explorer-quiet",
                "the incident explorer searched for one incident, marked quiet for three days",
            )

            # --- one incident -------------------------------------------------------------
            media = ids["media"]
            page.goto(f"{base}/ui/incidents/{media}")
            shot.take(
                page,
                "incident",
                "an incident page: the stage strip, the conclusion, then the investigation",
            )
            show(page, "#investigation")
            shot.take(
                page,
                "investigation",
                "the investigation of an incident: findings and evidence tied to tool calls",
            )
            show(page, "#plan")
            shot.take(
                page, "plan-console-go", "a plan waiting for a Go, with Go and No-go in the console"
            )
            page.locator("#record > summary").click()
            show(page, "#record")
            shot.take(
                page, "record", "the record of an incident, unfolded: every event, oldest first"
            )

            page.goto(f"{base}/ui/incidents/{ids['worker']}")
            show(page, "#plan")
            shot.take(page, "plan-verified", "a plan a runbook ran without asking, verified")

            page.goto(f"{base}/ui/incidents/{quiet}")
            show(page, "#status")
            page.locator(".close-form summary").click()
            page.fill(
                "input[name=note]",
                "The NAS was rebooted by hand; this source never sends the recovery",
            )
            shot.take(
                page,
                "still-open",
                "the Still open? box of an incident quiet for three days, with the close form",
            )
            page.get_by_role("button", name="Close the incident").click()
            page.wait_for_load_state()
            shot.take(
                page, "closed-by-hand", "the same incident once closed by hand: who, when and why"
            )

            # --- log, spend, actions, postmortems ----------------------------------------
            page.goto(f"{base}/ui/log")
            shot.take(page, "log", "the Log page: search, kinds, every event newest first")
            page.goto(f"{base}/ui/log?incident={media}")
            shot.take(page, "log-incident", "the Log page narrowed to one incident")
            page.goto(f"{base}/ui/log?show=feedback")
            shot.take(page, "log-what-you-did", "the Log page, filtered on what a person did")
            page.goto(f"{base}/ui/ledger?days=30")
            shot.take(
                page,
                "spend",
                "the Spend page over 30 days: totals, budgets and the cost per day by stage",
            )
            page.goto(f"{base}/ui/actions")
            shot.take(page, "actions", "the Actions page")
            page.goto(f"{base}/ui/autonomy")
            shot.take(page, "autonomy", "the Autonomy page")
            page.goto(f"{base}/ui/postmortems")
            shot.take(
                page, "postmortems", "the Postmortems page: status tabs and the table of drafts"
            )
            name = "2026-11-17-worker-stalled.md"  # the draft populate_actions writes
            page.goto(f"{base}/ui/postmortems/{name}")
            shot.take(page, "postmortem-review", "a postmortem draft with its Review block")
            page.evaluate("document.querySelector('.md svg')?.scrollIntoView({block: 'center'})")
            shot.take(page, "postmortem", "a rendered postmortem with a sequence diagram")
            page.goto(f"{base}/ui/postmortems/{name}/edit")
            shot.take(
                page,
                "postmortem-edit",
                "the postmortem editor: the whole file, front matter included",
            )

            # --- dark, and on a phone -----------------------------------------------------
            night = desk(storage_state=str(state), color_scheme="dark")
            night.goto(f"{base}/ui")
            shot.take(night, "explorer-dark", "the incident explorer, dark scheme")

            small = phone(storage_state=str(state))
            small.goto(f"{base}/ui")
            shot.take(small, "phone-explorer", "the incident explorer on a phone, with the tab bar")
            small.goto(f"{base}/ui/incidents/{media}")
            shot.take(small, "phone-incident", "an incident page on a phone")
            small.goto(f"{base}/ui/channels/add/ntfy")
            shot.take(small, "phone-channel-form", "a new ntfy channel, on a phone")
            small.goto(f"{base}/ui/postmortems/{name}")
            shot.take(small, "phone-postmortem-review", "reviewing a postmortem, on a phone")
            small_night = phone(storage_state=str(state), color_scheme="dark")
            small_night.goto(f"{base}/ui")
            shot.take(
                small_night, "phone-explorer-dark", "the incident explorer on a phone, dark scheme"
            )

        browser.close()
    shutil.rmtree(scratch, ignore_errors=True)
    print(f"{len(shot.made)} screenshots in {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "assets" / "console")
