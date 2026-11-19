"""Settings: the deployment from the environment (see `.env.example`), the behaviour from YAML.

`PAEON_CONFIG` names the rules file (`paeon.rules`): correlation, maintenance windows, model
aliases, triage, digest. Secrets stay in the environment, as `NAME` or as `NAME_FILE` (a path,
for Docker secrets); the rules file only names the variable.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from paeon.rules import Rules, RulesError, load_rules

log = logging.getLogger(__name__)


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
    ntfy_token: str | None = None  # the digest's
    investigation_ntfy_token: str | None = None
    executor_token: str | None = None  # how the core proves itself to the executor
    uptime_kuma_key: str | None = None
    telemetry_authorization: str | None = None
    ui_proxy_secret: str | None = None  # what the proxy adds to each console request
    notifier_secrets: Mapping[str, Mapping[str, str]] = field(default_factory=dict)  # by channel


@dataclass(frozen=True)
class ExecutorSettings:
    """`paeon executor`: its own file, its own port, and the only place the signing key is."""

    db_path: Path
    host: str
    port: int
    token: str
    rules: Rules
    signing_key: str | None
    ntfy_token: str | None
    kill_switch: bool
    coolify_token: str | None = None
    github_token: str | None = None
    notifier_secrets: Mapping[str, Mapping[str, str]] = field(default_factory=dict)


def _secret(env: Mapping[str, str], name: str) -> str:
    file = env.get(f"{name}_FILE", "")
    if file:
        try:
            return Path(file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"{name}_FILE cannot be read: {exc}") from exc
    return env.get(name, "").strip()


def _notifier_secrets(
    env: Mapping[str, str], rules: Rules, stages: tuple[str, ...]
) -> dict[str, dict[str, str]]:
    """The secrets of the named channels these stages send to. A process reads only its own:
    the core never needs the token of the channel that carries approvals."""
    found: dict[str, dict[str, str]] = {}
    for stage in stages:
        for name in getattr(rules, stage).notify:
            if name in found:
                continue
            found[name] = {}
            for key, (variable, required) in rules.notifiers[name].secret_envs().items():
                value = _secret(env, variable)
                if value:
                    found[name][key] = value
                elif required:
                    raise ConfigError(f"notifiers.{name}: {variable} (or _FILE) is not set")
    return found


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
    for warning in rules.unpriced_budgets():
        log.warning("%s", warning)
    model_keys = {
        name: _secret(env, alias.api_key_env)
        for name, alias in rules.models.items()
        if alias.api_key_env
    }
    ntfy = rules.digest.ntfy
    ntfy_token = _secret(env, ntfy.token_env) if ntfy is not None and ntfy.token_env else ""
    analysis = rules.investigation.ntfy
    analysis_token = (
        _secret(env, analysis.token_env) if analysis is not None and analysis.token_env else ""
    )

    executor_token = _secret(env, "PAEON_EXECUTOR_TOKEN")
    if rules.actions.enabled and len(executor_token) < 24:
        raise ConfigError(
            "actions.enabled is set: PAEON_EXECUTOR_TOKEN (or _FILE) is required, 24 characters"
            " or more, the same as the executor's"
        )
    kuma = rules.investigation.tools.uptime_kuma
    kuma_key = _secret(env, kuma.api_key_env) if kuma is not None and kuma.api_key_env else ""

    telemetry = rules.telemetry
    telemetry_authorization = (
        _secret(env, telemetry.authorization_env)
        if telemetry is not None and telemetry.authorization_env
        else ""
    )

    proxy = rules.ui.proxy_secret
    proxy_secret = "" if proxy is None else _secret(env, proxy.value_env)
    if proxy is not None and len(proxy_secret) < 24:
        # Fail closed: a console that names a secret and runs without it would be open.
        raise ConfigError(
            f"ui.proxy_secret is set: {proxy.value_env} (or _FILE) is required, 24 characters"
            " or more"
        )

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
        investigation_ntfy_token=analysis_token or None,
        executor_token=executor_token or None,
        uptime_kuma_key=kuma_key or None,
        telemetry_authorization=telemetry_authorization or None,
        ui_proxy_secret=proxy_secret or None,
        notifier_secrets=_notifier_secrets(env, rules, ("digest", "investigation")),
    )


def load_executor(env: Mapping[str, str]) -> ExecutorSettings:
    token = _secret(env, "PAEON_EXECUTOR_TOKEN")
    if len(token) < 24:
        raise ConfigError("PAEON_EXECUTOR_TOKEN (or _FILE) is required, 24 characters or more")
    rules_path = env.get("PAEON_CONFIG", "").strip()
    if not rules_path:
        # Without its own catalog the executor would have nothing to check a plan against.
        raise ConfigError("PAEON_CONFIG is required: the executor reads its own rules file")
    try:
        rules = load_rules(Path(rules_path))
    except RulesError as exc:
        raise ConfigError(f"PAEON_CONFIG: {exc}") from exc
    key = _secret(env, "PAEON_SIGNING_KEY")
    if key and len(key) < 32:
        raise ConfigError("PAEON_SIGNING_KEY (or _FILE) must be 32 characters or more")
    if key and key == token:
        raise ConfigError("PAEON_SIGNING_KEY must differ from PAEON_EXECUTOR_TOKEN")
    ntfy = rules.actions.ntfy
    ntfy_token = _secret(env, ntfy.token_env) if ntfy is not None and ntfy.token_env else ""
    coolify = rules.actions.coolify
    coolify_token = "" if coolify is None else _secret(env, coolify.token_env)
    if coolify is not None and not coolify_token:
        raise ConfigError(f"actions.coolify: {coolify.token_env} (or _FILE) is not set")
    repository = rules.actions.repository
    github = None if repository is None else repository.github
    github_token = "" if github is None else _secret(env, github.token_env)
    if github is not None and not github_token:
        raise ConfigError(f"actions.repository.github: {github.token_env} (or _FILE) is not set")
    return ExecutorSettings(
        coolify_token=coolify_token or None,
        github_token=github_token or None,
        notifier_secrets=_notifier_secrets(env, rules, ("actions",)),
        db_path=Path(env.get("PAEON_EXECUTOR_DB", "").strip() or "executor.sqlite"),
        host=env.get("PAEON_EXECUTOR_HOST", "").strip() or "127.0.0.1",
        port=int(_number(env, "PAEON_EXECUTOR_PORT", 8081, minimum=1)),
        token=token,
        rules=rules,
        signing_key=key or None,
        ntfy_token=ntfy_token or None,
        kill_switch=env.get("PAEON_KILL_SWITCH", "").strip().lower() in ("1", "true", "yes", "on"),
    )
