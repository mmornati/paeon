"""Model clients: the Anthropic Messages API and the OpenAI-compatible chat API, nothing else.

Each takes a base URL, so a gateway or a local server works without code. The Anthropic backend
uses the official SDK; the OpenAI-compatible one is plain HTTP, because what it talks to is as
often a gateway or a local server as OpenAI itself.

A conversation is a list of turns in a neutral form (`UserTurn`, the model's own `ModelReply`,
`ToolTurn`), which each client renders in its wire format. The assistant's turns are sent back
exactly as they were received (`ModelReply.raw`): the history is only ever appended to, which is
what models that keep their reasoning between tool calls require. A reply carries the model that
actually answered (a gateway may choose it) and the token counts, for the cost ledger.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

import anthropic
import httpx2 as httpx
from anthropic.types import MessageParam, ToolParam

from paeon.rules import ModelAlias

ANTHROPIC_URL = "https://api.anthropic.com"
OPENAI_URL = "https://api.openai.com/v1"


class ModelError(Exception):
    """The model could not be reached, refused, or did not answer in the expected shape."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    error: str | None = None  # the arguments could not be read


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class UserTurn:
    text: str


@dataclass(frozen=True)
class ToolTurn:
    results: tuple[ToolResult, ...]


@dataclass(frozen=True)
class ModelReply:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    tool_calls: tuple[ToolCall, ...] = ()
    truncated: bool = False  # stopped at max_tokens
    raw: Any = None  # the assistant turn in the backend's own format, to send back unchanged


Turn = UserTurn | ModelReply | ToolTurn


class ModelClient(Protocol):
    def converse(
        self, *, system: str, turns: Sequence[Turn], tools: Sequence[ToolSpec] = ()
    ) -> ModelReply: ...


def complete(client: ModelClient, *, system: str, user: str) -> ModelReply:
    """One question, one answer in text, no tools."""
    reply = client.converse(system=system, turns=[UserTurn(user)])
    if not reply.text:
        raise ModelError("the answer has no text")
    return reply


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


