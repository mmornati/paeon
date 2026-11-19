"""The documentation says what the code does: the pages that list keys are checked against it."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

import yaml

from paeon import rules
from paeon.rules import parse_rules

ROOT = Path(__file__).resolve().parent.parent
GUIDE = (ROOT / "docs" / "notifications.md").read_text(encoding="utf-8")
SECTION = {  # the heading of each service in the guide
    "ntfy": "ntfy",
    "telegram": "Telegram",
    "discord": "Discord",
    "slack": "Slack",
    "email": "E-mail",
    "gotify": "Gotify",
    "pushover": "Pushover",
    "teams": "Microsoft Teams",
    "matrix": "Matrix",
    "webhook": "Webhook",
    "apprise": "Apprise",
}


SOURCES = (ROOT / "docs" / "sources.md").read_text(encoding="utf-8")
SOURCE_SECTION = {  # the heading of each service in the sources guide
    "prometheus": "Prometheus",
    "alertmanager": "Alertmanager",
    "grafana": "Grafana",
    "loki": "Loki",
    "victorialogs": "VictoriaLogs",
    "openobserve": "OpenObserve",
    "uptime_kuma": "uptime-kuma",
    "gatus": "Gatus",
    "healthchecks": "Healthchecks",
    "netdata": "Netdata",
    "zabbix": "Zabbix",
    "beszel": "Beszel",
}


def kinds_of(section_name: str) -> dict[str, Any]:
    """The classes of a map told apart by `type` (`notifiers`, `sources`), by their type."""
    union = get_args(rules.Rules.model_fields[section_name].annotation)[1]
    found = {}
    for model in get_args(get_args(union)[0]):
        (kind,) = get_args(model.model_fields["type"].annotation)
        found[kind] = model
    return found


def channels() -> dict[str, Any]:
    union = get_args(rules.Rules.model_fields["notifiers"].annotation)[1]
    found = {}
    for model in get_args(get_args(union)[0]):
        (kind,) = get_args(model.model_fields["type"].annotation)
        found[kind] = model
    return found


def section(heading: str, guide: str = GUIDE) -> str:
    """The text of one service of a guide, up to the next heading."""
    start = guide.index(f"\n### {heading}\n")
    following = [at for mark in ("\n### ", "\n## ") if (at := guide.find(mark, start + 1)) > 0]
    return guide[start : min(following)]


def test_the_reference_page_is_what_the_schema_says() -> None:
    spec = importlib.util.spec_from_file_location(
        "gen_config_reference", ROOT / "scripts" / "gen_config_reference.py"
    )
    assert spec is not None
    assert spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    page = (ROOT / "docs" / "configuration-reference.md").read_text(encoding="utf-8")
    assert page == generator.render(), "run: uv run python scripts/gen_config_reference.py"


def test_every_key_of_the_rules_file_says_what_it_does() -> None:
    for name in dir(rules):
        model = getattr(rules, name)
        if not (isinstance(model, type) and issubclass(model, rules._Rule)):
            continue
        for key, field in model.model_fields.items():
            if key == "type" and get_origin(field.annotation) is Literal:
                continue
            assert field.description, f"{name}.{key} has no description"


def test_the_guide_lists_the_keys_of_each_service_and_no_other() -> None:
    known = channels()
    assert set(known) == set(SECTION)
    for kind, model in known.items():
        text = section(SECTION[kind])
        table = [line for line in text.splitlines() if line.startswith("| ") and "`" in line]
        listed = {re.findall(r"`([a-z_]+)`", line.split("|")[1])[0] for line in table}
        expected = set(model.model_fields) - {"type"}
        assert listed == expected, f"{kind}: the guide lists {sorted(listed)}"
        for line in table:
            key = re.findall(r"`([a-z_]+)`", line.split("|")[1])[0]
            assert ("**" in line.split("|")[1]) == model.model_fields[key].is_required(), (
                f"{kind}.{key}: bold means required"
            )


def test_every_channel_example_of_the_guide_is_accepted() -> None:
    kinds: set[str] = set()
    for block in re.findall(r"```yaml\n(.*?)```", GUIDE, re.S):
        document = yaml.safe_load(block)
        if "notifiers" not in document:
            document = {"notifiers": document}
        parsed = parse_rules(document)
        kinds |= {channel.type for channel in parsed.notifiers.values()}
    assert kinds == set(SECTION)


def test_the_sources_guide_lists_the_keys_of_each_service_and_no_other() -> None:
    known = kinds_of("sources")
    assert set(known) == set(SOURCE_SECTION)
    for kind, model in known.items():
        text = section(SOURCE_SECTION[kind], SOURCES)
        table = [line for line in text.splitlines() if line.startswith("| ") and "`" in line]
        listed = {re.findall(r"`([a-z_]+)`", line.split("|")[1])[0] for line in table}
        expected = set(model.model_fields) - {"type"}
        assert listed == expected, f"{kind}: the guide lists {sorted(listed)}"
        for line in table:
            key = re.findall(r"`([a-z_]+)`", line.split("|")[1])[0]
            assert ("**" in line.split("|")[1]) == model.model_fields[key].is_required(), (
                f"{kind}.{key}: bold means required"
            )
        assert f"| `{kind}` |" in SOURCES, f"{kind} is not in the table of services"


def test_every_source_example_of_the_guide_is_accepted() -> None:
    kinds: set[str] = set()
    for block in re.findall(r"```yaml\n(.*?)```", SOURCES, re.S):
        document = yaml.safe_load(block)
        if "sources" not in document:
            entries = list(document.values())
            if not all(isinstance(entry, dict) and "type" in entry for entry in entries):
                continue  # the configuration of the service itself, not Paeon's
            document = {"sources": document}
        kinds |= {source.type for source in parse_rules(document).sources.values()}
    assert kinds == set(SOURCE_SECTION)


def test_every_variable_the_code_reads_is_in_the_configuration_guide() -> None:
    read: set[str] = set()
    for path in (ROOT / "src" / "paeon").glob("*.py"):
        read |= set(re.findall(r'"(PAEON_[A-Z_]+)"', path.read_text(encoding="utf-8")))
    guide = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert read, "no variable found: the pattern is wrong"
    for variable in sorted(read):
        assert f"`{variable}`" in guide, f"{variable} is not in docs/configuration.md"
        assert variable in example, f"{variable} is not in .env.example"
