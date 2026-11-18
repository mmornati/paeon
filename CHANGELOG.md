# Changelog

Paeon is pre-1.0: anything may change between versions. Dates are the day of the change on `main`.

## 0.1.0 (unreleased)

The first version that covers the whole design: record, observe, investigate, approve and act,
earn autonomy.

### Acting (new, off by default)

- Six actions in the closed catalog. Besides the three on a container's state:
  `reattach_network_alias`, `coolify_restart`, and `config_change`, which is never applied: one
  existing value of one allow-listed YAML file becomes a branch and a pull request on the
  deployment repository, and merging it is the approval.
- The executor acts through one Docker proxy and reads through another
  (`actions.docker_read_url`), so that the one it acts through allows nothing but the actions.

- A second process, `paeon executor`, with its own database, credentials and port. The core
  proposes plans; the executor checks them against its own rules and decides.
- A closed catalog of three container actions (`restart_container`, `start_container`,
  `unpause_container`), switched on per container in `actions.catalog`.
- Four autonomy levels per (alert, action, container) in `actions.policies`: `observe`,
  `suggest`, `approve`, `auto`.
- Go / No-go as signed, single-use, expiring links bound to the plan, sent by the executor to
  ntfy. A GET shows a confirmation page and never applies.
- Every tap is answered with a short notification: what was received, or why the button did
  nothing. The executor's access log no longer holds the links, nor a line per poll.
- `actions.buttons: page` makes a button open the executor's page (what the link does, Confirm,
  then the outcome) instead of sending the link in the background, for phones whose push is
  slow: the answer is on screen.
- Execution with a lock per container, a pre-check, verification, rollback where an action has
  an inverse, and one automated attempt per incident.
- Runbooks: a known alert and a ladder of steps, with no model call. A runbook that fails hands
  the incident to the investigator, and so does one whose plan is only recorded (`observe`,
  `suggest`): a runbook can be tried in the record without the alert going unlooked at.
- Earned autonomy: track records, promotion proposals as a policy line, automatic demotion on a
  failed verification, an Undo or a "This was wrong".
- A kill switch: notification button, console, `paeon stop`, `PAEON_KILL_SWITCH`.

### Replay and traces

- `paeon replay triage|investigation`: recorded incidents against the model and prompts of
  today, compared with the recorded outcome and with your feedback. Exits 1 on a regression.
- `telemetry.otlp_endpoint`: one OpenTelemetry trace per incident, without any content.

### Investigation

- New read-only tools: `alert_query` (an OpenObserve alert's definition, and its query re-run
  when the data it reads is allow-listed), `alert_history`, `monitor_status` (uptime-kuma),
  `recent_commits` (git), `host_command` (commands you prepared on other hosts, over SSH).
- `investigation.fallback_model`: a second model takes over when the first fails or declines.
- A plan step may name a catalog action and a container; it is checked before it is accepted.
- Knowledge search is a full-text index (SQLite FTS5) instead of a scan.
- OpenObserve alerts are recorded with their description, severity and query.

### Triage and postmortems

- Triage is given the past postmortems and runbooks that match the alert.
- After the digest, one short message per triage verdict with Right / Wrong buttons
  (`digest.feedback`).
- Postmortem drafts can be committed and pushed to a git repository (`postmortems.git`), and are
  written for incidents Paeon acted on as well as those it investigated.

### Console, API, MCP

- Console pages: Actions, Autonomy, Postmortems; the plan and its outcome on the incident page;
  a bay for plans waiting for your answer.
- `GET /postmortems`, `GET /autonomy`, `GET /brief`.
- `paeon mcp`: a read-only MCP server over the record, on stdio.
- Notifications link to the console (`ui.public_url`); the analysis carries "cause is right /
  wrong" buttons when the executor is there to sign them.

### Changed

- Prompt versions are `triage-2` and `investigate-2`.
- The image contains `git` and the OpenSSH client, and exposes port 8081 as well.
- Triage now sends excerpts of matching runbooks and postmortems to its model. See "What leaves
  your server" in the README.

## 0.0.1

Phases 0 to 2: the event log, the OpenObserve and webhook sources, correlation, triage in shadow
mode, the digest, read-only investigation, postmortem drafts, and the first console.
