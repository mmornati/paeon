# Security policy

Paeon reads alerts and logs, sends parts of them to a language model, and is meant to end up
approving actions on a server. Reports about its security are welcome and taken seriously.

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private reporting instead:
[Report a vulnerability](https://github.com/mmornati/paeon/security/advisories/new).

Say what you found, how to reproduce it, and what an attacker gains. You should get a first answer
within a week. This is a project maintained in spare time: there is no bug bounty, and fixes land
on `main` first.

## Supported versions

Paeon is pre-1.0. Only the latest commit on `main` (the `edge` image) receives fixes.

## What is in scope

- Reading something the allow-lists should have refused: an unlisted log stream, metric, container
  log, container environment, or a file outside the knowledge paths.
- Getting past the bearer token, or making the service call a URL the operator did not configure.
- A secret (token, API key, heartbeat URL) written to the event log, the process log, a
  notification or a model request.
- Text in an alert or a log line (which Paeon treats as attacker-controlled) causing anything
  other than a wrong analysis: a side effect, a notification to another place, a file written
  outside the postmortem directory.

## What is not a vulnerability

- **A wrong or manipulated analysis.** The model can be wrong and can be misled by what it reads.
  That is why its output is advice for a person, and why Paeon takes no action today.
- **Redaction that misses a secret of an unusual shape.** Redaction is a second net. The control
  is the allow-list of what the model may read; a miss is a bug worth an ordinary issue (without
  the secret in it).
- **Exposure of the API to the internet.** Paeon has one bearer token and no TLS, no users and no
  rate limiting. It is built for an internal network; see the deployment notes below.

## Deploying it safely

- Keep the API on an internal network. Do not publish its port; if you must reach it from
  outside, put it behind a reverse proxy that adds TLS and authentication.
- Give it credentials that can only read: an OpenObserve account limited to search, and a Docker
  socket proxy limited to `GET`. Never mount the Docker socket into Paeon.
- List only the streams, metrics and containers you are willing to send to your model provider.
  Leave out anything holding personal data.
- Pass secrets as files (`NAME_FILE`) rather than environment variables where you can.
- The SQLite database holds alert payloads and redacted log excerpts. Treat it, its backups and
  the postmortem directory as sensitive.
- Set `daily_budget` and `max_per_hour`: an alert storm otherwise becomes a model bill.
