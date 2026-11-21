# Roadmap and limits

| Phase | What | State |
|---|---|---|
| 0. Record | Alert schema, append-only event log, OpenObserve pull adapter, webhooks, source staleness, heartbeat | ✅ built |
| 1. Observe | Correlation into incidents, flap and maintenance flags, change calendar, triage in shadow mode, daily digest with feedback | ✅ built |
| 2. Investigate | Read-only investigation by a strong model, analysis by notification, postmortem drafts | ✅ built |
| 3. Approve and act | Action catalog, signed Go / No-go, executor, verification, rollback, runbooks | ✅ built, not yet proven in use |
| 4. UI and MCP | Console (incident explorer, incident, log, spend, actions, autonomy, postmortems), read-only MCP server, `/brief` for a daily brief | ✅ built |
| 5. Earned autonomy | Promotion proposals, `auto`, Undo, automatic demotion | ✅ built, not yet proven in use |
| 6. Release | Docs, threat model, a Prometheus and Docker quick start | ✅ written; the "stranger in under an hour" test is still to be run |
| 7. Configure in the browser | Roles, a local sign-in, sources and channels added in the console without a restart, a first run with no rules file | ✅ built |

"Built" means written and covered by tests. The exit criteria of the
[design](llm-incident-response-architecture.md#9-roadmap) are about use (ten approved fixes
without a manual cleanup, a month of `auto` with no bad action): those take a server and time.

## Limits as built

- Actions are for one Docker host, the Coolify instance and the deployment repository you
  configure. There is none on another host, on the network or on Kubernetes.
- `config_change` changes a value that already exists in a YAML file. It cannot add a key, edit
  another format, or open a pull request anywhere but GitHub (elsewhere the branch is pushed and
  the notification says so).
- A step is verified by the container's state, its health check, Coolify's own status and an
  optional URL. The alert's own query is not re-run by the executor; the incident resolving is
  what confirms the fix.
- `coolify_restart` is written from Coolify's published API description and has not been run
  against an instance. The trace export has been tested against a collector double, not against
  a real OTLP endpoint.
- The quick start was run end to end without a model key: the investigation step of it was not.
- Replay judges a triage verdict by itself; for an investigation it shows the old and new root
  cause and leaves the judgment to you.
- There is no retention period for tool results in the event log.
- Of the notification channels, ntfy, Gotify, e-mail and the webhook were run against real
  servers; the others were tested against each service's documented request, not the live
  service. A tap cannot be answered from inside a chat: a button
  opens the executor's page.
- Of the sources, the OpenObserve adapter was written against a real instance. Prometheus,
  Alertmanager's API, Grafana, Loki, VictoriaLogs, Gatus, Healthchecks, Netdata, Zabbix and
  Beszel are written from each service's documentation and tested against the payloads and
  answers it documents, not the live service. `paeon source <name>` is the first thing to run.
- A Prometheus without Alertmanager has no way in yet: its alerts are not polled. Netdata
  Cloud's notifications are not read. For logs in Loki and VictoriaLogs the model gives words
  to look for, not a query.
- In the console, an admin adds sources and notification channels. Models, the stages that
  call them, and everything about acting are configured in the rules file only. A source or a
  channel added in the console has been tested against mock services, not live ones.
- The console's own sign-in is one account. For several people, put a proxy in front and use
  roles. There is no TLS in Paeon.
- The console approves only with `actions.console_approval` in the executor's rules, and only a
  plan already waiting. Tier 2 is approved by merging on the forge; nothing riskier exists.
