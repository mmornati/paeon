from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from paeon.config import ConfigError, load
from paeon.rules import MaintenanceWindow, Matcher, Rules, RulesError, load_rules, parse_rules
from paeon.schema import Severity, Status
from support import alert

EXAMPLE = Path(__file__).parent.parent / "examples" / "paeon.yaml"
TOKEN = "t" * 32


def test_defaults_without_a_file() -> None:
    rules = parse_rules(None)
    assert rules == Rules()
    assert rules.correlation.window_minutes == 30
    assert not rules.triage.enabled


def test_the_example_file_is_valid() -> None:
    rules = load_rules(EXAMPLE)
    assert rules.models["triage"].backend == "openai"
    assert rules.correlation.hosts[0].host == "server-1"
    assert rules.maintenance[0].name == "weekly updates"
    assert rules.digest.ntfy is None  # copying the example must not send anything


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"corelation": {}}, "corelation"),
        ({"correlation": {"flap": {"threshold": 1}}}, "correlation.flap.threshold"),
        ({"triage": {"enabled": True}}, "not defined under `models`"),
        ({"triage": {"skip": [{"title": "("}]}}, "not a regular expression"),
        ({"digest": {"timezone": "Mars/Olympus"}}, "unknown timezone"),
        (["a list"], "must be a YAML mapping"),
    ],
)
def test_invalid_rules_are_refused(document: object, message: str) -> None:
    with pytest.raises(RulesError, match=message):
        parse_rules(document)


def test_unreadable_or_broken_file(tmp_path: Path) -> None:
    with pytest.raises(RulesError, match="cannot be read"):
        load_rules(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("correlation: [unclosed")
    with pytest.raises(RulesError, match="not valid YAML"):
        load_rules(broken)


def test_matcher() -> None:
    firing = alert(
        Status.FIRING, 0, title="slo_fast_burn_api", severity=Severity.CRITICAL, labels={"h": "x"}
    )
    assert Matcher().matches(firing)
    assert Matcher(source="test", title="slo_.*", severity=Severity.CRITICAL).matches(firing)
    assert not Matcher(title="slo").matches(firing)  # full match
    assert not Matcher(source="other").matches(firing)
    assert not Matcher(severity=Severity.INFO).matches(firing)
    assert Matcher(labels={"h": "x"}).matches(firing)
    assert not Matcher(labels={"h": "y"}).matches(firing)


def test_host_from_labels_then_rules() -> None:
    rules = parse_rules(
        {"correlation": {"hosts": [{"match": {"title": "slo_.*"}, "host": "server-1"}]}}
    )
    assert rules.correlation.host_of(alert(Status.FIRING, 0, labels={"instance": "nas"})) == "nas"
    assert rules.correlation.host_of(alert(Status.FIRING, 0, title="slo_x")) == "server-1"
    assert rules.correlation.host_of(alert(Status.FIRING, 0)) is None


def test_maintenance_window_in_local_time_and_across_midnight() -> None:
    paris = ZoneInfo("Europe/Paris")
    window = MaintenanceWindow.model_validate(
        {
            "name": "night",
            "days": ["sun"],
            "start": "23:30",
            "duration_minutes": 60,
            "timezone": "Europe/Paris",
        }
    )
    # 2026-11-22 is a Sunday.
    assert window.covers(datetime(2026, 11, 22, 23, 45, tzinfo=paris))
    assert window.covers(datetime(2026, 11, 23, 0, 15, tzinfo=paris))  # started on Sunday
    assert not window.covers(datetime(2026, 11, 23, 0, 45, tzinfo=paris))
    assert not window.covers(datetime(2026, 11, 23, 23, 45, tzinfo=paris))  # Monday
    assert not window.covers(datetime(2026, 11, 22, 23, 45, tzinfo=ZoneInfo("UTC")))


def test_settings_read_the_rules_and_the_keys_they_name(tmp_path: Path) -> None:
    rules = tmp_path / "paeon.yaml"
    rules.write_text(
        "models:\n"
        "  triage: {backend: openai, model: auto, api_key_env: PAEON_TRIAGE_API_KEY}\n"
        "  local: {backend: openai, model: small, base_url: 'http://llm:8000/v1'}\n"
        "triage: {enabled: true}\n"
        "digest:\n"
        "  ntfy: {url: 'https://ntfy.example.org', topic: incidents, token_env: PAEON_NTFY_TOKEN}\n"
    )
    key = tmp_path / "key"
    key.write_text("not-a-real-key\n")
    settings = load(
        {
            "PAEON_INGEST_TOKEN": TOKEN,
            "PAEON_CONFIG": str(rules),
            "PAEON_TRIAGE_API_KEY_FILE": str(key),
            "PAEON_NTFY_TOKEN": "not-a-real-token",
        }
    )
    assert settings.rules.triage.enabled
    assert settings.model_keys == {"triage": "not-a-real-key"}
    assert settings.ntfy_token == "not-a-real-token"


def test_settings_refuse_a_broken_rules_file(tmp_path: Path) -> None:
    rules = tmp_path / "paeon.yaml"
    rules.write_text("triage: {enabled: true}\n")
    with pytest.raises(ConfigError, match="PAEON_CONFIG"):
        load({"PAEON_INGEST_TOKEN": TOKEN, "PAEON_CONFIG": str(rules)})


def test_a_budget_on_a_model_without_a_price_is_reported() -> None:
    def rules(price: dict[str, float], budget: dict[str, float]) -> Rules:
        return parse_rules(
            {
                "models": {"m": {"backend": "openai", "model": "x", "price": price}},
                "triage": {"enabled": True, "model": "m", **budget},
            }
        )

    (warning,) = rules({}, {"daily_budget": 1.0}).unpriced_budgets()
    assert "models.m.price" in warning
    assert rules({"input_per_mtok": 1.0}, {"daily_budget": 1.0}).unpriced_budgets() == []
    assert rules({}, {}).unpriced_budgets() == []
