# Quickstart

One machine, no account and no model key. In six steps you will start Paeon, send it an alert,
watch it become an incident, and open the console. Nothing leaves your machine: without a rules
file that enables a stage, Paeon calls no model.

You need Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

!!! tip "Running Prometheus and Docker already?"

    The [Prometheus quickstart](quickstart-prometheus.md) starts a small compose project with
    Prometheus, Alertmanager and a demo service you break on purpose, and goes as far as a first
    investigated alert.

## 1. Get it

```bash
git clone https://github.com/mmornati/paeon.git && cd paeon
```

```bash
uv sync
```

## 2. Configure the minimum

Paeon needs one secret, the token your alert senders present. Generate it into `.env`:

```bash
echo "PAEON_INGEST_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" > .env
```

And a rules file that turns the console on:

```bash
printf 'ui:\n  enabled: true\n' > paeon.yaml && echo "PAEON_CONFIG=paeon.yaml" >> .env
```

## 3. Start it

```bash
set -a && . ./.env && set +a && uv run paeon serve
```

It listens on `127.0.0.1:8080` and keeps its event log in `paeon.sqlite`. Leave it running and
open a second terminal in the same folder.

## 4. Send a first alert

Load the token in the second terminal:

```bash
set -a && . ./.env && set +a
```

Then post an alert the way Alertmanager would:

```bash
curl -X POST http://127.0.0.1:8080/ingest/alertmanager -H "Authorization: Bearer $PAEON_INGEST_TOKEN" -H 'Content-Type: application/json' -d '{"alerts": [{"status": "firing", "fingerprint": "demo-disk-full", "labels": {"alertname": "DiskFull", "severity": "warning", "instance": "server-1"}, "annotations": {"summary": "/ is 95 % full"}}]}'
```

Paeon answers `{"alerts":1,"changes":1}`: one alert received, one state change recorded.

## 5. See the incident

```bash
uv run paeon incidents
```

One line of JSON: incident 1, `open`, on `server-1`, with the alert attached. Now open the
console at <http://127.0.0.1:8080/ui>: the incident is in the **Open** group, and its page shows
the record of what happened so far.

!!! warning "Keep it local"

    The console has no login here. On your own machine, bound to `127.0.0.1`, that is fine. On
    a server it belongs behind a reverse proxy that authenticates, or it can ask for a password
    itself (`ui.auth: local`): see [the console](console.md).

## 6. Resolve it

Send the same alert with `resolved`:

```bash
curl -X POST http://127.0.0.1:8080/ingest/alertmanager -H "Authorization: Bearer $PAEON_INGEST_TOKEN" -H 'Content-Type: application/json' -d '{"alerts": [{"status": "resolved", "fingerprint": "demo-disk-full", "labels": {"alertname": "DiskFull", "severity": "warning", "instance": "server-1"}, "annotations": {"summary": "/ is 95 % full"}}]}'
```

The incident moves to **Resolved**. Every step you just took is one line of the event log:

```bash
uv run paeon events
```

## What you have, and what is next

You have the recording half of Paeon: alerts in, incidents out, a log of everything. No model
was called and nothing was sent anywhere. From here, each step is a decision of yours:

| You want | Go to |
|---|---|
| Your real alerts in it | [Connect your alerts](connect-alerts.md): uptime-kuma, Alertmanager, Grafana, OpenObserve, Gatus, Zabbix and others |
| A model to say whether an alert is noise | `triage` in [Configuration](configuration.md): one cheap call per incident, in shadow mode |
| A root cause with evidence on your phone | `investigation` in [Configuration](configuration.md), and what it sends in [Privacy and safety](privacy-and-safety.md) |
| Paeon to fix things after your Go | [Acting](acting.md): a second process, a closed catalog, and a decision of yours |
| It running for real | The hardened [compose file](https://github.com/mmornati/paeon/blob/main/examples/compose.yaml): read-only root filesystem, non-root, secrets as files, Docker through a proxy limited to `GET` |

## If it does not work

| Symptom | Cause |
|---|---|
| `paeon serve` exits at once | `PAEON_INGEST_TOKEN` is missing or shorter than 24 characters. The message says so. |
| `curl` answers 401 | The token in this terminal is not the one the server started with: run the `set -a` line again. |
| `/ui` answers 404 | `ui.enabled` is not set in the rules file Paeon reads: check `PAEON_CONFIG` in `.env`. |
| Port 8080 is taken | Add `PAEON_PORT=8090` to `.env` and use that port in the commands. |
