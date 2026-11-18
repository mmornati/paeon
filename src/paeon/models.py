"""Model clients: the Anthropic Messages API and the OpenAI-compatible chat API, nothing else.

Each takes a base URL, so a gateway or a local server works without code. A call is one system
prompt and one user message in, text out: no tools, no streaming. The reply carries the model
that actually answered (a gateway may choose it) and the token counts, for the cost ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx2 as httpx

from paeon.rules import ModelAlias

ANTHROPIC_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_URL = "https://api.openai.com/v1"


class ModelError(Exception):
    """The model could not be reached or did not answer in the expected shape."""


@dataclass(frozen=True)
class ModelReply:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0


class ModelClient(Protocol):
    def complete(self, *, system: str, user: str) -> ModelReply: ...


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


class _HttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        headers: dict[str, str],
        timeout: float,
        transport: httpx.BaseTransport | None,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        # Errors are reduced to their kind or status: the request carries a key and the prompt.
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


class AnthropicClient(_HttpClient):
    """`POST {base_url}/v1/messages`."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str = ANTHROPIC_URL,
        max_tokens: int = 600,
        timeout: float = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"anthropic-version": ANTHROPIC_VERSION}
        if api_key:
            headers["x-api-key"] = api_key
        super().__init__(base_url=base_url, headers=headers, timeout=timeout, transport=transport)
        self._model = model
        self._max_tokens = max_tokens

    def complete(self, *, system: str, user: str) -> ModelReply:
        data = self._post(
            "/v1/messages",
            {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise ModelError("the answer has no content")
        text = "".join(
            block["text"]
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        if not text:
            raise ModelError("the answer has no text")
        raw_usage = data.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        cached = _count(usage.get("cache_read_input_tokens"))
        return ModelReply(
            text=text,
            model=str(data.get("model") or self._model),
            # `input_tokens` excludes what was read from or written to the cache.
            input_tokens=_count(usage.get("input_tokens"))
            + _count(usage.get("cache_creation_input_tokens"))
            + cached,
            output_tokens=_count(usage.get("output_tokens")),
            cached_tokens=cached,
        )


class OpenAIClient(_HttpClient):
    """`POST {base_url}/chat/completions`, as served by OpenAI and by compatible gateways."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str = OPENAI_URL,
        max_tokens: int = 600,
        timeout: float = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        super().__init__(base_url=base_url, headers=headers, timeout=timeout, transport=transport)
        self._model = model
        self._max_tokens = max_tokens

    def complete(self, *, system: str, user: str) -> ModelReply:
        data = self._post(
            "/chat/completions",
            {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        choices = data.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text:
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
        )


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
