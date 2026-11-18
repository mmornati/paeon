"""`paeon serve` runs the service; `events`, `incidents`, `digest` and `feedback` read and
annotate what it recorded."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from paeon import __version__
from paeon import digest as digests
from paeon.config import ConfigError, load
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
    events.add_argument("--kind", help="only this kind, e.g. alert.firing")
    events.add_argument("--after", type=int, default=0, help="only events with a greater id")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        if args.command == "serve":
            return _serve()
        if args.command == "incidents":
            return _incidents(args.db)
        if args.command == "digest":
            return _digest(args.db, args.hours)
        if args.command == "feedback":
            return _feedback(args.db, args.incident, args.vote, args.note)
        return _events(args.db, args.kind, args.after)
    except ConfigError as exc:
        sys.stderr.write(f"paeon: {exc}\n")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
