# Command line, API and MCP

## Command line

| Command | |
|---|---|
| `paeon serve` | Run the core |
| `paeon executor` | Run the executor (only with `actions.enabled`) |
| `paeon incidents` | Incidents as JSON lines |
| `paeon events --kind alert.firing` | The event log as JSON lines |
| `paeon digest --hours 24` | The digest of the last hours |
| `paeon feedback <id> up\|down` | Say whether triage was right |
| `paeon autonomy` | Each action's level, its track record, and the promotions it has earned |
| `paeon stop` / `paeon resume` | The kill switch, next to the executor |
| `paeon reinstate <alert> <action> <container>` | Lift a demotion, next to the executor |
| `paeon notify <channel>` | Send a test message through one notification channel |
| `paeon source <name>` | Say what one source gives, and ask its service one question |
| `paeon mcp` | Serve the record read-only to an assistant, over MCP on stdio |
| `paeon replay triage\|investigation` | Re-run recorded incidents against the model and prompts of today |

The same is available over HTTP behind the bearer token: `GET /incidents`, `GET /incidents/{id}`,
`POST /incidents/{id}/feedback`, `GET /digest`, `GET /postmortems`, `GET /autonomy`, and
`GET /brief` (what is open, what waits for your answer, what waits for a review: made for a
daily brief).

Alerts come in by `POST /ingest/<name>`, one address per [source](sources.md), and planned work
by `POST /ingest/change`.

## Ask it from your assistant

`paeon mcp` is a read-only [MCP](https://modelcontextprotocol.io) server over the record:
incidents, their findings, postmortems and runbooks. It opens the database file directly, so it
needs no network path to the service. In Claude Code, from the repository of your server:

```bash
claude mcp add paeon -- ssh server-1 docker exec -i paeon paeon mcp
```

Then "has this happened before?" is a question it can answer. What it returns is redacted like
everything a model reads, and it has no tool that writes.

## Before you change a prompt or a model

Every triage call recorded what it was given, and every investigation its tools and what each
one returned. `paeon replay` sends the same again to the model as it is configured now:

```bash
paeon replay triage --days 90
```

It compares with what was said then and with what you said about it. A verdict you rated right
that changes is a regression, and one that would now be called noise is reported loudest; the
command exits 1. For investigations the old and new root causes are printed side by side, and
a call the model did not make at the time answers "not recorded". `--export corpus.jsonl` writes
the cases to a file you can keep in git, `--corpus` replays from it, `--dry-run` lists them.

A replay writes nothing to the event log and counts against no budget, but it does call the
model: it prints what that cost. Pin the model for it; with a gateway choosing, a prompt change
and a routing change cannot be told apart.
