**What and why**

**How it was checked**

- [ ] `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest --cov`
- [ ] Tests cover what must be refused, where an allow-list or redaction is touched
- [ ] No real data in fixtures: no hostnames, addresses, tokens, payloads or model answers from a real system
- [ ] README and `examples/` updated if behaviour or configuration changed
