# Paeon

Alert triage, investigation and approved remediation for self-hosted servers.

Paeon listens to your alerting (OpenObserve, uptime-kuma, anything that speaks the Alertmanager
webhook), and is designed to work in four stages with a hard line between thinking and doing:
a cheap model triages, a strong model investigates with read-only tools, you approve on your phone,
and plain code executes a closed catalog of typed actions. Autonomy is earned per action and per
target. The full design is in
[docs/llm-incident-response-architecture.md](docs/llm-incident-response-architecture.md).

## Status: phase 1, observe

Paeon **records, correlates and, if you enable it, asks a model for an opinion that nothing acts
on**. It sends no page, takes no action and changes nothing in your alerting. The roadmap is in
[§9 of the design](docs/llm-incident-response-architecture.md#9-roadmap).

| Phase | What | State |
|---|---|---|
| 0. Record | Alert schema, append-only event log, OpenObserve pull adapter, webhooks, source staleness, heartbeat | built |
| 1. Observe | Correlation into incidents, flap and maintenance flags, change calendar, triage in shadow mode, daily digest with feedback | built, see the limits below |
| 2. Investigate | Read-only investigation by a strong model, analysis by notification, postmortem drafts | planned |
| 3. Approve and act | Action catalog, signed Go / No-go, executor, verification, rollback | planned |
| 4. UI and MCP | Incident pages, autonomy matrix | planned |
| 5. Earned autonomy | Promotion proposals, `auto` for the first actions, Undo | planned |
| 6. Open-source release | Docs, threat model, contribution guide | planned |

What exists:

- **Alert schema** (`paeon.schema.Alert`): one normalized observation, modelled on the
  Alertmanager webhook.
- **Event log** (`paeon.store`): SQLite, append-only (enforced by triggers). Every decision below
  is an event; incidents are a projection of it.
- **Recorder** (`paeon.recorder`): turns repeated observations into state changes, so an alert
  that is evaluated every five minutes for two hours is one `alert.firing` and one
  `alert.resolved`.
- **Sources**: the OpenObserve pull adapter (polls the `triggers` stream; nothing changes in
  OpenObserve), and `POST /ingest/uptime-kuma` and `POST /ingest/alertmanager` behind a bearer
  token. A pull source that stops answering opens an internal `source_stale` alert, and Paeon can
  push a heartbeat to an uptime-kuma push monitor.
- **Correlation** (`paeon.correlation`): the same alert firing again within a window is the same
  incident; alerts on the same host within a few minutes join the open incident. Flapping and
  maintenance windows are flags on the incident. Nothing is dropped.
- **Change calendar**: `POST /ingest/change` records a deploy, an update run or a backup, announced
  by whatever did it.
- **Triage in shadow mode** (`paeon.triage`): one model call per incident with a context pack built
  by code (the alerts, their history, recent changes, other open incidents), redacted before it
  leaves. The verdict (`noise`, `transient`, `known_issue`, `real`) is recorded with the model that
  answered, the tokens and the cost. It fails open: model down, invalid answer twice, low
  confidence, budget spent or storm guard hit, and the incident counts as real. Page-level alerts
  are never sent to the model.
- **Model clients** (`paeon.models`): the Anthropic Messages API and the OpenAI-compatible chat
  API, each with a configurable base URL.
- **Digest and feedback** (`paeon.digest`): `paeon digest`, `GET /digest`, and optionally one ntfy
  message a day. `paeon feedback <id> up|down` records whether triage was right.

Limits of phase 1 as built:

- The context pack has no re-run of the alert's query and no postmortems: the first needs the
  alert definition from OpenObserve (an open question of the design), the second arrives with
  phase 2.
- Feedback is given from the CLI or the API. The digest notification has no 👍 / 👎 buttons yet.
- Maintenance windows are weekly and declared in the rules file; there is no storm guard on
  correlation itself, only on triage.

## Run it

```bash
cp .env.example .env    # set PAEON_INGEST_TOKEN, and the OpenObserve variables if you use it
uv sync
set -a; . ./.env; set +a
uv run paeon serve
```

Read what was recorded:

```bash
uv run paeon events --kind alert.firing
```

```bash
uv run paeon incidents
```

```bash
uv run paeon digest --hours 24
```

### Rules, models and triage

Behaviour is one YAML file, named by `PAEON_CONFIG`: correlation windows, host rules, maintenance
windows, model aliases, triage and the digest. Start from
[examples/paeon.yaml](examples/paeon.yaml); every key is optional, and without the file Paeon
correlates with the defaults and calls no model. Secrets stay in the environment: the file names
the variable (`api_key_env`, `token_env`), and each accepts `NAME_FILE` too.

Triage is off until `triage.enabled: true`. When it is on, alert titles, descriptions and labels
are sent to the model you configured, after redaction of secret-shaped strings. Raw payloads and
log lines are not sent.

A deployment example is in [examples/compose.yaml](examples/compose.yaml).

### Change calendar

Tell Paeon about planned work, from the script that does it:

```bash
curl -X POST http://paeon:8080/ingest/change -H "Authorization: Bearer $PAEON_INGEST_TOKEN" -H 'Content-Type: application/json' -d '{"kind": "update", "title": "weekly updater", "host": "server-1"}'
```

### uptime-kuma

Add a notification of type **Webhook**: post URL `http://paeon:8080/ingest/uptime-kuma`, body
`application/json`, and an additional header `{"Authorization": "Bearer <PAEON_INGEST_TOKEN>"}`.
Apply it to every monitor: Paeon only observes, and what it later acts on is decided by its own
routes.

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
