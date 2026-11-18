# Privacy and safety

## What leaves your server

Nothing, until you enable a stage. Then, always to the model you configured and after redaction
of secret-shaped strings:

| Stage | Sent to the model |
|---|---|
| Triage | Alert titles, descriptions and labels, and excerpts of the three runbooks or past postmortems that match them. No raw payload, no log line. |
| Investigation | The same, plus what its tools return: rows of the log streams you listed, the metrics you listed, container state without the environment, log lines of the containers you listed, the definition of an OpenObserve alert, monitor names and states, commit subjects and file names of the repositories you listed, the output of the commands you prepared on other hosts, and excerpts of your runbooks and postmortems. |
| Postmortems | The incident's timeline and the investigation's findings. |
| Actions | Nothing: no model is involved. The catalog (action names, target name patterns, the Coolify resource names and the files and keys you allow) is part of the investigator's prompt when actions are on. |
| Replay | What was sent at the time, again: recorded context packs and recorded tool results. |
| Traces | To your OTLP endpoint, not to a model: kinds, durations, model names, token counts, costs, outcomes, and incident and alert titles. |

The allow-lists in `investigation.tools` are the control: a stream or a container that is not
listed cannot be read. Leave out anything that holds personal data. Give Paeon credentials that
can only read as well (an OpenObserve account limited to search, a Docker socket proxy limited to
`GET`), so that the allow-lists are the second net and not the only one.

Paeon itself has no telemetry and calls nothing but what you configure: your alert source, your
model endpoint, your ntfy topics, your heartbeat URL and, if you set them, a git remote for the
postmortems, the hosts you listed for SSH, an OTLP endpoint, Coolify, and your deployment
repository with its forge's API.

## Safety model

Paeon assumes that **alert text and log lines are written by an attacker** (a file name, a
User-Agent, a hostname from DHCP) and that **the model will eventually be wrong**.

- The model's output is advice. The only part of it that code can ever run is a step that names
  one catalog action on one allowed container, and only at the level your policies give.
- The process that reads untrusted text and the process that can act are two, with different
  credentials. The second checks everything the first sends against its own rules.
- Tools only read, each behind an allow-list, with results bounded, redacted and recorded in full.
- Triage fails open: a model that is down, an invalid answer, low confidence or a spent budget all
  mean "treat as real".
- Paeon never sits in the path of your alerts. Your existing notifications stay as they are.
- The API has a bearer token and nothing else. **Keep it on an internal network.**

More in the [threat model](threat-model.md), in [SECURITY.md](https://github.com/mmornati/paeon/blob/main/SECURITY.md) (how to report a
vulnerability) and in [§4 of the design](llm-incident-response-architecture.md#4-safety-model).

## A word on cost and trust

Paeon calls paid model APIs on your behalf when you enable a stage. Set the budgets in the rules
file before you point it at a noisy alert source. Its analysis is written by a language model: it
can be confidently wrong. Read the evidence, not only the conclusion.
