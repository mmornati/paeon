"""Tool calls through both clients, against recorded answers."""

from __future__ import annotations

import json
from typing import Any

import httpx2 as httpx

from paeon.models import (
    AnthropicClient,
    ModelReply,
    OpenAIClient,
    ToolResult,
    ToolSpec,
    ToolTurn,
    UserTurn,
    cost_of,
)
from paeon.rules import ModelAlias
from support import FIXTURES

TOOLS = [
    ToolSpec(
        "inspect_container",
        "State of a container.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    )
]


def replay(name: str, seen: list[dict[str, Any]]) -> httpx.MockTransport:
    body = json.loads((FIXTURES / name).read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def test_anthropic_tool_loop_appends_and_sends_the_turn_back_unchanged() -> None:
    seen: list[dict[str, Any]] = []
    client = AnthropicClient(
        model="claude-opus-5-5",
        api_key="k",
        effort="high",
        max_tokens=8000,
        transport=replay("anthropic_tool_use.json", seen),
    )
    first = client.converse(system="investigate", turns=[UserTurn("{}")], tools=TOOLS)
    assert [(c.id, c.name, c.input) for c in first.tool_calls] == [
        ("toolu_01A09q90qw90lq917835lq9", "inspect_container", {"name": "api"}),
        ("toolu_01B7zs81mc02pd445109mk2", "recent_changes", {"hours": 3}),
    ]
    assert first.text.startswith("The API monitor is down")
    assert (first.input_tokens, first.cached_tokens) == (2220, 0)  # 420 + 1800 written to cache

    results = ToolTurn(
        (
            ToolResult("toolu_01A09q90qw90lq917835lq9", '{"call": "c1"}'),
            ToolResult("toolu_01B7zs81mc02pd445109mk2", "boom", is_error=True),
        )
    )
    client.converse(system="investigate", turns=[UserTurn("{}"), first, results], tools=TOOLS)
    client.close()

    request = seen[0]
    assert request["tools"] == [
        {
            "name": "inspect_container",
            "description": "State of a container.",
            "input_schema": TOOLS[0].input_schema,
        }
    ]
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["output_config"] == {"effort": "high"}
    assert "tool_choice" not in request  # forcing a tool is refused by current models
    assert "thinking" not in request
    assert "temperature" not in request

    user, assistant, tool_results = seen[1]["messages"]
    assert user == seen[0]["messages"][0]  # the history is only appended to
    # The assistant turn goes back whole, thinking block and signature included.
    assert [block["type"] for block in assistant["content"]] == [
        "thinking",
        "text",
        "tool_use",
        "tool_use",
    ]
    assert assistant["content"][0]["signature"] == "c2lnbmF0dXJl"
    # Both results in one user message.
    assert tool_results == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_01A09q90qw90lq917835lq9",
                "content": '{"call": "c1"}',
                "is_error": False,
            },
            {
                "type": "tool_result",
                "tool_use_id": "toolu_01B7zs81mc02pd445109mk2",
                "content": "boom",
                "is_error": True,
            },
        ],
    }


def test_anthropic_without_tools_sends_neither_tools_nor_cache_control() -> None:
    seen: list[dict[str, Any]] = []
    client = AnthropicClient(model="m", transport=replay("anthropic_message.json", seen))
    client.converse(system="s", turns=[UserTurn("u")])
    assert not {"tools", "cache_control", "output_config"} & seen[0].keys()


def test_openai_tool_calls_and_results() -> None:
    seen: list[dict[str, Any]] = []
    client = OpenAIClient(
        model="auto",
        base_url="http://gateway:8080/v1",
        transport=replay("openai_tool_calls.json", seen),
    )
    first = client.converse(system="investigate", turns=[UserTurn("{}")], tools=TOOLS)
    good, bad = first.tool_calls
    assert (good.id, good.name, good.input, good.error) == (
        "call_a1",
        "container_logs",
        {"name": "api", "tail": 50},
        None,
    )
    assert (bad.name, bad.input) == ("recent_changes", {})
    assert bad.error == "arguments are not a JSON object"
    assert first.model == "strong-model-2026-09"

    turn = ToolTurn((ToolResult("call_a1", "lines"), ToolResult("call_a2", "bad", is_error=True)))
    client.converse(system="investigate", turns=[UserTurn("{}"), first, turn], tools=TOOLS)
    client.close()

    assert seen[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "inspect_container",
                "description": "State of a container.",
                "parameters": TOOLS[0].input_schema,
            },
        }
    ]
    system, user, assistant, result_1, result_2 = seen[1]["messages"]
    assert (system["role"], user["role"]) == ("system", "user")
    assert assistant["role"] == "assistant"
    assert [call["id"] for call in assistant["tool_calls"]] == ["call_a1", "call_a2"]
    assert result_1 == {"role": "tool", "tool_call_id": "call_a1", "content": "lines"}
    assert result_2["tool_call_id"] == "call_a2"


def test_cost_counts_cached_input_at_its_own_price() -> None:
    reply = ModelReply(
        text="x", model="m", input_tokens=10_000, output_tokens=1_000, cached_tokens=8_000
    )
    plain = ModelAlias.model_validate(
        {
            "backend": "anthropic",
            "model": "m",
            "price": {"input_per_mtok": 4, "output_per_mtok": 20},
        }
    )
    cached = ModelAlias.model_validate(
        {
            "backend": "anthropic",
            "model": "m",
            "price": {"input_per_mtok": 4, "output_per_mtok": 20, "cached_input_per_mtok": 0.2},
        }
    )
    assert cost_of(reply, plain) == 0.06  # 10k at 4 + 1k at 20, per million
    assert cost_of(reply, cached) == 0.0296  # 2k at 4 + 8k at 0.2 + 1k at 20
