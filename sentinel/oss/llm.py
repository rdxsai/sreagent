"""OpenAI-compatible call primitives (OpenRouter / gpt-oss), with the same rate
limiting + backoff the Anthropic loop uses. Two entry points: chat() for a tool-use
turn, and structured() for a schema-constrained JSON answer (manager plan/synthesize),
which prompts for JSON and validates with pydantic, retrying on malformed output, so
it works on any OpenAI-compatible provider without relying on response_format support.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

import httpx
import openai
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from sentinel.agent.ratelimit import RateLimiter
from sentinel.providers import ModelPreset

_LIMITER = RateLimiter(
    rate_per_sec=float(os.environ.get("SENTINEL_API_RPS", "3")),
    burst=int(os.environ.get("SENTINEL_API_BURST", "3")),
)
_CONCURRENCY = threading.Semaphore(int(os.environ.get("SENTINEL_API_CONCURRENCY", "4")))

_RETRYABLE = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
    # raw transport drops that the SDK (max_retries=0) lets through, common under
    # parallel load on OpenRouter
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
)


@retry(
    retry=retry_if_exception_type(_RETRYABLE),
    wait=wait_exponential(multiplier=1, max=30),
    stop=stop_after_attempt(6),
    reraise=True,
)
def _create(client: openai.OpenAI, **kwargs: Any) -> Any:
    _LIMITER.acquire()
    with _CONCURRENCY:
        return client.chat.completions.create(**kwargs)


def reasoning_of(message: Any) -> str:
    """gpt-oss reasoning arrives out-of-band on the message; capture it for the trace.
    OpenRouter may expose it as .reasoning, in model_extra, or as reasoning_details."""
    for attr in ("reasoning",):
        v = getattr(message, attr, None)
        if isinstance(v, str) and v:
            return v
    extra = getattr(message, "model_extra", None) or {}
    v = extra.get("reasoning")
    if isinstance(v, str) and v:
        return v
    details = getattr(message, "reasoning_details", None) or extra.get("reasoning_details")
    if isinstance(details, list):
        return "\n".join(str(d.get("text", d)) if isinstance(d, dict) else str(d) for d in details)
    return ""


def usage_of(resp: Any) -> dict[str, int]:
    u = getattr(resp, "usage", None)
    return {
        "input": getattr(u, "prompt_tokens", 0) or 0,
        "output": getattr(u, "completion_tokens", 0) or 0,
    }


def chat(
    client: openai.OpenAI,
    preset: ModelPreset,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    tool_choice: str | None = None,
    max_tokens: int | None = None,
    effort: str | None = None,
) -> Any:
    kw = preset.body(max_tokens=max_tokens, effort=effort)
    if tools is not None:
        kw["tools"] = tools
        if tool_choice is not None:
            kw["tool_choice"] = tool_choice
    return _create(client, messages=messages, **kw)


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def structured(
    client: openai.OpenAI,
    preset: ModelPreset,
    messages: list[dict],
    schema: type[BaseModel],
    *,
    max_tokens: int | None = None,
    effort: str | None = None,
    retries: int = 2,
) -> tuple[BaseModel, dict[str, int], str]:
    """Return a validated schema instance, usage, and the model's reasoning text.
    Reprompts on malformed JSON."""
    contract = {
        "role": "user",
        "content": (
            "Reply with ONLY a single JSON object, no prose, no code fence, matching "
            f"this JSON schema:\n{json.dumps(schema.model_json_schema())}"
        ),
    }
    msgs = list(messages) + [contract]
    usage: dict[str, int] = {"input": 0, "output": 0}
    last_err: Exception | None = None
    for _ in range(retries + 1):
        resp = _create(client, messages=msgs, **preset.body(max_tokens=max_tokens, effort=effort))
        u = usage_of(resp)
        usage["input"] += u["input"]
        usage["output"] += u["output"]
        msg = resp.choices[0].message
        text = msg.content or ""
        try:
            return schema.model_validate(json.loads(_extract_json(text))), usage, reasoning_of(msg)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_err = exc
            msgs = msgs + [
                {"role": "assistant", "content": text[:2000]},
                {"role": "user", "content": f"That was not valid ({str(exc)[:200]}). Reply with ONLY the JSON object."},
            ]
    raise RuntimeError(f"structured output failed after retries: {last_err}")
