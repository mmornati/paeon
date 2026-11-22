# Quick start: Prometheus, Alertmanager and Docker

From nothing to a first investigated alert, on one machine, with a compose project you can throw
away afterwards. You need Docker with the compose plugin and a clone of this repository. A model
API key is optional: without one Paeon records and correlates, and you see everything but the
analysis.

Nothing here acts on your machine. Paeon reads Docker through a proxy limited to `GET`.

## 1. Start it

```bash
cd examples/prometheus && mkdir -p secrets
```

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))" > secrets/ingest_token
```

Put your Anthropic API key in `secrets/anthropic_api_key`, or the word `none` to run without a
model:

```bash
echo none > secrets/anthropic_api_key
```

```bash
docker compose up --build -d
```

If port 8080 or 9090 is taken on your machine, change the left side of the `ports` lines in
`compose.yaml` first.

That starts five containers: `demo-app` (the thing you will break), Prometheus, Alertmanager,
Paeon, and a Docker socket proxy. The console is at <http://127.0.0.1:8080/ui> and Prometheus at
<http://127.0.0.1:9090>. Both are bound to localhost.

Without a key, turn the two model stages off before starting: set `investigation.enabled` and
`postmortems.enabled` to `false` in `paeon.yaml`.

## 2. Break something

```bash
docker compose stop demo-app
```

Within a minute Prometheus fires `DemoAppDown`, Alertmanager posts it to
`/ingest/alertmanager`, and an incident appears in the console's incident explorer. With a key, about twenty seconds later
the investigation starts: watch the incident's page, where each tool call appears as it is made.
The model lists the containers, sees `demo-app` exited, reads its last log lines (the only
container the rules let it read), finds the runbook in `runbooks/demo-app.md`, and hands in a root
cause with the evidence behind each claim.

## 3. Fix it

```bash
docker compose start demo-app
```

The alert resolves, the incident closes, and half an hour later a postmortem draft is written
(the delay is the window in which the same alert would reopen the incident; it is
`correlation.window_minutes`). It appears under **Postmortems** in the console.

## 4. Look at what was recorded

```bash
docker compose exec paeon paeon incidents
```

```bash
docker compose exec paeon paeon events --kind tool.call
```

The **Spend** page shows what the model calls cost. The prices in `paeon.yaml` are zero: set
the ones of your contract, then `daily_budget` and `budget_per_incident`, before you point Paeon
at alerts you do not control.

## 5. Make it yours

| You want | Do |
|---|---|
| Your own alerts | Add the `paeon` receiver of `alertmanager.yml` to your Alertmanager, next to your existing receivers, with the bearer token in a file. Paeon is never the only path of an alert. |
| Logs and metrics as tools | Paeon's telemetry tools read OpenObserve today. With Prometheus alone the investigator has Docker, Paeon's own record and your runbooks. |
| Your containers | List them under `investigation.tools.docker.logs`. A container that is not listed cannot have its logs read, and the environment of none is ever returned. |
| Your runbooks | Mount a directory of markdown at `/knowledge/runbooks`. Postmortems are added to the search as they are written. |
| A cheaper first look | Enable `triage` with a small model: one call per incident; it decides what is investigated and holds no alert back. See `examples/paeon.yaml`. |
| The analysis on your phone | Give `investigation` a channel: `investigation.ntfy`, or any service in [Notifications](notifications.md). |
| Paeon to fix things | Read [acting.md](acting.md) first. It is a second process and a decision of yours. |

## Clean up

```bash
docker compose down --volumes
```

## If it does not work

| Symptom | Cause |
|---|---|
| Paeon restarts in a loop | `secrets/ingest_token` is missing or shorter than 24 characters. `docker compose logs paeon` says so. |
| No incident after two minutes | Check <http://127.0.0.1:9090/alerts>: the alert must be firing, not pending. Then `docker compose logs alertmanager` for a 401 (the token file) or a connection error. |
| The investigation fails at once | The key is wrong or the model name does not exist for it: the incident page shows `HTTP 401` or `HTTP 404`. The alert is recorded all the same. |
| `/ui` answers 404 | `ui.enabled` is not set in the rules file Paeon reads. |
