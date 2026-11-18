# Configuration

Behaviour is one YAML file, named by `PAEON_CONFIG`: correlation windows, host rules, maintenance
windows, model aliases, triage, investigation, postmortems and the digest. Start from
[examples/paeon.yaml](https://github.com/mmornati/paeon/blob/main/examples/paeon.yaml). Every key is optional and **every stage is off until
you enable it**.

Secrets never go in that file. It names the environment variable that holds them (`api_key_env`,
`token_env`), and every secret also accepts `NAME_FILE=/path` for Docker secrets.

## Stages

| Stage | What it does | Model |
|---|---|---|
| `triage` | One call per incident with a context pack built by code. Verdict: `noise`, `transient`, `known_issue` or `real`. **Shadow mode**: recorded and shown in the digest, nothing acts on it. Page-level alerts are never sent. | cheap |
| `investigation` | An agent loop with read-only tools and a budget. Hands in a summary, the impact, a root cause with a confidence, the evidence, the hypotheses it rejected and a plan. A second model can take over when the first fails or declines. | strong, with tool calls |
| `postmortems` | A markdown draft with YAML front matter once an incident that was investigated or acted on has stayed resolved. Timeline and automation review come from the event log; the model writes the prose; the file says `status: draft`. Optionally committed and pushed to a git repository. | strong |
| `actions` | No model. Plans from an analysis or from a runbook go to the executor. See [Acting](acting.md). | none |

The budgets (`daily_budget`, `budget_per_incident`), the storm guard (`max_per_hour`) and the limits on tool calls and time are set per stage in the same file. A spent budget never hides an alert: Paeon fails open and treats the incident as real.

## Environment

The service itself is configured by `PAEON_*` variables: the ingest token, the database path, the address it listens on, the OpenObserve account, the heartbeat URL and the path of the rules file. [.env.example](https://github.com/mmornati/paeon/blob/main/.env.example) lists them all with a comment each.

## Traces

```yaml
telemetry:
  otlp_endpoint: http://openobserve:5080/api/default
  authorization_env: PAEON_OTLP_AUTHORIZATION     # "Basic <base64 of user:password>"
```

Each incident becomes an OpenTelemetry trace: a span for the incident and one per step, with its
duration, model, token counts, cost and outcome. Spans carry facts about a step and never its
content: no prompt, no answer, no tool input or result.
