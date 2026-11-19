# Changelog

Paeon is pre-1.0: anything may change between versions. Dates are the day of the change on `main`.

## Unreleased

Channels can be added in the console, by an admin, next to those of the rules file.

A rules file and an environment of 0.3.0 work unchanged: everything below is off by default.

### Console

- `ui.settings`: an admin adds, changes, tests and removes notification channels on the
  Channels page. They take effect at once, are kept in the database with their secrets
  encrypted (`PAEON_SECRET_KEY`), and carry the digest and the analysis, never an approval.
  The rules file is never written, wins on a name both define, and its channels are shown
  locked. With `ui.settings` off, the file is all there is.
- With `ui.settings`, an admin also adds sources on the Sources page. A source is connected
  without a restart: it receives at `/ingest/<name>`, is polled if its service is, and offers
  its tools to the next investigation. The same rules hold as for channels: the file wins on
  a name, secrets are encrypted, and a change of server asks for them again.
- `PAEON_CONSOLE=local`: a start with no rules file. The console is on, asks for a password
  and lets its admin add sources and channels; the incidents page lists what is left to do.
- With `ui.auth: local` and no `PAEON_UI_ADMIN_PASSWORD`, the password is chosen in the
  browser the first time, with a code printed in the log. The quickstart now ends in the
  console instead of a rules file.
- `ui.roles`: viewer, operator and admin, from the groups header of the proxy. Without it,
  everyone the proxy lets in is an operator, as before.
- `ui.auth: local`: the console asks for one password itself (`PAEON_UI_ADMIN_PASSWORD`), for
  a deployment with no authenticating proxy.
- The log records sign-ins and every change to a channel, with who made it.
- New dependency: `cryptography`.

## 0.3.0

Sources: twelve monitoring services named in the rules file, for their alerts and for
read-only tools, and a configuration reference generated from the schema.

A rules file and an environment of 0.2.0 work unchanged: nothing has to be migrated.

### Sources

- Monitoring services are named once under `sources`, each a `type` with its own keys, the way
  notification channels are. A source can receive alerts at `/ingest/<name>`, be polled, and
  give an investigation read-only tools. None adds a dependency.
- Ten more services: `prometheus` (also VictoriaMetrics, Mimir and Thanos: PromQL on an
  allow-list of metrics, alerting rules, scrape targets), `alertmanager` (active alerts and
  silences, next to its webhook), `grafana` (webhook contact point, annotations, alert rules),
  `loki` and `victorialogs` (log lines of the streams you name), `gatus`, `healthchecks`,
  `netdata` and `zabbix` (a webhook in, their state read), and `beszel` (polled).
- `openobserve` and `uptime_kuma` are source types too, for a second instance or to keep the
  whole configuration in the rules file. `/ingest/alertmanager`, `/ingest/uptime-kuma` and the
  `PAEON_OPENOBSERVE_*` variables work as before, with no entry.
- A tool of a source carries its name (`query_metrics_prom`, `search_logs_logs`).
- Sources are read through one client that asks only the paths its adapter lists, follows no
  redirect and reports no URL. For logs the model gives words, not a query. A `url` that holds
  a password is refused.
- `/ingest/<name>` also takes a form, for senders that are a shell script (Netdata).
- `paeon source <name>` says what a source gives and asks its service one question.
- The console has a **Sources** page: how each source's alerts arrive, its tools, what it
  fired, and whether a polled one is stale.
- A [Sources guide](docs/sources.md) covers each service in full, and its key tables and
  examples are checked against the schema.
- The new adapters are written from each service's documentation and tested against the
  payloads and answers it documents. None has met a live server yet.

### Documentation

- A configuration reference (`docs/configuration-reference.md`) with every key of the rules
  file, its type, its default and what it does. It is generated from the schema
  (`scripts/gen_config_reference.py`), and a test fails when it is behind.
- Every key of the rules file has a description in the schema.
- The Notifications guide covers each service in full: what to prepare, every key, what
  arrives, and what a failure means.
- The Configuration guide lists the sections of the file and every environment variable of the
  core and of the executor.
- `paeon notify <channel>` sends a test message through one channel and says what the service
  answered.

## 0.2.0

Notification channels: ten services and Apprise, each stage sending to the ones you name.

### Notifications

- Notification channels are named once under `notifiers` and listed by each stage in `notify`
  (`digest`, `investigation`, `actions`). A stage can send to several; one that fails does not
  keep the others from sending, and each delivery is journaled with its channel. The `ntfy:`
  block of each stage still works: it is short for one ntfy channel.
- A button is a label and a link, rendered by each channel as well as its service allows. The
  approval stays in the signed link, so every channel that can show a link can carry a Go.
- `notification.sent` and `notification.failed` entries carry `channel`, `name` and `to`.
- Five more services, each a `type` under `notifiers`, none with a new dependency:
  `telegram` (a bot; the buttons are a keyboard of links), `discord` (a webhook; link buttons),
  `slack` (an incoming webhook; links in the text), `email` (SMTP; links in the body) and
  `webhook` (the message as JSON, for a receiver of your own).
- A channel's secret (a bot token, a webhook URL, a password) is named by its `*_env` key and
  read only by the process that sends to it. A listed channel without its secret stops the
  start; an error of a channel names the service and the status, never the URL.
- Four services without buttons of their own, where the links are part of the message:
  `gotify`, `pushover` (never its repeating emergency priority), `teams` (a card on a Workflows
  webhook, with buttons that open the link) and `matrix`.
- `apprise`, for every other service: an optional install (`pip install 'paeon[apprise]'`),
  imported only when a channel of that type is configured. The image does not include it.
- A **Channels** page in the console: every channel a stage sends to, what it carries, and its
  deliveries of the last 30 days. Never an address or a secret.
- A Notifications guide (`docs/notifications.md`) with each service's keys and setup.
- Text from an alert is never read as markup by a service: no parse mode on Telegram, markdown
  switched off on Discord, escaped on Slack, in HTML mail, on Pushover and on Matrix, and text
  runs on Teams.

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
