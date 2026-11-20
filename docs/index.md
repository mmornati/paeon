# Paeon

**The on-call colleague for your self-hosted server.** Alert triage, read-only investigation and
approved remediation, with a hard line between what a model may think and what code may do.

It is 03:12. Your phone buzzes: `SLO fast burn`. Is it the nightly update again, or is the disk
really full this time? Paeon sits next to your alerting and answers that question before you are
awake enough to ask it.

<div class="video" markdown>
<iframe src="../videos/the-flow/?embed=1&lang=en" title="One incident, from alert to fix" loading="lazy" allow="fullscreen; autoplay" allowfullscreen></iframe>
</div>

!!! warning "Out of the box Paeon takes no action on your server"

    It records, correlates, triages, investigates and drafts postmortems. Acting is a second
    process you add on purpose, with a closed catalog of actions, and nothing runs without your
    Go until you say otherwise. It is alpha software, and the acting part has not yet run on a
    real server for long: run it next to your alerting, never instead of it.

## Start here

| | |
|---|---|
| [Quickstart](quickstart.md) | One machine, no model key, no file to write, six steps: an alert in, an incident out, the rest finished in the console. |
| [Prometheus quickstart](quickstart-prometheus.md) | A compose project you can throw away: break a service, read the investigation. |
| [How it works](how-it-works.md) | The pipeline, the stages, and why the part that thinks cannot act. |
| [Videos](videos.md) | Paeon explained, and one incident followed from the alert to the fix. |

## Why Paeon

- **The model proposes, code disposes.** No shell, no free-form command, no write credential for
  a model, in any stage. A plan is a list of actions from a closed catalog, checked again by a
  separate executor that trusts nothing the model touched.
- **Go / No-go on your phone.** A signed, single-use, expiring button, bound to the exact plan.
  Opening the link never applies anything.
- **Autonomy is earned, per action and per container.** Paeon proposes a promotion after a
  record of verified runs; you make it, in git. One failure, one Undo or one "This was wrong"
  takes it back.
- **Investigation with evidence.** Every claim in an analysis is tied to the tool call that
  supports it. A claim that cites no real call is marked as unverified.
- **Allow-lists first, redaction second.** A log stream, a metric or a container that you did
  not list cannot be read, and what is read is scrubbed of secret-shaped strings before it
  leaves.
- **Everything is an event.** One append-only SQLite log: every alert, decision, model call
  (with its cost) and tool result.
- **Bounded by design.** Budgets per incident and per day, a storm guard per hour, limits on
  tool calls and time. A spent budget never hides an alert: Paeon fails open.
- **Your models, your gateway.** The Anthropic Messages API and the OpenAI-compatible chat API,
  each with a configurable base URL. Local servers work.
- **Small.** One container, one SQLite file, one YAML file. No vector database, no queue.

Paeon is an independent project, not affiliated with or endorsed by OpenObserve, uptime-kuma,
Prometheus, Docker, ntfy, Anthropic or OpenAI; those names belong to their owners.
