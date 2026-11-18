# How it works

``` mermaid
flowchart LR
    subgraph Sources
      OO[OpenObserve]
      UK[uptime-kuma]
      AM[Alertmanager<br/>webhook]
    end
    OO & UK & AM --> REC[Record<br/>append-only event log]
    REC --> COR[Correlate<br/>incidents, flapping,<br/>maintenance]
    COR --> TRI[Triage<br/>cheap model, one call,<br/>no tools]
    TRI --> INV[Investigate<br/>strong model,<br/>read-only tools]
    COR -- known signature --> RB[Runbook<br/>no model]
    INV --> YOU([You<br/>analysis on ntfy])
    INV --> PM[Postmortem<br/>draft]
    INV & RB -- plan --> EXE
    YOU -- Go / No-go --> EXE
    subgraph Executor [Executor, a separate process]
      EXE[Check the plan<br/>against its own rules] --> ACT[Apply, verify,<br/>roll back]
    end
    ACT --> PM
```

The core (everything on the left) reads attacker-controlled text and talks to models, so it is
never trusted with a write credential. The executor holds one, and believes only its own copy of
the rules and a link you tapped. The full reasoning is in the
[design document](llm-incident-response-architecture.md).

## The stages

Every stage is off until you enable it in the rules file.

| Stage | What it does | Model |
|---|---|---|
| `triage` | One call per incident with a context pack built by code. Verdict: `noise`, `transient`, `known_issue` or `real`. **Shadow mode**: recorded and shown in the digest, nothing acts on it. Page-level alerts are never sent. | cheap |
| `investigation` | An agent loop with read-only tools and a budget. Hands in a summary, the impact, a root cause with a confidence, the evidence, the hypotheses it rejected and a plan. A second model can take over when the first fails or declines. | strong, with tool calls |
| `postmortems` | A markdown draft with YAML front matter once an incident that was investigated or acted on has stayed resolved. Timeline and automation review come from the event log; the model writes the prose; the file says `status: draft`. Optionally committed and pushed to a git repository. | strong |
| `actions` | No model. Plans from an analysis or from a runbook go to the executor. See [Acting](acting.md). | none |

## What the investigator can read

Tools only read, each behind an allow-list. What a tool returns is bounded, redacted and recorded in full.

- **Paeon's own record**: open and past incidents, the history of any alert or monitor, the
  change calendar, full-text search in your runbooks and past postmortems.
- **OpenObserve**: log search on an allow-list of streams, PromQL on an allow-list of metric
  names, and an alert's own definition with its query re-run when the data it reads is listed.
- **Docker**, through a socket proxy: list, inspect (without the environment), and logs for an
  allow-list of containers.
- **uptime-kuma**: the state of every monitor right now.
- **git**: the recent commits of working copies you mount (subjects and file names, no authors).
- **Other hosts**, over SSH: commands you wrote, chosen by name. The model never writes a
  command or an argument.
## Everything is an event

One append-only SQLite log holds every alert, decision, model call (with its cost) and tool result. Incidents, the digest, the console and postmortems are derived from it; nothing is ever edited or removed.

## Module by module

- **Alert schema** (`paeon.schema.Alert`): one normalized observation, modelled on the
  Alertmanager webhook.
- **Event log** (`paeon.store`): SQLite, append-only (enforced by triggers). Every decision is an
  event; incidents are a projection of it.
- **Recorder** (`paeon.recorder`): turns repeated observations into state changes, so an alert
  that is evaluated every five minutes for two hours is one `alert.firing` and one
  `alert.resolved`.
- **Sources** (`paeon.sources`): the OpenObserve pull adapter and the webhook endpoints.
- **Correlation** (`paeon.correlation`): the same alert firing again within a window is the same
  incident; alerts on the same host within a few minutes join the open incident. Flapping and
  maintenance windows are flags on the incident. Nothing is dropped.
- **Triage** (`paeon.triage`), **investigation** (`paeon.investigation`, `paeon.tools`) and
  **postmortems** (`paeon.postmortem`): the three model stages.
- **Knowledge** (`paeon.knowledge`): SQLite FTS5 over your markdown, kept in step with the files.
- **Catalog** (`paeon.catalog`): the actions that exist, and what a plan is.
- **Remediation** (`paeon.remediation`): the core's side of acting. It proposes plans, matches
  runbooks and mirrors the executor's journal into the event log.
- **Executor** (`paeon.executor`, `paeon.executor_app`): the second process. Policy, approvals,
  locks, pre-checks, verification, rollback, Undo, the kill switch.
- **Links** (`paeon.links`): the signed, single-use, expiring links behind every button.
- **Autonomy** (`paeon.autonomy`): track records, demotions and promotion proposals.
- **MCP** (`paeon.mcp`): the read-only server over the record.
- **Actuators** (`paeon.actuators`): Coolify, and the deployment repository with the YAML edit
  that changes one value and nothing else.
- **Replay** (`paeon.replay`) and **telemetry** (`paeon.telemetry`): the regression suite and the
  trace export.
- **Model clients** (`paeon.models`): the Anthropic Messages API (through the `anthropic` SDK)
  and the OpenAI-compatible chat API, both with tool calls.
- **Redaction** (`paeon.redact`): the scrub applied before anything reaches a model.
- **Console** (`paeon.ui`): the event log rendered for a person under `/ui`. Off by default,
  see [Console](console.md).
- **Digest and feedback** (`paeon.digest`): `paeon digest`, `GET /digest`, and optionally one
  ntfy message a day.
