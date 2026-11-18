"""`paeon serve` runs the recorder; `paeon events` exports the event log as JSON lines."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence

from paeon import __version__
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="paeon", description=__doc__)
    parser.add_argument("--version", action="version", version=f"paeon {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="record alerts (configured by PAEON_* variables)")
    events = commands.add_parser("events", help="print the event log as JSON lines")
    events.add_argument("--db", default=os.environ.get("PAEON_DB", "paeon.sqlite"))
    events.add_argument("--kind", help="only this kind, e.g. alert.firing")
    events.add_argument("--after", type=int, default=0, help="only events with a greater id")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        if args.command == "serve":
            return _serve()
        return _events(args.db, args.kind, args.after)
    except ConfigError as exc:
        sys.stderr.write(f"paeon: {exc}\n")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
