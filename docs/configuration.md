# Configuration

Paeon is configured in two places:

- **The rules file**, one YAML file named by `PAEON_CONFIG`: what Paeon does. Correlation,
  maintenance windows, sources, models, the stages, notification channels, actions, the console.
- **The environment**: where a process runs and every secret. The address it listens on, the
  database path, tokens and keys.

Start from [examples/paeon.yaml](https://github.com/mmornati/paeon/blob/main/examples/paeon.yaml)
and [.env.example](https://github.com/mmornati/paeon/blob/main/.env.example). Every key is
optional and **every stage is off until you enable it**: with an ingest token and nothing else,
Paeon records alerts and groups them into incidents.

| To configure | Read |
|---|---|
| Every key of the rules file, with its type and default | [Configuration reference](configuration-reference.md) |
| Where alerts come from | [Connect your alerts](connect-alerts.md) |
| Each monitoring service: its alerts and what an investigation may read | [Sources](sources.md) |
| Where Paeon sends, service by service | [Notifications](notifications.md) |
| Plans, approvals and the executor | [Acting](acting.md) |
| The console and its proxy | [The console](console.md) |

## How the file is read

- The file is read **once, when the process starts**. A change needs a restart of the process
  that reads it (`paeon serve`, and `paeon executor` for its own copy).
- It is checked as a whole before anything starts. A key that does not exist, a value of the
  wrong type, a model alias or a channel that is named and not defined, a source with a key
  its service does not have: each stops the start
  and says which key is wrong.
- Secrets never go in the file. A key ending in `_env` holds the **name** of an environment
  variable (`api_key_env`, `token_env`, `webhook_env`), and every secret also accepts
  `NAME_FILE=/path`, for Docker secrets.
- The executor reads the same format from **its own copy**, and trusts only that copy. See
  [Acting](acting.md).
- The file is never written by Paeon. With `ui.settings` on, an admin can add notification
  channels in the console; they are kept in the database and laid over the file, which wins
  on a name both define. Sources can be added the same way. See
  [The console](console.md#channels-and-sources-in-the-console).

## The sections of the file

| Section | What it holds | Reference |
|---|---|---|
| `correlation` | How alerts become incidents: windows, host labels, flapping. | [correlation](configuration-reference.md#correlation) |
| `maintenance` | Weekly windows in which alerts are expected. | [maintenance](configuration-reference.md#maintenance) |
| `sources` | Named monitoring services: their alerts, and what an investigation may read. | [sources](configuration-reference.md#sources) |
| `models` | Model aliases: backend, model name, key, price. A stage names an alias. | [models](configuration-reference.md#models) |
| `triage` | Stage 1: a cheap first look at each incident. | [triage](configuration-reference.md#triage) |
| `investigation` | Stage 2: a strong model with the read-only tools you allow. | [investigation](configuration-reference.md#investigation) |
| `postmortems` | Drafts written once an incident stays resolved. | [postmortems](configuration-reference.md#postmortems) |
| `digest` | The daily summary and its feedback questions. | [digest](configuration-reference.md#digest) |
| `notifiers` | Named notification channels, one per service. | [notifiers](configuration-reference.md#notifiers) |
| `actions` | Stages 3 and 4: the catalog, policies, runbooks, the executor. | [actions](configuration-reference.md#actions) |
| `ui` | The console. | [ui](configuration-reference.md#ui) |
| `telemetry` | Traces of Paeon's own work over OTLP. | [telemetry](configuration-reference.md#telemetry) |

## Stages

| Stage | What it does | Model |
|---|---|---|
| `triage` | One call per incident with a context pack built by code. Verdict: `noise`, `transient`, `known_issue` or `real`. Recorded and shown in the digest. It changes no notification; the one thing it decides is whether stage 2 investigates the incident (`investigation.verdicts`). Page-level alerts are never sent. | cheap |
| `investigation` | An agent loop with read-only tools and a budget. Hands in a summary, the impact, a root cause with a confidence, the evidence, the hypotheses it rejected and a plan. A second model can take over when the first fails or declines. | strong, with tool calls |
| `postmortems` | A markdown draft with YAML front matter once an incident that was investigated or acted on has stayed resolved. Timeline and automation review come from the event log; the model writes the prose; the file says `status: draft`. Optionally committed and pushed to a git repository. | strong |
| `actions` | No model. Plans from an analysis or from a runbook go to the executor. See [Acting](acting.md). | none |

The budgets (`daily_budget`, `budget_per_incident`), the storm guard (`max_per_hour`) and the limits on tool calls and time are set per stage in the same file. A spent budget never hides an alert: Paeon fails open and treats the incident as real.

## Sources

A monitoring service is defined once under `sources`, by a name of yours: Prometheus,
Alertmanager, Grafana, Loki, VictoriaLogs, OpenObserve, uptime-kuma, Gatus, Healthchecks,
Netdata, Zabbix or Beszel. A source that pushes its alerts posts them to `/ingest/<name>`, and
what an investigation may read in it is the allow-list of that source. See
[Sources](sources.md).

## Notifications

The digest, the analysis and the approvals go to the channels you name: ntfy, Telegram, Discord,
Slack, e-mail, Gotify, Pushover, Teams, Matrix, a webhook of your own, or anything Apprise
knows. Channels are defined once under `notifiers` and listed per stage in `notify`. See
[Notifications](notifications.md).

## Environment

Every secret below also accepts `NAME_FILE=/path`.

### The core (`paeon serve`)

| Variable | Default | |
|---|---|---|
| `PAEON_INGEST_TOKEN` | **required** | Bearer token the webhook senders and the API clients present. 24 characters or more. |
| `PAEON_CONFIG` | none | Path of the rules file. Without it, Paeon records and correlates with the defaults. |
| `PAEON_DB` | `paeon.sqlite` | Where the event log lives. |
| `PAEON_HOST` | `127.0.0.1` | The address the API listens on. `0.0.0.0` in a container. |
| `PAEON_PORT` | `8080` | Its port. |
| `PAEON_OPENOBSERVE_URL` | none | Switches on the OpenObserve pull adapter. The internal address, for example `http://openobserve:5080`. The same can be written in the rules file, as a [source](sources.md#openobserve). |
| `PAEON_OPENOBSERVE_ORG` | `default` | The organisation. |
| `PAEON_OPENOBSERVE_USER` | | The account. Required with the URL. |
| `PAEON_OPENOBSERVE_PASSWORD` | | Its password. Required with the URL. |
| `PAEON_OPENOBSERVE_POLL_SECONDS` | `60` | How often alerts are pulled. 5 at least. |
| `PAEON_OPENOBSERVE_STALE_SECONDS` | `900` | No evaluation row for this long opens the internal `source_stale` alert. 60 at least. |
| `PAEON_HEARTBEAT_URL` | none | A push URL called while Paeon is healthy (an uptime-kuma Push monitor). |
| `PAEON_HEARTBEAT_SECONDS` | `60` | How often it is called. 10 at least. |
| `PAEON_EXECUTOR_TOKEN` | | How the core proves itself to the executor. Required with `actions.enabled`, 24 characters or more, the same as the executor's. |
| `PAEON_UI_PROXY_SECRET` | | The value your proxy adds to each console request. Required with `ui.proxy_secret`, 24 characters or more. The name can be changed there. |
| `PAEON_CONSOLE` | none | `local`: with no `PAEON_CONFIG`, turns the console on with its own password and lets its admin add sources and channels. See [The console](console.md#with-no-rules-file-at-all). |
| `PAEON_UI_ADMIN_PASSWORD` | | The console's own password with `ui.auth: local`, 12 characters or more. Without it, the password is chosen in the browser with a code from the log; with it, it takes over the chosen one. |
| `PAEON_SECRET_KEY` | | Encrypts the secrets of channels added in the console and signs its sessions. Required with `ui.settings` or `ui.auth: local`, 32 characters or more. Changing it signs everyone out and sets those channels aside until they are saved again. |

### The executor (`paeon executor`)

| Variable | Default | |
|---|---|---|
| `PAEON_EXECUTOR_TOKEN` | **required** | The token the core presents. 24 characters or more. |
| `PAEON_CONFIG` | **required** | Path of the executor's own copy of the rules file. |
| `PAEON_SIGNING_KEY` | none | The key that signs Go / No-go links. 32 characters or more, different from the token. Without it nothing can be approved. Never give it to the core. |
| `PAEON_EXECUTOR_DB` | `executor.sqlite` | Where the executor's journal lives. |
| `PAEON_EXECUTOR_HOST` | `127.0.0.1` | The address it listens on. |
| `PAEON_EXECUTOR_PORT` | `8081` | Its port. |
| `PAEON_KILL_SWITCH` | off | `1`, `true`, `yes` or `on`: every plan is only recorded, whatever the policies say. |

### Variables the rules file names

These have no fixed name: a key of the rules file says which variable to read. The names below
are the ones the examples use.

| Named by | Example | Read by | Holds |
|---|---|---|---|
| `models.<alias>.api_key_env` | `ANTHROPIC_API_KEY` | core | A model provider's API key. |
| `notifiers.<name>.token_env`, `webhook_env`, `password_env`, `user_env`, `url_env`, `authorization_env`, `urls_env` | `PAEON_TELEGRAM_TOKEN` | the process that sends to the channel | A channel's secret. See [Notifications](notifications.md). |
| `sources.<name>.token_env`, `password_env`, `api_key_env` | `PAEON_GRAFANA_TOKEN` | core | A source's secret. See [Sources](sources.md). |
| `digest.ntfy.token_env`, `investigation.ntfy.token_env` | `PAEON_NTFY_TOKEN` | core | The ntfy token of a stage's own `ntfy:` block. |
| `actions.ntfy.token_env` | `PAEON_ACTIONS_NTFY_TOKEN` | executor | The ntfy token of the approval topic. |
| `investigation.tools.uptime_kuma.api_key_env` | `PAEON_UPTIME_KUMA_API_KEY` | core | An uptime-kuma API key, for `/metrics`. |
| `telemetry.authorization_env` | `PAEON_OTLP_AUTHORIZATION` | core | The whole `Authorization` header of the OTLP endpoint. |
| `actions.coolify.token_env` | `PAEON_COOLIFY_TOKEN` | executor | A Coolify API token that can deploy. |
| `actions.repository.github.token_env` | `PAEON_GITHUB_TOKEN` | executor | A token that can open pull requests on the deployment repository. |
| `ui.proxy_secret.value_env` | `PAEON_UI_PROXY_SECRET` | core | The value the proxy adds to console requests. |

## Traces

```yaml
telemetry:
  otlp_endpoint: http://openobserve:5080/api/default
  authorization_env: PAEON_OTLP_AUTHORIZATION     # "Basic <base64 of user:password>"
```

Each incident becomes an OpenTelemetry trace: a span for the incident and one per step, with its
duration, model, token counts, cost and outcome. Spans carry facts about a step and never its
content: no prompt, no answer, no tool input or result.
