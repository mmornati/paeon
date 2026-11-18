"""Settings: the deployment from the environment (see `.env.example`), the behaviour from YAML.

`PAEON_CONFIG` names the rules file (`paeon.rules`): correlation, maintenance windows, model
aliases, triage, digest. Secrets stay in the environment, as `NAME` or as `NAME_FILE` (a path,
for Docker secrets); the rules file only names the variable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from paeon.rules import Rules, RulesError, load_rules


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class OpenObserveSettings:
    url: str
    org: str
    user: str
    password: str
    poll_seconds: float
    stale_seconds: float


@dataclass(frozen=True)
class Settings:
    db_path: Path
    host: str
    port: int
    ingest_token: str
    openobserve: OpenObserveSettings | None
    heartbeat_url: str | None
    heartbeat_seconds: float
    rules: Rules = field(default_factory=Rules)
    model_keys: Mapping[str, str] = field(default_factory=dict)  # by model alias
    ntfy_token: str | None = None


def _secret(env: Mapping[str, str], name: str) -> str:
    file = env.get(f"{name}_FILE", "")
    if file:
        try:
            return Path(file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"{name}_FILE cannot be read: {exc}") from exc
    return env.get(name, "").strip()


def _number(env: Mapping[str, str], name: str, default: float, *, minimum: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum:g}")
    return value


def load(env: Mapping[str, str]) -> Settings:
    token = _secret(env, "PAEON_INGEST_TOKEN")
    if len(token) < 24:
        # Fail closed: an endpoint that records whatever anyone posts is worse than no endpoint.
        raise ConfigError("PAEON_INGEST_TOKEN (or _FILE) is required, 24 characters or more")

    openobserve: OpenObserveSettings | None = None
    url = env.get("PAEON_OPENOBSERVE_URL", "").strip()
    if url:
        user = env.get("PAEON_OPENOBSERVE_USER", "").strip()
        password = _secret(env, "PAEON_OPENOBSERVE_PASSWORD")
        if not user or not password:
            raise ConfigError(
                "PAEON_OPENOBSERVE_URL is set: PAEON_OPENOBSERVE_USER and"
                " PAEON_OPENOBSERVE_PASSWORD (or _FILE) are required too"
            )
        openobserve = OpenObserveSettings(
            url=url,
            org=env.get("PAEON_OPENOBSERVE_ORG", "").strip() or "default",
            user=user,
            password=password,
            poll_seconds=_number(env, "PAEON_OPENOBSERVE_POLL_SECONDS", 60, minimum=5),
            stale_seconds=_number(env, "PAEON_OPENOBSERVE_STALE_SECONDS", 900, minimum=60),
        )

    rules = Rules()
    rules_path = env.get("PAEON_CONFIG", "").strip()
    if rules_path:
        try:
            rules = load_rules(Path(rules_path))
        except RulesError as exc:
            raise ConfigError(f"PAEON_CONFIG: {exc}") from exc
    model_keys = {
        name: _secret(env, alias.api_key_env)
        for name, alias in rules.models.items()
        if alias.api_key_env
    }
    ntfy = rules.digest.ntfy
    ntfy_token = _secret(env, ntfy.token_env) if ntfy is not None and ntfy.token_env else ""

    port = int(_number(env, "PAEON_PORT", 8080, minimum=1))
    return Settings(
        db_path=Path(env.get("PAEON_DB", "").strip() or "paeon.sqlite"),
        host=env.get("PAEON_HOST", "").strip() or "127.0.0.1",
        port=port,
        ingest_token=token,
        openobserve=openobserve,
        heartbeat_url=env.get("PAEON_HEARTBEAT_URL", "").strip() or None,
        heartbeat_seconds=_number(env, "PAEON_HEARTBEAT_SECONDS", 60, minimum=10),
        rules=rules,
        model_keys=model_keys,
        ntfy_token=ntfy_token or None,
    )
