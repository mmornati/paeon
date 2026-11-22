# Changelog

Paeon is pre-1.0: anything may change between versions. Dates are the day of the change on `main`.

## Unreleased

### Maintenance windows hold their incidents

A maintenance window used to be a flag on the incident: triage and the investigation saw it, and
a restart during the weekly update could still wake you up and pay for an investigation. On a
real Sunday run, two incidents opened by the authentik restart sent two notifications at 05:07
and cost one investigation each, for a service that was healthy a minute later.

- An incident that opens in a window is held until the window closes, plus `grace_minutes`
  (10 by default): no triage, no investigation, no notification.
- Then each alert still firing is re-checked with its source, as the console's Re-check does.
  An incident that is over closes as expected (`maintenance.released`, triage skipped as
  `maintenance`) and still shows in the digest. One that is not goes on as usual, and triage and
  the investigation get `after_maintenance` with what was still firing.
- The re-check also catches a recovery that uptime-kuma never sent: it does not notify
  PENDING to UP, which left incidents open after a restart.
- New keys on each window: `hold` (default `true`; `false` keeps the flag only) and
  `grace_minutes`. A rules file of 0.6.0 works unchanged, and holds by default.

## 0.6.0

The console, redesigned as an incident explorer: it stays readable with hundreds of incidents.

A rules file and an environment of 0.5.0 work unchanged. Links to `/ui`, `/ui/log`,
`/ui/ledger` and every incident page still open; the pages take new, optional query parameters.

### Incidents

- One dense table instead of a card per incident under stage sections. A search box (title,
  host or `#42`), a time range (24 h, 7 d, 30 d, all), status tabs with counts (All, Needs you,
  Open, Resolved), incidents opened per hour, day or week by severity, and filters with counts
  (severity, triage, investigation, host).
- Each row carries a five-square stage track: triage, investigation, plan, action, postmortem,
  each answered by a model, done by code, failed, waiting, skipped or off.
- Paged by the server, 50 at a time, by the position of the last row: an incident that opens
  while you read does not shift the next page. Every filter is in the URL, so a view can be
  bookmarked; the live refresh keeps it. `j` and `k` move through the rows, `/` searches.
- The store reads a page's events in one query, where the board read each incident's in its
  own every ten seconds.

### Incident page

- The header shows the five stages with what came of each, then the investigation's
  conclusion. Facts, alerts and the triage review sit in a side column on a desk.
- The record is folded until asked for; a cited tool call opens it at that call. The section
  tabs show where you are.

### Log, Spend and the rest

- Log: one event to a line on a desk, a search that matches what the line says after
  redaction (never the stored payload, so a secret cannot be found by guessing it), and one
  incident at a time.
- The ledger is called Spend: 7, 14 or 30 days, each day's cost stacked by stage on a labelled
  scale, and a word that costs are in the currency of your model prices.
- Postmortems: tabs for those to review and those reviewed. A reviewed postmortem no longer
  shows the notice that it is a draft.
- Actions: Stop automation asks for its confirmation behind an outlined button.

### Navigation and accessibility

- Destinations grouped as Respond, Review and Configure, a bar that says what each stage does,
  and on a phone a tab bar at the bottom with the rest under More. Incidents shows how many are
  open.
- Controls have a 3:1 edge in both schemes, muted text passes 4.5:1, table headers name their
  columns, the live status no longer interrupts a screen reader every ten seconds, and links
  get finger-sized targets on a touch screen.

## 0.5.0

What a person does from the console, and an analysis that says what becomes of its plan.

A rules file and an environment of 0.4.0 work unchanged. Go from the console is off until the
executor's own rules turn it on.

### Plans

- The analysis on your phone says what became of the plan: waiting for your Go, run by the
  policies, or recorded only, with what held it (`a policy sets it to observe`, the default
  level, the kill switch). It used to be sent before the executor had decided, so a recorded
  plan read like one waiting for a Go.
- `actions.console_approval`: an operator answers Go or No-go in the console, for a plan the
  executor already waits on. Read by the executor from its own copy of the rules file; the
  phone is told, and the record keeps who answered. Off by default; the threat model says what
  it costs.

### Incidents

- **Re-check now** asks each source that can answer what an alert is right now (OpenObserve's
  latest evaluation; Alertmanager and uptime-kuma through their tools when they have a `url`).
  An alert back to normal resolves the incident the usual way.
- **Close by hand**, with one line of why, for an incident whose source never sends the
  recovery. The record keeps who; an alert that fires again opens an incident again.
- An open incident whose alerts were not seen for two days is marked quiet on the board.

### Postmortems

- Reviewed from the console: mark reviewed (triage reads reviewed postmortems only), set back
  to draft, edit the whole file with its front matter checked, or have the model write a
  missing prose again. Each change is recorded and committed like the drafts.
- A model answer that is not valid JSON is asked again once, with what was wrong with it.
- The navigation counts the postmortems left to review.

## 0.4.0

Configure in the browser: an admin adds the sources alerts come from and the channels Paeon
sends to, in the console, without a restart, next to the rules file and never in it. A first
run needs no rules file at all, and the console has roles and, if you want, its own sign-in.

A rules file and an environment of 0.3.0 work unchanged: everything below is off by default.
One thing tightens if you turn it on: with `ui.roles`, rating triage and stopping automation
take the operator role.

### Sources and channels in the console

- `ui.settings`: an admin adds, tests, changes and removes **notification channels** (Channels
  page) and **sources** (Sources page), for every service Paeon has. They take effect at
  once: a channel carries the digest and the analysis from its next message; a source
  receives at `/ingest/<name>`, is polled if its service is, and offers its tools to the next
  investigation.
- They live in the database, their secrets encrypted with `PAEON_SECRET_KEY` and never shown
  again. Changing where a channel or a source sends asks for its secrets again.
- The rules file is never written. Its entries are shown locked, win on a name both define,
  and with `ui.settings` off the file is all there is.
- A channel added in the console never carries an approval: the executor sends those from its
  own rules file.
- The forms come from the same schema as the rules file, so a key added to a service appears
  in its form.

### A first run in the browser

- `PAEON_CONSOLE=local`, with no `PAEON_CONFIG`: the console is on, asks for a password and
  lets its admin add sources and channels. The incidents page lists what is left to do until
  it is done.
- The admin password is chosen in the browser the first time, with a code Paeon prints in its
  log at each start, or given as `PAEON_UI_ADMIN_PASSWORD`, which then takes over. Only a
  salted scrypt hash of a chosen one is kept.
- The quickstart now ends in the console instead of a rules file.

### Roles and sign-in

- `ui.roles`: viewer (reads), operator (rates triage, stops automation) and admin (sources and
  channels), from the groups header of the proxy. It needs `proxy_secret`. Without it,
  everyone the proxy lets in is an operator, as before.
- `ui.auth: local`: the console asks for one password itself, for a server with no
  authenticating proxy. Signed HttpOnly session cookie; wrong passwords pause sign-in, per
  address and from anywhere.
- The log records sign-ins and every change to a source or a channel, with who made it and
  without an address or a secret.

### Also

- New dependency: `cryptography`.
- New screenshots of the Sources and Channels pages, the setup page and the getting-started
  list in the docs, and a fourth video, "Set up in the browser".

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
