from __future__ import annotations

import pytest

from paeon.redact import MASK, redact, redact_text

# Built at run time so that no secret-shaped literal sits in the repository.
JWT = ".".join(["eyJ" + "a" * 12, "b" * 16, "c" * 16])
GITHUB = "ghp_" + "A1b2" * 6
PROVIDER = "sk-" + "x9" * 12


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("GET http://admin:hunter2pass@db:5432/ failed", "hunter2pass"),
        ("Authorization: Bearer abcdef0123456789", "abcdef0123456789"),
        ("auth header was Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
        ("login failed with password=s3cr3t-value", "s3cr3t-value"),
        ('{"api_key": "abc123def456"}', "abc123def456"),
        ("https://push.example.org/hook?token=q1w2e3r4&x=1", "q1w2e3r4"),
        ("DB_PASSWORD: correct-horse", "correct-horse"),
        (f"jwt {JWT} rejected", JWT),
        (f"pushed with {GITHUB}", GITHUB),
        (f"key {PROVIDER} is over quota", PROVIDER),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----", "MIIB"),
    ],
)
def test_secret_shapes_are_masked(text: str, secret: str) -> None:
    cleaned = redact_text(text)
    assert secret not in cleaned
    assert MASK in cleaned


def test_ordinary_alert_text_is_untouched() -> None:
    text = "connect ECONNREFUSED 10.0.0.5:8096 after 3 retries; token bucket empty"
    assert redact_text(text) == text


def test_long_text_is_cut() -> None:
    cleaned = redact_text("x" * 50, max_length=10)
    assert cleaned.startswith("x" * 10 + "…")
    assert "40 characters cut" in cleaned


def test_nested_values_and_secret_named_keys() -> None:
    cleaned = redact(
        {
            "labels": {"host": "server-1", "auth_token": "plain-value"},
            "items": ["password=abc12345", 3, None],
            "count": 2,
        }
    )
    assert cleaned == {
        "labels": {"host": "server-1", "auth_token": MASK},
        "items": [f"password={MASK}", 3, None],
        "count": 2,
    }
