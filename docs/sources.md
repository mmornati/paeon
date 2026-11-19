# Sources

A **source** is one monitoring service. It can give Paeon two things:

- its **alerts**, pushed to Paeon by a webhook or polled by Paeon;
- **read-only tools** for an [investigation](how-it-works.md): metrics, logs, rule definitions,
  monitor states.

Paeon sits next to your alerting, never in its path: your existing notifications stay as they
are, Paeon gets a copy, and nothing it does changes anything in a source.

| Service | `type` | Alerts arrive | An investigation can read | Section |
|---|---|---|---|---|
| Prometheus, VictoriaMetrics, Mimir, Thanos | `prometheus` | through Alertmanager | metrics (PromQL), alerting rules, scrape targets | [Prometheus](#prometheus) |
| Alertmanager | `alertmanager` | webhook | active alerts, silences | [Alertmanager](#alertmanager) |
| Grafana | `grafana` | webhook | annotations, alert rules | [Grafana](#grafana) |
| Grafana Loki | `loki` | through Alertmanager | log lines | [Loki](#loki) |
| VictoriaLogs | `victorialogs` | through Alertmanager | log entries | [VictoriaLogs](#victorialogs) |
| OpenObserve | `openobserve` | polled | logs, metrics, alert definitions | [OpenObserve](#openobserve) |
| uptime-kuma | `uptime_kuma` | webhook | monitor states | [uptime-kuma](#uptime-kuma) |
| Gatus | `gatus` | webhook | endpoint statuses | [Gatus](#gatus) |
| Healthchecks | `healthchecks` | webhook | checks and their flips | [Healthchecks](#healthchecks) |
| Netdata | `netdata` | webhook | active alerts, chart data | [Netdata](#netdata) |
| Zabbix | `zabbix` | webhook | problems, items, history | [Zabbix](#zabbix) |
| Beszel | `beszel` | polled | systems, statistics, containers | [Beszel](#beszel) |

Anything else that can send the Alertmanager webhook (vmalert through Alertmanager, SigNoz,
Karma, many others) can post to an `alertmanager` source.

## Set up a source

1. **Define it** under `sources`, with a name of yours and the keys of its service (below).
2. **Give it its secret**, if it has one, in the environment of `paeon serve`, under the
   variable the source names (`token_env`, `password_env`, `api_key_env`). `NAME_FILE=/path`
   works too, for Docker secrets.
3. **Point the service at Paeon**, if it pushes: the address is `/ingest/<name>`, with the
   ingest token as a bearer. Each section says where that goes in the service.
4. **Restart** Paeon: the rules file is read at start.
5. **Try it**: `paeon source <name>` says what the source gives and asks its service one
   question.

```yaml
sources:
  prom:   { type: prometheus, url: http://prometheus:9090, metrics: ["node_*", "container_*"] }
  logs:   { type: loki, url: http://loki:3100, streams: { api: '{container="api"}' } }
  gatus:  { type: gatus, url: http://gatus:8080 }
```

```bash
docker exec paeon paeon source prom
# prom: prometheus
#   tools for an investigation: query_metrics_prom, alert_rules_prom, scrape_targets_prom
#   answered: answered a query
```

The rules that hold for every source:

- A name is lower-case letters, digits, `-` and `_`, 32 characters at most. `change` and
  `paeon` are taken.
- A source without a `url` only receives. A source with one also reads; the ones that only
  read (`prometheus`, `loki`, `victorialogs`) need it.
- A secret never goes in the rules file, and not in a `url` either: `http://user:pass@host` is
  refused. A source whose secret is named and missing stops the start, with the name of the
  variable.
- `user` with `password_env` is basic authentication, `token_env` is a bearer token. Use them
  for the service's own authentication or for a reverse proxy in front of it. Keep one of the
  two.
- A key that does not exist for the service is refused when the file is loaded.
- The tools are offered to an investigation only (`investigation.enabled`). Triage never reads
  a source.

!!! note "Three sources need no entry"

    `/ingest/alertmanager` and `/ingest/uptime-kuma` answer without anything under `sources`,
    and the `PAEON_OPENOBSERVE_*` variables still switch on the OpenObserve poller, named
    `openobserve`. Nothing has to change in a deployment made before sources had names. Add an
    entry with one of those names only to give it a `url`, and with it the tools.

The console's **Sources** page lists every source, how its alerts arrive, the tools it offers
and what came from it in the last 30 days.

## What a source gives

**Alerts.** Whatever the service sends is turned into Paeon's own alert: a source, a stable
identity, firing or resolved, a severity, a title, labels. From there every source is the same:
alerts are [correlated into incidents](how-it-works.md), and a rule that matches on `source`
uses the name you gave it.

- A pushed alert is recorded as it came (`webhook.received`), then as a state change
  (`alert.firing`, `alert.resolved`). A repeat of the same state is not a new event.
- A polled source that stops answering opens the internal `source_stale` alert after its
  `stale_seconds`, so a silent source is not mistaken for a quiet day.

**Tools.** Each tool carries the name of its source: `query_metrics_prom`, `search_logs_logs`.
Two Prometheus servers are two tools, and the model sees which one it asks. Every call and its
result is recorded in the event log, bounded in size and scrubbed of secret-shaped strings,
like every other tool.

## Reading without writing

A source's API is given to a model, so what can be asked is fixed in code:

- **The paths are fixed.** Each adapter lists the paths it reads, and nothing else can be
  requested: the model never writes a URL, a path or a method. Redirects are not followed.
- **Reads are GETs.** Two services take a read as a POST: Zabbix (JSON-RPC, where the list is
  of methods, all of them `get`) and Beszel (the sign-in, once). Both bodies are written by
  the adapter.
- **Allow-lists decide what is readable.** Metric names (`metrics`), log streams (`streams`),
  charts (`charts`), hosts (`hosts`). An empty list reads nothing, and the tool is not offered.
- **For logs, the model gives words, not a query.** You write the stream's selector; the model
  picks a stream by name and lists strings to look for. They are sent as quoted strings, so
  they cannot widen the query.
- **An error never carries a URL.** It says the service and the status.

The allow-lists are the second net. The first is the credential: give each source an account
that can only read. Each section says how.

## The services

No source adds a dependency. In each table, a key in bold is required.

### Prometheus

Anything that answers the Prometheus HTTP API: Prometheus itself, VictoriaMetrics, Grafana
Mimir, Thanos. It is read; its alerts reach Paeon through [Alertmanager](#alertmanager).

**Before you start.** Nothing to change on the server. Know how it is protected:

- Prometheus has no accounts of its own. If you put basic authentication in its web
  configuration or a reverse proxy in front, use `user` and `password_env`.
- VictoriaMetrics: `-httpAuth.username` and `-httpAuth.password`, or `vmauth` in front.
- Mimir: put the prefix in `url` (`http://mimir:8080/prometheus`) and the tenant in `headers`
  (`X-Scope-OrgID`). Thanos takes `THANOS-TENANT`.

| Key | Default | |
|---|---|---|
| **`url`** | | The server, with its path prefix if it has one. |
| `metrics` | none | Metric name patterns PromQL may read (`node_*`, `up`). Empty: no query tool. |
| `rules` | `true` | Offer the alerting rules. |
| `targets` | `true` | Offer the health of the scrape targets. |
| `headers` | none | Headers added to every request, for a tenant. Not for secrets. |
| `user` | | User for basic authentication, with `password_env`. |
| `password_env` | | Variable with that user's password. |
| `token_env` | | Variable with a bearer token. |

```yaml
prom: { type: prometheus, url: http://prometheus:9090, metrics: ["node_*", "container_*", "up"] }
```

**What an investigation gets.**

- `query_metrics_<name>`: a PromQL range query over up to seven days. Every metric name in the
  query must match a pattern of `metrics`, or the query is refused before it is sent.
- `alert_rules_<name>`: each alerting rule's query, its `for`, its state, whether it evaluates,
  and the alerts it holds.
- `scrape_targets_<name>`: which targets are up, and the last error of those that are not. The
  scrape URL is left out: it can carry a credential.

The step of a query is raised when the range would be more than 10000 points, under the 11000
Prometheus accepts. Turn `rules` or `targets` off where the server does not answer them: a
Thanos querier, or a VictoriaMetrics single node without `-vmalert.proxyURL`.

### Alertmanager

The Alertmanager webhook brings alerts in. With a `url`, its API also says what is active and
what is silenced: the usual answer to "it fired, why did nobody hear it?".

**Before you start.** Add Paeon as a receiver, on a route that keeps going to your own:

```yaml
route:
  routes:
    - receiver: paeon
      continue: true
receivers:
  - name: paeon
    webhook_configs:
      - url: http://paeon:8080/ingest/alertmanager
        send_resolved: true
        http_config:
          authorization: { type: Bearer, credentials: "<PAEON_INGEST_TOKEN>" }
```

| Key | Default | |
|---|---|---|
| `url` | | The server, as Paeon reaches it. Without it, the source only receives. |
| `user` | | User for basic authentication, with `password_env`. |
| `password_env` | | Variable with that user's password. |
| `token_env` | | Variable with a bearer token. |

```yaml
alertmanager: { type: alertmanager, url: http://alertmanager:9093 }
```

**What arrives.** One alert per entry of the webhook, identified by its `fingerprint`. The
severity is the `severity` label (`critical`, `page` and `error` are critical; `warning` and
`info` are themselves), the title is `alertname`, and the link is the `generatorURL`.

**What an investigation gets.** `active_alerts_<name>` (each alert with its state: active, or
suppressed and by which silence or which other alert) and `silences_<name>` (what each
matches, until when, who created it and why).

The calls that create or delete a silence are not among the paths this source reads.

### Grafana

Grafana's alerting sends to a webhook contact point. With a `url` and a service account, the
annotations and the alert rules are read.

**Before you start.**

1. **Alerting**, **Contact points**, **Add contact point**, integration **Webhook**. URL:
   `http://paeon:8080/ingest/grafana` (the name of your source). Under **Optional Webhook
   settings**, set **Authorization Header - Scheme** to `Bearer` and **Credentials** to the
   ingest token. Leave the payload as it is: a custom payload is not read.
2. Add the contact point to a **notification policy**, next to your own.
3. For the tools: **Administration**, **Users and access**, **Service accounts**, **Add
   service account** with the role **Viewer**, then **Add service account token**. That token
   is `token_env`.

| Key | Default | |
|---|---|---|
| `url` | | The server, as Paeon reaches it. Without it, the source only receives. |
| `annotations` | `true` | Offer the annotations. |
| `rules` | `true` | Offer the alert rules. |
| `token_env` | | Variable with the service account's token. |
| `user` | | User for basic authentication, with `password_env`. |
| `password_env` | | Variable with that user's password. |

```yaml
grafana: { type: grafana, url: http://grafana:3000, token_env: PAEON_GRAFANA_TOKEN }
```

**What arrives.** The Alertmanager payload with Grafana's additions. The `values` that made
the rule fire are added to the description, and the link is the rule's page, or its panel or
dashboard when the rule has no page.

**What an investigation gets.** `annotations_<name>` (each state change of a rule, from which
state to which, and the marks people or deploy scripts left on dashboards) and
`alert_rules_<name>` (each rule's queries, condition, `for` and no-data behaviour).

Whether a Viewer may read the alert rules depends on the Grafana version and its access
control. When it may not, the tool answers that the service account may not read them: give
the account that permission, or set `rules: false`. Queries are not run through Grafana: read
the data from its own source ([Prometheus](#prometheus), [Loki](#loki)).

### Loki

Log lines from Grafana Loki. Loki's alerts come from its ruler, through
[Alertmanager](#alertmanager).

**Before you start.** Loki has no accounts: use what protects it (a gateway's basic
authentication in `user` and `password_env`) and, with `auth_enabled: true`, the tenant in
`headers` as `X-Scope-OrgID`. Then choose the streams. Each is a name of yours and the LogQL
selector you write for it; leave out anything private.

| Key | Default | |
|---|---|---|
| **`url`** | | The server, for example `http://loki:3100`. |
| `streams` | none | Name the model uses, to the stream selector you wrote. Nothing else is readable. |
| `max_lines` | `100` | Most lines one search returns. 1000 at most. |
| `headers` | none | Headers added to every request, for a tenant. Not for secrets. |
| `user` | | User for basic authentication, with `password_env`. |
| `password_env` | | Variable with that user's password. |
| `token_env` | | Variable with a bearer token. |

```yaml
logs:
  type: loki
  url: http://loki:3100
  headers: { X-Scope-OrgID: home }
  streams:
    api: '{container="api"}'
    proxy: '{job="caddy"}'
```

**What an investigation gets.** `search_logs_<name>`: the newest lines of one stream over up
to seven days. The model names a stream and may list strings a line must contain and strings
it must not. They are matched as written, case included. Each line comes with the labels of
its stream and is cut at 2000 characters.

The model cannot write LogQL: no regular expression, no parser stage, no metric query. That is
the price of a query that cannot leave its stream.

### VictoriaLogs

Log entries from VictoriaLogs. It has no alerting of its own: vmalert evaluates the rules and
[Alertmanager](#alertmanager) sends them.

**Before you start.** `-httpAuth.username` and `-httpAuth.password` if you set them, or
`vmauth` in front. A tenant goes in `headers` (`AccountID`, `ProjectID`). Then choose the
streams: a name of yours and the LogsQL filter you write for it, without a pipe.

| Key | Default | |
|---|---|---|
| **`url`** | | The server, for example `http://vlogs:9428`. |
| `streams` | none | Name the model uses, to the LogsQL filter you wrote. Nothing else is readable. |
| `max_lines` | `100` | Most entries one search returns. 1000 at most. |
| `headers` | none | Headers added to every request, for a tenant. Not for secrets. |
| `user` | | User for basic authentication, with `password_env`. |
| `password_env` | | Variable with that user's password. |
| `token_env` | | Variable with a bearer token. |

```yaml
vlogs:
  type: victorialogs
  url: http://vlogs:9428
  streams:
    api: '{app="api"}'
```

**What an investigation gets.** `search_logs_<name>`: the newest entries of one stream over up
to seven days, each with its time, its message, its stream and its other fields. The model
names a stream and may list words or phrases an entry must contain and those it must not.

A start time and a limit are always sent: without a start, VictoriaLogs scans everything it
keeps.

### OpenObserve

An OpenObserve organization: Paeon polls its alert evaluations and reads its logs and
metrics. This is the `PAEON_OPENOBSERVE_*` adapter under a name of yours; use it for a second
organization, or to keep the whole configuration in the rules file.

**Before you start.** Create a user that can read the `triggers` stream and the streams you
list. Paeon never writes to OpenObserve and nothing changes in your alerts.

| Key | Default | |
|---|---|---|
| **`url`** | | The server, for example `http://openobserve:5080`. |
| `org` | `default` | The organization. |
| `user` | | The account. Needed: OpenObserve has no open API. |
| `password_env` | | Variable with its password. Needed. |
| `alerts` | `true` | Poll the `triggers` stream for alert evaluations. |
| `poll_seconds` | `60` | How often the alerts are polled. 5 at least. |
| `stale_seconds` | `900` | Without data for this long, the source is reported stale. |
| `streams` | none | Log streams that may be searched. Nothing else is readable. |
| `metrics` | none | Metric name patterns PromQL may read. |
| `max_rows` | `100` | Most rows one search returns. 1000 at most. |
| `token_env` | | Not used by OpenObserve. |

```yaml
oo-home:
  type: openobserve
  url: http://openobserve:5080
  org: home
  user: paeon@example.org
  password_env: PAEON_OPENOBSERVE_HOME_PASSWORD
  streams: [docker, traefik]
  metrics: ["container_*"]
```

**What arrives.** One alert per OpenObserve alert, firing while its evaluations say `firing`
(or `notify_failed`: the case where nobody else was told) and resolved when they say `normal`.
The alert's description, severity and query are read from its definition.

**What an investigation gets.** `search_logs_<name>`, `query_metrics_<name>` and
`alert_query_<name>` (what an alert watches and, when the data is on the allow-list, its query
run again over a wider window).

Do not set both `PAEON_OPENOBSERVE_URL` and a source named `openobserve`: the start is refused.

### uptime-kuma

uptime-kuma's Webhook notification brings monitors that go down and up. With a `url` and an
API key, the state of every monitor is read from `/metrics`.

**Before you start.**

1. **Settings**, **Notifications**, **Setup Notification**, type **Webhook**. Post URL
   `http://paeon:8080/ingest/uptime-kuma`, request body **Preset - application/json**, and
   under **Additional Headers**: `{"Authorization": "Bearer <PAEON_INGEST_TOKEN>"}`. Apply it
   to every monitor.
2. For the tool: **Settings**, **API Keys**, **Add API Key**. That key is `api_key_env`.

| Key | Default | |
|---|---|---|
| `url` | | The server, as Paeon reaches it. Without it, the source only receives. |
| `api_key_env` | | Variable with an API key, for `/metrics`. |
| `user` | | User for basic authentication, with `password_env`. |
| `password_env` | | Variable with that user's password. |
| `token_env` | | Variable with a bearer token, for a proxy in front. |

```yaml
uptime-kuma: { type: uptime_kuma, url: http://uptime-kuma:3001, api_key_env: PAEON_UPTIME_KUMA_API_KEY }
```

**What arrives.** A monitor that goes down is a critical alert, identified by the monitor's
id; up resolves it. "Pending", "maintenance" and the test message say nothing and are only
recorded.

**What an investigation gets.** `monitor_status_<name>`: every monitor's state now, its last
response time and the days left on its certificate. The monitored URL is left out.

uptime-kuma 2 has no API for a monitor's history: Paeon's own record of the webhook is where
`alert_history` reads it. Version 3 turns `/metrics` off unless you enable it in its settings.

### Gatus

Gatus sends a custom alert when an endpoint fails and when it recovers. With a `url`, the
endpoint statuses are read.

**Before you start.** Gatus has no webhook of a fixed shape: the `custom` provider sends the
body you write. Use this one, and enable the alert on each endpoint:

```yaml
alerting:
  custom:
    url: "http://paeon:8080/ingest/gatus"
    method: "POST"
    headers:
      Authorization: "Bearer <PAEON_INGEST_TOKEN>"
      Content-Type: "application/json"
    body: |
      {"status": "[ALERT_TRIGGERED_OR_RESOLVED]", "name": "[ENDPOINT_NAME]", "group": "[ENDPOINT_GROUP]", "description": "[ALERT_DESCRIPTION]"}
    default-alert:
      enabled: true
      send-on-resolved: true

endpoints:
  - name: blog
    url: "https://blog.example.org"
    conditions: ["[STATUS] == 200"]
    alerts: [{ type: custom }]
```

| Key | Default | |
|---|---|---|
| `url` | | The server, as Paeon reaches it. Without it, the source only receives. |
| `user` | | User of Gatus's basic `security`, with `password_env`. |
| `password_env` | | Variable with that user's password. |
| `token_env` | | Variable with a bearer token, for a proxy in front. |

```yaml
gatus: { type: gatus, url: http://gatus:8080 }
```

**What arrives.** `TRIGGERED` is a critical alert named `<group>/<name>`, `RESOLVED` resolves
it. Without `send-on-resolved: true` Gatus never says an endpoint recovered, and the alert
stays open.

**What an investigation gets.** `endpoint_status_<name>`: whether each endpoint is healthy
now, its last response time and status code, which conditions failed, how many of its recent
checks failed, and when it last changed state.

Gatus puts each placeholder in the body as it is. A description or a name that holds a double
quote makes the body invalid, and Paeon answers 422: keep quotes out of them.

### Healthchecks

[Healthchecks](https://healthchecks.io) watches things that must report in: cron jobs,
backups. Its webhook integration brings the checks that go down and up; a read-only API key
reads their state.

**Before you start.**

1. In the project: **Integrations**, **Add Integration**, **Webhook**. For **both** "Execute
   when a check goes down" and "Execute when a check goes up": method `POST`, URL
   `http://paeon:8080/ingest/healthchecks`, and
   - request body: `{"status": "$STATUS", "name": $NAME_JSON, "slug": "$SLUG", "tags": "$TAGS"}`
   - request headers: `Authorization: Bearer <PAEON_INGEST_TOKEN>` and
     `Content-Type: application/json`, one per line.
2. Give each check a name: it is how Paeon tells them apart.
3. For the tools: **Project Settings**, **API Access**, and copy the **read-only** key. That
   key is `api_key_env`.

The hosted healthchecks.io calls from the internet: Paeon's `/ingest` address must be
reachable from there. A Healthchecks of your own calls from your network.

| Key | Default | |
|---|---|---|
| `url` | | The server: `https://healthchecks.io` if hosted. Without it, the source only receives. |
| `api_key_env` | | Variable with the project's read-only API key. |
| `user` | | User for basic authentication, with `password_env`. |
| `password_env` | | Variable with that user's password. |
| `token_env` | | Variable with a bearer token, for a proxy in front. |

```yaml
healthchecks: { type: healthchecks, url: https://healthchecks.io, api_key_env: PAEON_HEALTHCHECKS_KEY }
```

**What arrives.** `down` is a warning named after the check, identified by its slug (or its
name, without one); `up` resolves it. The body above leaves out `$CODE`: a check's UUID is
also the secret of its ping URL.

**What an investigation gets.** `check_status_<name>` (each check's status, its last and next
expected ping, its schedule) and `check_flips_<name>` (when one check went down and came back,
over the last hours).

With a read-only key, Healthchecks itself leaves ping URLs out of its answers, and Paeon drops
them from what a read-write key returns. The log of pings is not read: it needs a read-write
key.

### Netdata

A Netdata agent calls a script of yours on every alert transition. With a `url`, its active
alerts and the charts you list are read.

**Before you start.** On the agent's host, edit the notification configuration
(`sudo ./edit-config health_alarm_notify.conf` in Netdata's configuration directory) and add:

```bash
SEND_CUSTOM="YES"
DEFAULT_RECIPIENT_CUSTOM="paeon"

custom_sender() {
  curl --silent --show-error --max-time 10 \
    -H "Authorization: Bearer <PAEON_INGEST_TOKEN>" \
    --data-urlencode "status=${status}" \
    --data-urlencode "name=${name}" \
    --data-urlencode "chart=${chart}" \
    --data-urlencode "host=${host}" \
    --data-urlencode "value=${value_string}" \
    --data-urlencode "info=${info}" \
    --data-urlencode "url=${goto_url}" \
    "http://paeon:8080/ingest/netdata"
}
```

It posts a form, so no value has to be quoted by hand. For the tools, the agent has no token
to give: it limits by address. Allow Paeon's address in `netdata.conf` (`[web]`,
`allow dashboard from`), or put a proxy in front and use `user` and `password_env`.

| Key | Default | |
|---|---|---|
| `url` | | The agent, for example `http://netdata:19999`. Without it, the source only receives. |
| `charts` | none | Chart id patterns whose data may be read (`system.*`, `disk_space.*`). Empty: no data tool. |
| `user` | | User for basic authentication, with `password_env`. |
| `password_env` | | Variable with that user's password. |
| `token_env` | | Variable with a bearer token, for a proxy in front. |

```yaml
netdata: { type: netdata, url: http://netdata:19999, charts: ["system.*", "disk_space.*", "mem.*"] }
```

**What arrives.** `CRITICAL` and `WARNING` are firing, with that severity; `CLEAR` resolves.
The other statuses (`UNDEFINED`, `UNINITIALIZED`, `REMOVED`) say nothing and are only
recorded. An alert is identified by its host, chart and name.

**What an investigation gets.** `active_alerts_<name>` (the alerts raised now, each with its
chart, value and since when) and `chart_data_<name>` (the values of one chart over up to
seven days, averaged into rows).

One source is one agent. The reads use the agent's `/api/v1`, which it still serves next to
v3. Notifications sent by Netdata Cloud are not read: they need a certificate handshake Paeon
does not do.

### Zabbix

A webhook media type brings problems and recoveries. With a `url` and an API token, problems,
items and their history are read.

**Before you start.**

1. **Alerts**, **Media types**, **Create media type**, type **Webhook**, with these
   parameters:

    | Name | Value |
    |---|---|
    | `paeon_url` | `http://paeon:8080/ingest/zabbix` |
    | `paeon_token` | the ingest token |
    | `event_id` | `{EVENT.ID}` |
    | `event_value` | `{EVENT.VALUE}` |
    | `update_status` | `{EVENT.UPDATE.STATUS}` |
    | `trigger_id` | `{TRIGGER.ID}` |
    | `name` | `{EVENT.NAME}` |
    | `severity` | `{EVENT.NSEVERITY}` |
    | `host` | `{HOST.NAME}` |

    and this script:

    ```js
    var params = JSON.parse(value);
    var request = new HttpRequest();
    request.addHeader('Content-Type: application/json');
    request.addHeader('Authorization: Bearer ' + params.paeon_token);
    var target = params.paeon_url;
    delete params.paeon_url;
    delete params.paeon_token;
    var answer = request.post(target, JSON.stringify(params));
    if (request.getStatus() !== 200) {
      throw 'Paeon answered HTTP ' + request.getStatus();
    }
    return answer;
    ```

2. On its **Message templates** tab, add a template for **Problem** and one for **Problem
   recovery**. Their text is not used, but Zabbix sends nothing without them.
3. Give a user this media (**Users**, the user, **Media**), and add a **trigger action** whose
   operations and recovery operations send to that user.
4. For the tools: a **user role** of type User with API access limited to an allow-list
   (`problem.get`, `trigger.get`, `host.get`, `item.get`, `history.get`), a user with that
   role and read permission on the host groups you want read, and an **API token** for that
   user (**Users**, **API tokens**). That token is `token_env`.

| Key | Default | |
|---|---|---|
| `url` | | The frontend's address, without `api_jsonrpc.php`. Without it, the source only receives. |
| `token_env` | | Variable with the API token. |
| `hosts` | none | Host name patterns whose items and history may be read (`server-*`). Empty: no item tools. |
| `max_values` | `200` | Most history values one request returns. 2000 at most. |
| `user` | | User for basic authentication in front of the frontend, with `password_env`. |
| `password_env` | | Variable with that user's password. |

```yaml
zabbix: { type: zabbix, url: https://zabbix.example.org, token_env: PAEON_ZABBIX_TOKEN, hosts: ["server-*"] }
```

**What arrives.** A problem is an alert identified by its trigger, with the event's name as
title; its recovery resolves it. Severities map as: disaster and high to critical, average and
warning to warning, information to info. A message about an update of a problem (an
acknowledgement, a comment) is not a state change and is only recorded.

**What an investigation gets.** `problems_<name>` (the open problems, with their hosts and
whether someone acknowledged them), `host_items_<name>` (the items collected on one host, with
their last value) and `item_history_<name>` (one item's values over up to seven days).

Zabbix's API takes every call as a POST to one address, reads included. This source can only
send the five `get` methods above and `apiinfo.version`, with parameters it writes itself; the
role of step 4 makes Zabbix refuse anything else as well. Items and history are read for the
hosts in `hosts` only; problems are listed for every host the token can see.

### Beszel

A Beszel hub is polled for the alerts that are triggered. Its systems, their statistics and
their containers are read.

**Before you start.** Paeon signs in to the hub with an account. Beszel keeps alerts **per
account**: Paeon sees the alerts of the account it signs in with. Either

- give Paeon the account that set the alerts, or
- create an account for Paeon with the role **read-only**, share the systems with it (or set
  `SHARE_ALL_SYSTEMS=true` on the hub), sign in once as that account and set the alerts you
  want Paeon to see.

Do not use the hub's superuser.

| Key | Default | |
|---|---|---|
| **`url`** | | The hub, for example `http://beszel:8090`. |
| `user` | | The account's e-mail. Needed. |
| `password_env` | | Variable with its password. Needed. |
| `alerts` | `true` | Poll the triggered alerts. |
| `poll_seconds` | `60` | How often the hub is polled. 10 at least. |
| `stale_seconds` | `900` | Without an answer for this long, the source is reported stale. |
| `token_env` | | Not used: Beszel signs in with a password. |

```yaml
beszel:
  type: beszel
  url: http://beszel:8090
  user: paeon@example.org
  password_env: PAEON_BESZEL_PASSWORD
```

**What arrives.** Each Beszel alert that is triggered is a firing alert named
`<system>: <what>` (CPU, Memory, Disk, Status); when Beszel clears it, it is resolved. A
`Status` alert, the hub losing a system, is critical; the others are warnings.

**What an investigation gets.** `system_status_<name>` (each system: up, down or paused, with
its last figures), `system_stats_<name>` (one system minute by minute) and
`containers_<name>` (status, health, CPU and memory of the containers Beszel sees). Figures
keep Beszel's own short names: `cpu`, `mp` (memory %), `dp` (disk %).

Nothing is pushed: Beszel's notifications carry a sentence, with nothing that says firing or
resolved but its wording. The sign-in is the one request that is not a GET. Beszel says its
collections may change between versions: if a hub answers differently after an update, the
source becomes stale and says so, and `paeon source` shows the error.

## When something does not work

Run `paeon source <name>` first. It exits 0 when the service answered, 1 when it did not, and
2 when the source is not defined or its secret is not set.

| What you see | What it means |
|---|---|
| `no source 'x' under sources` | The name is not in the rules file the process reads (`PAEON_CONFIG`). |
| `sources.x: NAME (or _FILE) is not set` | The secret the source names is missing from the environment. |
| `<service> is unreachable: ConnectError` | The address is wrong or not reachable from Paeon's container. Use the internal name. |
| `<service> answered HTTP 401` or `403` | The credential is wrong, or may not read this. For Grafana rules and Zabbix methods, see their sections. |
| `<service> answered HTTP 404` | The `url` has a wrong prefix, or the server does not answer this API (`rules: false`, `targets: false`). |
| The service gets `401` from Paeon | The bearer it sends is not the ingest token. |
| The service gets `404` from Paeon | No source of that name receives: check the name in the address. |
| The service gets `422` from Paeon | The body is not JSON (a quote in a Gatus or Healthchecks value), or not an object. |
| `"alerts": 0` in Paeon's answer | The body was read and says nothing: a test message, a placeholder that was not replaced, a status that is not a verdict. |
| `source_stale` in the incidents | A polled source has not answered for its `stale_seconds`. |

Every push is in the event log as it came, even when it says nothing:

```bash
docker exec paeon paeon events --kind webhook.received
```

## What has run against a real service

The OpenObserve adapter was written against a real instance, and the uptime-kuma and
Alertmanager webhooks are the ones Paeon has always read. The other adapters are written from
each service's documentation and tested against the payloads and answers it documents; none of
them has met a live server yet. If you run one, `paeon source <name>` and a first alert tell you in
a minute whether it holds, and a [report](https://github.com/mmornati/paeon/issues) of what
differs is welcome.
