"""The two model clients, against recorded answers. No request leaves the machine."""

from __future__ import annotations

import json
from typing import Any

import httpx2 as httpx
import pytest

from paeon.models import (
    AnthropicClient,
    ModelError,
    OpenAIClient,
    build_client,
    complete,
)
from paeon.rules import ModelAlias
from support import FIXTURES

KEY = "not-a-real-key"


def recorded(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return data


def replay(body: Any, seen: list[httpx.Request], status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def test_anthropic_messages_request_and_reply() -> None:
    seen: list[httpx.Request] = []
    client = AnthropicClient(
        model="claude-haiku-5-5",
        api_key=KEY,
        max_tokens=300,
        transport=replay(recorded("anthropic_message.json"), seen),
    )
    reply = complete(client, system="be brief", user="{}")
    client.close()

    [request] = seen
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == KEY
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert json.loads(request.content) == {
        "model": "claude-haiku-5-5",
        "max_tokens": 300,
        "system": "be brief",
        "messages": [{"role": "user", "content": "{}"}],
    }
    assert reply.model == "claude-haiku-5-5"
    assert reply.text.startswith('{"verdict": "noise"')
    # 812 uncached + 300 read from the cache
    assert (reply.input_tokens, reply.cached_tokens, reply.output_tokens) == (1112, 300, 64)


def test_openai_compatible_request_and_reply_through_a_gateway() -> None:
    seen: list[httpx.Request] = []
    client = OpenAIClient(
        model="auto",
        api_key=KEY,
        base_url="http://gateway:8080/v1/",
        transport=replay(recorded("openai_chat_completion.json"), seen),
    )
    reply = complete(client, system="be brief", user="{}")
    client.close()

    [request] = seen
    assert str(request.url) == "http://gateway:8080/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["model"] == "auto"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    # The gateway chose the model: the reply says which one answered.
    assert reply.model == "small-fast-model-2026-10"
    assert (reply.input_tokens, reply.cached_tokens, reply.output_tokens) == (1040, 512, 58)


def test_no_key_sends_no_credential() -> None:
    seen: list[httpx.Request] = []
    complete(
        OpenAIClient(model="m", transport=replay(recorded("openai_chat_completion.json"), seen)),
        system="s",
        user="u",
    )
    complete(
        AnthropicClient(model="m", transport=replay(recorded("anthropic_message.json"), seen)),
        system="s",
        user="u",
    )
    assert "authorization" not in seen[0].headers
    assert seen[1].headers["x-api-key"] == "none"  # the SDK wants a value; a gateway ignores it


def test_anthropic_client_ignores_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-environment")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://elsewhere:1")
    seen: list[httpx.Request] = []
    client = AnthropicClient(
        model="m", api_key=KEY, transport=replay(recorded("anthropic_message.json"), seen)
    )
    complete(client, system="s", user="u")
    assert seen[0].headers["x-api-key"] == KEY
    assert seen[0].url.host == "api.anthropic.com"


@pytest.mark.parametrize(
    ("body", "status", "message"),
    [
        ({"error": {"type": "overloaded_error", "message": f"key {KEY}"}}, 529, "HTTP 529"),
        ("<html>bad gateway</html>", 200, "not a message"),
        ([1, 2], 200, "not a message"),
        ({"content": "text"}, 200, "not a message"),
        ({"content": [], "usage": {}, "model": "m", "stop_reason": "end_turn"}, 200, "no text"),
        (
            {"content": [], "usage": {}, "model": "m", "stop_reason": "refusal"},
            200,
            "declined the request",
        ),
    ],
)
def test_anthropic_errors_are_model_errors(body: Any, status: int, message: str) -> None:
    client = AnthropicClient(model="m", api_key=KEY, transport=replay(body, [], status))
    with pytest.raises(ModelError, match=message) as caught:
        complete(client, system="s", user="u")
    assert KEY not in str(caught.value)


@pytest.mark.parametrize(
    "body", [{"choices": []}, {"choices": [{"message": {"content": None}}]}, {"choices": "x"}]
)
def test_openai_answers_without_text_are_model_errors(body: Any) -> None:
    client = OpenAIClient(model="m", transport=replay(body, []))
    with pytest.raises(ModelError, match=r"no text|no message"):
        complete(client, system="s", user="u")


def test_unreachable_model_is_a_model_error_without_the_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = OpenAIClient(
        model="m", base_url="http://gateway:8080/v1", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ModelError, match="ConnectError") as caught:
        complete(client, system="s", user="u")
    assert "gateway" not in str(caught.value)


def test_missing_usage_counts_as_zero() -> None:
    body = {"choices": [{"message": {"content": "ok"}}]}
    reply = complete(OpenAIClient(model="m", transport=replay(body, [])), system="s", user="u")
    assert (reply.model, reply.input_tokens, reply.output_tokens) == ("m", 0, 0)


def test_build_client_from_an_alias() -> None:
    anthropic = build_client(ModelAlias(backend="anthropic", model="claude-haiku-5-5"), KEY)
    openai = build_client(
        ModelAlias(backend="openai", model="auto", base_url="http://gateway:8080/v1")
    )
    assert isinstance(anthropic, AnthropicClient)
    assert isinstance(openai, OpenAIClient)
    anthropic.close()
    openai.close()
