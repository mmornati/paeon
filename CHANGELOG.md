# Changelog

Paeon is pre-1.0: anything may change between versions. Dates are the day of the change on `main`.

## Unreleased

### Notifications

- Notification channels are named once under `notifiers` and listed by each stage in `notify`
  (`digest`, `investigation`, `actions`). A stage can send to several; one that fails does not
  keep the others from sending, and each delivery is journaled with its channel. The `ntfy:`
  block of each stage still works: it is short for one ntfy channel.
- A button is a label and a link, rendered by each channel as well as its service allows. The
  approval stays in the signed link, so every channel that can show a link can carry a Go.
- `notification.sent` and `notification.failed` entries carry `channel`, `name` and `to`.

## 0.1.1

Security fixes, and the release workflow.

### Security

From a review of the code and of a deployment behind Traefik and Authentik.

- `ui.proxy_secret`: the console can require a secret value that only the reverse proxy adds.
  `require_header` alone is satisfied by any container on the proxy's network.
- A request with a body is refused before the body is read when it has no token, or is larger
  than 2 MB (256 kB on the executor).
- Redaction no longer takes quadratic time on a text full of unclosed private keys, looks at a
  bounded part of a very long text, and masks more shapes: an empty user in a URL, any
  `Authorization` or `Cookie` header, `private_key`, `signing_key`, `access_key`, `passphrase`,
  GitLab and Google keys, a numeric password.
- A link whose signature holds a character outside ASCII is answered 410, not 500.
- Triage is no longer shown postmortem drafts, only reviewed ones; the investigation sees a
  draft marked `unreviewed_draft`.
- `git` no longer runs the signature program a repository's own configuration names.
- A warning at start for a budget on a model alias without a price: it would never be reached.
- The documentation said nothing acts on a triage verdict. It decides whether the incident is
  investigated; the threat model now says so.

### Releases

- A release is a tag `vX.Y.Z` on `main`: the workflow checks that the tag, the code and this
  file agree, then publishes the image under its version and the GitHub release.

## 0.1.0

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

### Documentation

- A public site on GitHub Pages: a landing page, the documentation (MkDocs, under `/docs/`) with
  a five-minute quickstart and a tour of the console, and three narrated videos drawn on a
  canvas. Built by `scripts/build-site.sh`, deployed by the `Documentation` workflow.

### Changed

- The console is redesigned as an incident management app: a sidebar (a tab row on a phone), the
  board as a pipeline count over incidents grouped by stage, a filter box (`/`), an incident page
  with a summary header and section tabs, cost figures and a chart of spend per day on the
  ledger, status badges throughout, and a light or dark switch. Same pages, same URLs, same
  policy: nothing is loaded from another host.
- A postmortem is shown rendered instead of as markdown source (new dependency:
  `markdown-it-py`). Raw HTML in the file is shown as text, and a link is kept only to an http(s)
  or mailto address or to another postmortem.
- A `mermaid` block of a postmortem is drawn as a diagram, by Mermaid 12.3.0 bundled in the
  package (5.5 MB, served from Paeon itself). A postmortem page that has a diagram is served
  with `style-src 'self' 'unsafe-inline'`, because Mermaid styles its drawing inline; every
  other page keeps the strict policy.
- Prompt versions are `triage-2` and `investigate-2`.
- The image contains `git` and the OpenSSH client, and exposes port 8081 as well.
- Triage now sends excerpts of matching runbooks and postmortems to its model. See "What leaves
  your server" in the README.

## 0.0.1

Phases 0 to 2: the event log, the OpenObserve and webhook sources, correlation, triage in shadow
mode, the digest, read-only investigation, postmortem drafts, and the first console.