class AnthropicClient:
    """The Messages API, through the official SDK."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str = ANTHROPIC_URL,
        max_tokens: int = 2000,
        timeout: float = 60,
        effort: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # Everything is passed explicitly: the SDK would otherwise read ANTHROPIC_* variables.
        self._client = anthropic.Anthropic(
            api_key=api_key or "none",  # a gateway without authentication still needs a value
            base_url=base_url,
            timeout=timeout,
            max_retries=1,
            http_client=None if transport is None else httpx.Client(transport=transport),
        )
        self._model = model
        self._max_tokens = max_tokens
        self._effort = effort

    def converse(
        self, *, system: str, turns: Sequence[Turn], tools: Sequence[ToolSpec] = ()
    ) -> ModelReply:
        messages: list[MessageParam] = []
        for turn in turns:
            if isinstance(turn, UserTurn):
                messages.append({"role": "user", "content": turn.text})
            elif isinstance(turn, ModelReply):
                messages.append({"role": "assistant", "content": turn.raw})
            else:
                # Every result of one assistant turn goes back in a single message.
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": result.call_id,
                                "content": result.content,
                                "is_error": result.is_error,
                            }
                            for result in turn.results
                        ],
                    }
                )
        extra: dict[str, Any] = {}
        if tools:
            extra["tools"] = [
                cast(
                    ToolParam,
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.input_schema,
                    },
                )
                for tool in tools
            ]
            # A tool loop resends its whole history each turn: let the API cache the prefix.
            extra["cache_control"] = {"type": "ephemeral"}
        if self._effort:
            extra["output_config"] = {"effort": self._effort}
        # Errors are reduced to their kind or status: the request carries a key and the prompt.
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=messages,
                **extra,
            )
        except anthropic.APIStatusError as exc:
            raise ModelError(f"HTTP {exc.status_code}") from None
        except anthropic.APIError as exc:  # connection errors, timeouts, unreadable answers
            raise ModelError(f"request failed: {type(exc).__name__}") from None
        # A gateway can answer 200 with something that is not a message: the SDK does not check.
        try:
            return self._reply(response)
        except (AttributeError, TypeError, ValueError):
            raise ModelError("the answer is not a message") from None

    def _reply(self, response: Any) -> ModelReply:
        if response.stop_reason == "refusal":
            raise ModelError("the model declined the request")
        text = "".join(block.text for block in response.content if block.type == "text")
        calls = tuple(
            ToolCall(id=block.id, name=block.name, input=dict(block.input))
            for block in response.content
            if block.type == "tool_use"
        )
        usage = response.usage
        cached = _count(usage.cache_read_input_tokens)
        return ModelReply(
            text=text,
            model=response.model or self._model,
            # `input_tokens` excludes what was read from or written to the cache.
            input_tokens=_count(usage.input_tokens)
            + _count(usage.cache_creation_input_tokens)
            + cached,
            output_tokens=_count(usage.output_tokens),
            cached_tokens=cached,
            tool_calls=calls,
            truncated=response.stop_reason == "max_tokens",
            raw=response.content,
        )

    def close(self) -> None:
        self._client.close()


class OpenAIClient:
    """`POST {base_url}/chat/completions`, as served by OpenAI and by compatible gateways."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str = OPENAI_URL,
        max_tokens: int = 2000,
        timeout: float = 60,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )
        self._model = model
        self._max_tokens = max_tokens

    def converse(
        self, *, system: str, turns: Sequence[Turn], tools: Sequence[ToolSpec] = ()
    ) -> ModelReply:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for turn in turns:
            if isinstance(turn, UserTurn):
                messages.append({"role": "user", "content": turn.text})
            elif isinstance(turn, ModelReply):
                messages.append(turn.raw)
            else:
                messages.extend(
                    {"role": "tool", "tool_call_id": result.call_id, "content": result.content}
                    for result in turn.results
                )
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]
        data = self._post("/chat/completions", body)
        choices = data.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        if not isinstance(message, dict):
            raise ModelError("the answer has no message")
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        calls = tuple(_openai_calls(message.get("tool_calls")))
        if not text and not calls:
            raise ModelError("the answer has no text")
        raw_usage = data.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        details = usage.get("prompt_tokens_details")
        return ModelReply(
            text=text,
            model=str(data.get("model") or self._model),
            input_tokens=_count(usage.get("prompt_tokens")),
            output_tokens=_count(usage.get("completion_tokens")),
            cached_tokens=_count(details.get("cached_tokens")) if isinstance(details, dict) else 0,
            tool_calls=calls,
            truncated=isinstance(first, dict) and first.get("finish_reason") == "length",
            raw={"role": "assistant", **{k: v for k, v in message.items() if k != "role"}},
        )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._http.post(path, json=body)
        except httpx.HTTPError as exc:
            raise ModelError(f"request failed: {type(exc).__name__}") from None
        if response.status_code >= 400:
            raise ModelError(f"HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise ModelError("the answer is not JSON") from None
        if not isinstance(data, dict):
            raise ModelError("the answer is not a JSON object")
        return data

    def close(self) -> None:
        self._http.close()


def _openai_calls(raw: Any) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for entry in raw if isinstance(raw, list) else []:
        function = entry.get("function") if isinstance(entry, dict) else None
        if not isinstance(function, dict):
            continue
        call_id, name = str(entry.get("id") or ""), str(function.get("name") or "")
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except (TypeError, ValueError):
            arguments = None
        if isinstance(arguments, dict):
            calls.append(ToolCall(id=call_id, name=name, input=arguments))
        else:
            calls.append(
                ToolCall(id=call_id, name=name, input={}, error="arguments are not a JSON object")
            )
    return calls


def build_client(
    alias: ModelAlias, api_key: str = "", *, transport: httpx.BaseTransport | None = None
) -> AnthropicClient | OpenAIClient:
    if alias.backend == "anthropic":
        return AnthropicClient(
            model=alias.model,
            api_key=api_key,
            base_url=alias.base_url or ANTHROPIC_URL,
            max_tokens=alias.max_tokens,
            timeout=alias.timeout_seconds,
            effort=alias.effort,
            transport=transport,
        )
    return OpenAIClient(
        model=alias.model,
        api_key=api_key,
        base_url=alias.base_url or OPENAI_URL,
        max_tokens=alias.max_tokens,
        timeout=alias.timeout_seconds,
        transport=transport,
    )


def cost_of(reply: ModelReply, alias: ModelAlias) -> float:
    """What a reply cost, at the alias's prices. Cache writes are counted as plain input."""
    price = alias.price
    cached_price = (
        price.input_per_mtok if price.cached_input_per_mtok is None else price.cached_input_per_mtok
    )
    fresh = max(reply.input_tokens - reply.cached_tokens, 0)
    total = (
        fresh * price.input_per_mtok
        + reply.cached_tokens * cached_price
        + reply.output_tokens * price.output_per_mtok
    )
    return round(total / 1_000_000, 6)
