# Contributing to Paeon

Thank you for looking. Paeon is young and its design is opinionated, so the fastest way to a
merged change is to talk first: open an issue describing the problem before a large pull request.

## Ground rules

These come from the [design](docs/llm-incident-response-architecture.md) and are not up for
negotiation in a pull request:

- **The model proposes; code disposes.** No tool that writes, no shell, no free-form command for
  a model, in any stage.
- **Everything read is behind an allow-list**, and everything sent to a model is redacted first.
- **Every decision is an event** in the append-only log. If a change makes Paeon decide something,
  it records it.
- **Fail open.** A broken model, a spent budget or a bad configuration must never hide an alert.
- **Paeon must never make alerting worse.** It adds to the existing path; it does not replace it.

## Setup

You need Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

```bash
uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest --cov
```

That line is what CI runs. Coverage must stay above 85 %, and `mypy` runs in strict mode.

## Tests

- No test calls a real provider or server. Model, ntfy, Docker and OpenObserve calls go through a
  mock transport that replays recorded or hand-written answers (`tests/support.py`,
  `tests/fixtures/`).
- **Never commit real data.** No real alert payload, log line, hostname, address, token or model
  answer from your own systems. Rewrite fixtures by hand with `example.org`, `server-1` and
  made-up values.
- A change to an allow-list or to redaction needs tests for what must be refused, not only for
  what is accepted.

## Pull requests

- One topic per pull request, with tests, and the README or example config updated when behaviour
  changes.
- Commit messages: an imperative summary line, then why.
- New dependencies need a reason and a permissive licence (MIT, BSD, Apache-2.0 or similar).
- GitHub Actions are pinned by commit hash and base images by digest; keep it that way.

## Licence of contributions

Paeon is licensed under [Apache-2.0](LICENSE). By submitting a contribution you agree that it is
licensed under the same terms (section 5 of the licence), and you confirm that you have the right
to submit it. There is no separate contributor agreement.

## Security

Do not report vulnerabilities in an issue or a pull request. See [SECURITY.md](SECURITY.md).
