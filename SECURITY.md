# Security policy

Paeon reads alerts and logs, sends parts of them to a language model, and can restart containers
on a server after an approval. Reports about its security are welcome and taken seriously. What
it defends against, and what it does not, is in the [threat model](docs/threat-model.md).

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
- Anything acting without the approval its policy requires: an action outside the catalog or on
  a container the catalog does not allow, a forged, replayed or expired approval link that works,
  a link that applies on a GET, a plan that runs at a higher level than the executor's rules give.
- The core obtaining what only the executor should have: the signing key, a way to approve, a
  way to release the kill switch.
- The executor running anything while the kill switch is engaged.

## What is not a vulnerability

- **A wrong or manipulated analysis.** The model can be wrong and can be misled by what it reads.
  That is why its output is advice for a person, and why nothing in it can run except a catalog
  action at the level a person chose.
- **A subverted core triggering an `auto` policy.** The executor cannot see alerts and believes
  the core about which alert a plan is for. This limit is documented in the threat model; a way
  around the catalog, the allow-listed targets or the rate limit would be a vulnerability.
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
- If you enable actions: run the executor as its own container with its own copy of the rules
  file, give the signing key to it alone, route only `/act/` to it from outside, and go through
  the checklist at the end of the [threat model](docs/threat-model.md).
