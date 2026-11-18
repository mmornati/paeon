"""`paeon serve` runs the core and `paeon executor` the executor; `events`, `incidents`,
`digest`, `autonomy` and `feedback` read and annotate the record; `mcp` serves it read-only to
an assistant; `stop`, `resume` and `reinstate` are the owner's switches on the executor."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from paeon import __version__, autonomy
from paeon import digest as digests
from paeon.config import ConfigError, load, load_executor
from paeon.rules import Rules, RulesError, load_rules
from paeon.store import Store


def _serve() -> int:
    import uvicorn

    from paeon.app import create_app

    settings = load(os.environ)
    store = Store(settings.db_path)
    try:
        uvicorn.run(
            create_app(settings, store), host=settings.host, port=settings.port, log_config=None
        )
    finally:
        store.close()
    return 0


def _executor() -> int:
    import uvicorn

    from paeon.executor import ExecutorStore
    from paeon.executor_app import build_executor, create_executor_app

    settings = load_executor(os.environ)
    store = ExecutorStore(settings.db_path)
    try:
        app = create_executor_app(settings, build_executor(settings, store))
        uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
    finally:
        store.close()
    return 0


def _rules() -> Rules:
    path = os.environ.get("PAEON_CONFIG", "").strip()
    return load_rules(Path(path)) if path else Rules()


def _mcp(db: str) -> int:
    from paeon import mcp

    store = Store(db)
    try:
        mcp.serve(store, _rules(), sys.stdin, sys.stdout)
    finally:
        store.close()
    return 0


def _autonomy(db: str) -> int:
    store = Store(db)
    try:
        records = autonomy.matrix(
            store.events(kinds=autonomy.KINDS), _rules().actions, datetime.now(UTC)
        )
        switch = store.events(kinds=("killswitch.engaged", "killswitch.released"))
        if autonomy.killed(switch):
            sys.stdout.write("The kill switch is engaged: every plan is only recorded.\n")
        if not records:
            sys.stdout.write("No action has run yet.\n")
        for r in records:
            held = f" (policy says {r.policy}; demoted: {r.demoted})" if r.demoted else ""
            sys.stdout.write(
                f"{r.action}({r.target}) on {r.alert}: {r.effective}{held};"
                f" {r.verified} verified of {r.runs}, {r.failed} failed, {r.undone} undone,"
                f" {r.wrong} wrong; {r.streak} in a row\n"
            )
            if r.proposal:
                sys.stdout.write(f"    earned, add under actions.policies: {r.proposal}\n")
    finally:
        store.close()
    return 0


def _replay(args: argparse.Namespace) -> int:
    from paeon import replay
    from paeon.config import _secret
    from paeon.models import build_client

    rules = _rules()
    stage = rules.triage if args.stage == "triage" else rules.investigation
    since = None if args.days is None else datetime.now(UTC) - timedelta(days=args.days)
    skipped = 0
    if args.corpus:
        with Path(args.corpus).open(encoding="utf-8") as file:
            triaged, investigated = replay.read_corpus(file)
    else:
        store = Store(args.db)
        try:
            triaged = replay.triage_cases(store, since=since)
            investigated, skipped = replay.investigation_cases(store, since=since)
        finally:
            store.close()
    cases: Sequence[Any] = triaged if args.stage == "triage" else investigated
    cases = cases[-args.limit :] if args.limit else cases
    if args.export:
        with Path(args.export).open("w", encoding="utf-8") as file:
            count = replay.write_corpus(list(cases), file)
        sys.stdout.write(f"{count} cases written to {args.export}\n")
        return 0
    if args.dry_run or not cases:
        for case in cases:
            rated = f" (you said {case.label})" if case.label else ""
            sys.stdout.write(f"#{case.incident_id} {case.title}{rated}\n")
        sys.stdout.write(f"{len(cases)} cases, {skipped} that cannot be replayed.\n")
        return 0
    if stage.model not in rules.models:
        raise ConfigError(f"{args.stage}.model {stage.model!r} is not defined under `models`")
    alias = rules.models[stage.model]
    if alias.model == "auto":
        sys.stderr.write(
            "paeon: the model is `auto`: a gateway may answer with a different model each"
            " time. Pin one to tell a prompt change from a routing change.\n"
        )
    key = _secret(os.environ, alias.api_key_env) if alias.api_key_env else ""
    client = build_client(alias, key)
    try:
        if args.stage == "triage":
            report = replay.report_triage(replay.replay_triage(cases, client, alias))
        else:
            results = replay.replay_investigation(cases, client, rules)
            report = replay.report_investigation(results, skipped)
    finally:
        client.close()
    sys.stdout.write("\n".join(report.lines) + f"\nCost of this replay: {report.cost:g}.\n")
    return 1 if report.failed else 0


def _switch(db: str, kind: str, payload: dict[str, str]) -> int:
    """Write one of the owner's decisions straight into the executor's journal."""
    if not Path(db).is_file():
        sys.stderr.write(f"paeon: no executor database at {db}\n")
        return 1
    store = Store(db)
    try:
        store.append(kind, {**payload, "via": "cli"})
    finally:
        store.close()
    return 0


def _events(db: str, kind: str | None, after: int) -> int:
    store = Store(db)
    try:
        for event in store.events(after_id=after, kind=kind):
            line = {
                "id": event.id,
                "recorded_at": event.recorded_at.isoformat(),
                "kind": event.kind,
                "source": event.source,
                "fingerprint": event.fingerprint,
                "payload": event.payload,
            }
            sys.stdout.write(json.dumps(line, separators=(",", ":")) + "\n")
    finally:
        store.close()
    return 0


def _incidents(db: str) -> int:
    store = Store(db)
    try:
        for incident in store.incidents():
            sys.stdout.write(incident.model_dump_json() + "\n")
    finally:
        store.close()
    return 0


def _digest(db: str, hours: int) -> int:
    store = Store(db)
    try:
        now = datetime.now(UTC)
        report = digests.build(store, since=now - timedelta(hours=hours), until=now)
        sys.stdout.write(digests.render(report) + "\n")
    finally:
        store.close()
    return 0


def _feedback(db: str, incident_id: int, vote: str, note: str) -> int:
    store = Store(db)
    try:
        if store.incident(incident_id) is None:
            sys.stderr.write(f"paeon: no incident {incident_id}\n")
            return 1
        store.append(
            "feedback.recorded",
            {"about": "triage", "vote": vote, "note": note},
            incident_id=incident_id,
        )
    finally:
        store.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="paeon", description=__doc__)
    parser.add_argument("--version", action="version", version=f"paeon {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    default_db = os.environ.get("PAEON_DB", "paeon.sqlite")
    commands.add_parser("serve", help="run the service (configured by PAEON_* variables)")
    events = commands.add_parser("events", help="print the event log as JSON lines")
    events.add_argument("--db", default=default_db)
    incidents = commands.add_parser("incidents", help="print the incidents as JSON lines")
    incidents.add_argument("--db", default=default_db)
    digest = commands.add_parser("digest", help="print the digest of the last hours")
    digest.add_argument("--db", default=default_db)
    digest.add_argument("--hours", type=int, default=24)
    feedback = commands.add_parser("feedback", help="say whether triage was right on an incident")
    feedback.add_argument("incident", type=int)
    feedback.add_argument("vote", choices=["up", "down"])
    feedback.add_argument("--note", default="")
    feedback.add_argument("--db", default=default_db)
    commands.add_parser("executor", help="run the executor (PAEON_EXECUTOR_* variables)")
    mcp = commands.add_parser("mcp", help="serve the record read-only over MCP, on stdio")
    mcp.add_argument("--db", default=default_db)
    matrix = commands.add_parser("autonomy", help="print each action's level and track record")
    matrix.add_argument("--db", default=default_db)
    executor_db = os.environ.get("PAEON_EXECUTOR_DB", "executor.sqlite")
    stop = commands.add_parser("stop", help="engage the kill switch: plans are only recorded")
    stop.add_argument("--db", default=executor_db, help="the executor's database")
    resume = commands.add_parser("resume", help="release the kill switch")
    resume.add_argument("--db", default=executor_db, help="the executor's database")
    reinstate = commands.add_parser("reinstate", help="lift the demotion of one action")
    reinstate.add_argument("alert", help="the alert's title")
    reinstate.add_argument("action")
    reinstate.add_argument("target")
    reinstate.add_argument("--db", default=executor_db, help="the executor's database")
    replay = commands.add_parser(
        "replay", help="re-run recorded incidents against the model and prompts of today"
    )
    replay.add_argument("stage", choices=["triage", "investigation"])
    replay.add_argument("--db", default=default_db)
    replay.add_argument("--days", type=int, help="only incidents of the last days")
    replay.add_argument("--limit", type=int, help="only the most recent cases")
    replay.add_argument("--corpus", help="read the cases from this file instead of the database")
    replay.add_argument("--export", help="write the cases to this file and call no model")
    replay.add_argument("--dry-run", action="store_true", help="list the cases, call no model")
    events.add_argument("--kind", help="only this kind, e.g. alert.firing")
    events.add_argument("--after", type=int, default=0, help="only events with a greater id")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # The HTTP client logs every request URL at INFO, and the heartbeat URL carries a token.
    for name in ("httpx2", "httpcore2"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        if args.command == "serve":
            return _serve()
        if args.command == "executor":
            return _executor()
        if args.command == "mcp":
            return _mcp(args.db)
        if args.command == "autonomy":
            return _autonomy(args.db)
        if args.command == "replay":
            return _replay(args)
        if args.command == "stop":
            return _switch(args.db, "killswitch.engaged", {})
        if args.command == "resume":
            return _switch(args.db, "killswitch.released", {})
        if args.command == "reinstate":
            who = {"alert": args.alert, "action": args.action, "target": args.target}
            return _switch(args.db, "autonomy.reinstated", who)
        if args.command == "incidents":
            return _incidents(args.db)
        if args.command == "digest":
            return _digest(args.db, args.hours)
        if args.command == "feedback":
            return _feedback(args.db, args.incident, args.vote, args.note)
        return _events(args.db, args.kind, args.after)
    except (ConfigError, RulesError) as exc:
        sys.stderr.write(f"paeon: {exc}\n")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
