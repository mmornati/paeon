# Paeon

Alert triage, investigation and approved remediation for self-hosted servers.

Paeon listens to your alerting (OpenObserve, uptime-kuma, anything that speaks the Alertmanager
webhook), and is designed to work in four stages with a hard line between thinking and doing:
a cheap model triages, a strong model investigates with read-only tools, you approve on your phone,
and plain code executes a closed catalog of typed actions. Autonomy is earned per action and per
target. The full design is in
[docs/llm-incident-response-architecture.md](docs/llm-incident-response-architecture.md).

## Status: phase 0, record only

What exists today **records alerts and does nothing else**: no model call, no notification, no
action. Its job is to capture two weeks of real alerts as the corpus the later stages are built
and tested against.

- **Alert schema** (`paeon.schema.Alert`): one normalized observation, modelled on the
  Alertmanager webhook.
- **Event log** (`paeon.store`): SQLite, append-only (enforced by triggers).
- **Recorder** (`paeon.recorder`): turns repeated observations into state changes, so an alert
  that is evaluated every five minutes for two hours is one `alert.firing` and one
  `alert.resolved`.
- **OpenObserve pull adapter**: polls the `triggers` stream over the internal network. Nothing
  changes in OpenObserve and its own notifications stay as they are.
- **Webhook endpoints**: `POST /ingest/uptime-kuma` and `POST /ingest/alertmanager`, behind a
  bearer token.
- **Watching the watcher**: a pull source that stops answering opens an internal
  `source_stale` alert, and Paeon can push a heartbeat to an uptime-kuma push monitor.

## Run it

```bash
cp .env.example .env    # set PAEON_INGEST_TOKEN, and the OpenObserve variables if you use it
uv sync
set -a; . ./.env; set +a
uv run paeon serve
```

Export what was recorded, as JSON lines:

```bash
uv run paeon events --kind alert.firing
```

### uptime-kuma

Add a notification of type **Webhook**: post URL `http://paeon:8080/ingest/uptime-kuma`, body
`application/json`, and an additional header `{"Authorization": "Bearer <PAEON_INGEST_TOKEN>"}`.
Apply it to every monitor: in phase 0 Paeon only records, and what it later acts on is decided
by its own routes.

### OpenObserve

Create a user that can read the `triggers` stream and set the `PAEON_OPENOBSERVE_*` variables.
Paeon reads; it never writes to OpenObserve.

### Alertmanager, Grafana and others

Point a webhook receiver at `http://paeon:8080/ingest/alertmanager` with the same bearer token.

## Develop

```bash
uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest --cov
```

## Licence

Apache-2.0.
