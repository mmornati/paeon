# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Server-rendered: Jinja templates served by the existing FastAPI app, one hand-written CSS file,
a little vanilla JS for live refresh. No Node and no build step; the UI ships in the same wheel
and Docker image as the service.

## Users

One operator of a self-hosted server (a home server or a small VPS), who is also the person who
wrote the alert rules. Phone first: they tap an ntfy notification to see why Paeon said what it
said about an incident. Later, at a desk, they review the day: what came in, what the models
decided, what it cost.

## Product Purpose

Paeon listens to alerting (OpenObserve, uptime-kuma, Alertmanager webhooks), groups alerts into
incidents, and runs them through stages with a hard line between thinking and doing: a cheap
model triages, a strong model investigates with read-only tools, a person approves, plain code
executes a closed catalog of typed actions. The console exists so the operator stops opening the
SQLite file: it shows what was taken into account, which model or tool was called and why, and
what Paeon sent or did.

## Positioning

Everything Paeon decides is one event in an append-only log; incidents, timelines, cost and
postmortems are projections of it. The console renders that record. It never shows a state the
log cannot account for.

## Operating Context

- Entry points: an ntfy notification (analysis of an investigation, daily digest), or a bookmark.
- Runs next to Traefik and Authentik on the operator's server; other sources of truth
  (OpenObserve dashboards, markdown postmortems, runbooks) stay where they are.
- Stages can each be off. A deployment may record and correlate only, with no model call at all.

## Capabilities and Constraints

- As built (phase 2): record, correlate, triage in shadow mode, investigate read-only, draft
  postmortems, daily digest, feedback on triage. Paeon takes no action on the server.
- The UI is read-only apart from triage feedback (up / down), which the API already accepts.
- Off by default (`ui.enabled`). When on, Paeon relies on the reverse proxy (Traefik with
  Authentik forward-auth) to protect the UI paths; it has no login of its own.
- Planned, not built: approvals (Go / No-go), the action catalog and executor, the autonomy
  matrix. The UI must not show them as if they existed.
- Terms: alert (one source's observation), incident (correlated alerts), stage (triage,
  investigation, postmortem), verdict (`noise`, `transient`, `known_issue`, `real`), tool call,
  model call, change (an entry of the change calendar), shadow mode.

## Evidence on Hand

- The event log and its kinds (`src/paeon/store.py`, and every `store.append` call).
- The design: `docs/llm-incident-response-architecture.md` (UI in section 8, roadmap in 9).
- No logo, no brand assets, no screenshots. The name is Paeon (the physician of the gods).
- No real incident data is committed; demonstration data must be synthetic and labelled.

## Product Principles

1. The log is the record. Show the event, its time and its source before any summary of it.
2. Thinking and doing are different things and must look different.
3. Say what was not done: a skipped stage, a spent budget, a failed call are facts worth a line.
4. Cost is part of every model decision, shown where the decision is shown.
5. Nothing private leaves, and nothing shown was unredacted first.
