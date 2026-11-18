# Paeon

Alert triage, investigation and approved remediation for self-hosted servers.

Paeon listens to your alerting (OpenObserve, uptime-kuma, anything that speaks the Alertmanager
webhook), and is designed to work in four stages with a hard line between thinking and doing:
a cheap model triages, a strong model investigates with read-only tools, you approve on your phone,
and plain code executes a closed catalog of typed actions. Autonomy is earned per action and per
target. The full design is in
[docs/llm-incident-response-architecture.md](docs/llm-incident-response-architecture.md).

## Status: phase 2, observe and investigate

Paeon **records, correlates, and, if you enable the stages, triages in shadow mode,
investigates with read-only tools and drafts postmortems**. It takes no action on your server and
changes nothing in your alerting; what it can send is its analysis and a daily digest, to an
ntfy topic you configure. The roadmap is in
[§9 of the design](docs/llm-incident-response-architecture.md#9-roadmap).

| Phase | What | State |
|---|---|---|
| 0. Record | Alert schema, append-only event log, OpenObserve pull adapter, webhooks, source staleness, heartbeat | built |
| 1. Observe | Correlation into incidents, flap and maintenance flags, change calendar, triage in shadow mode, daily digest with feedback | built |
| 2. Investigate | Read-only investigation by a strong model, analysis by notification, postmortem drafts | built, see the limits below |
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
  by code, redacted before it leaves. The verdict (`noise`, `transient`, `known_issue`, `real`) is
  recorded with the model that answered, the tokens and the cost. It fails open, and page-level
  alerts are never sent to the model.
- **Investigation** (`paeon.investigation`): a strong model with tools that only read
  (`paeon.tools`) and a budget in tool calls, time and cost. It must hand in a summary, the
  impact, a root cause with a confidence, the evidence (each claim tied to the tool call that
  supports it; a claim that cites no real call is marked), the hypotheses it rejected, and a plan
  for a person to carry out. Every tool call is recorded with its full redacted result.
- **Tools**: Paeon's own record (open and past incidents, the change calendar, keyword search in
  runbooks and postmortems), OpenObserve (log search on an allow-list of streams, PromQL if you
  allow-list metric names), Docker through a socket proxy (list, inspect without the environment, logs for an
  allow-list of containers).
- **Postmortem drafts** (`paeon.postmortem`): markdown with YAML front matter, written to a
  directory when an investigated incident has stayed resolved. The front matter, the timeline and
  the automation review come from the event log; the model writes the prose; the file says
  `status: draft`.
- **Model clients** (`paeon.models`): the Anthropic Messages API (through the `anthropic` SDK)
  and the OpenAI-compatible chat API, each with a configurable base URL, both with tool calls.
- **Digest and feedback** (`paeon.digest`): `paeon digest`, `GET /digest`, and optionally one ntfy
  message a day. `paeon feedback <id> up|down` records whether triage was right.

Limits as built:

- The investigator cannot re-run an alert's own query: that needs the alert definition from
  OpenObserve, an open question of the design. It can search the streams you allow.
- There is no tool for uptime-kuma monitor history, for git history or for hosts reached over
  SSH: the investigator sees other machines only through their logs and metrics.
- Knowledge search is a keyword scan of markdown files, not an index.
- Postmortem drafts are files in a directory. Committing them to a git repository is left to
  whatever syncs that directory; Paeon holds no git credential.
- The plan in an analysis is free text for a person. The typed action catalog is phase 3.
- A model that declines a request ends the investigation as failed; no fallback model is tried.
- Triage does not yet read past postmortems, and feedback is given from the CLI or the API (the
  notifications have no buttons).

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

Every stage is off until you enable it (`triage`, `investigation`, `postmortems`).

What leaves the server when they are on, always to the model you configured and after redaction
of secret-shaped strings:

- triage: alert titles, descriptions and labels. No raw payload, no log line.
- investigation: the same, plus what its tools return: rows of the log streams you listed,
  listed metrics, container state without the environment, log lines of the containers you listed, and
  excerpts of your runbooks and postmortems.
- postmortems: the incident's timeline and the investigation's findings.

The allow-lists in `investigation.tools` are the control: a stream or a container that is not
listed cannot be read. Give Paeon credentials that can only read as well (an OpenObserve account
limited to search, a Docker socket proxy limited to GET), so that the allow-lists are the second
net and not the only one.

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
