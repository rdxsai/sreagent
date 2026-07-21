"""Frozen model interface: one OpenAI-compatible client with named presets.

Switchable by the SENTINEL_MODEL env var (or resolve(name)). Keys come from the
environment only, never a file. Presets bake in the knobs that matter for a
weaker model on a metered provider: a small max_tokens (the thing that trips a
402 on OpenRouter when left unbounded), reasoning effort, and per-provider
routing so scores are reproducible run to run.

Verified 2026-07-17: gpt-oss-120b's OpenRouter slug is `openai/gpt-oss-120b`;
`:exacto` is OpenRouter's highest-tool-accuracy routing mode; reasoning effort is
the `reasoning={"effort": ...}` body param. gpt-oss is the primary target; the
deepseek/qwen presets are here for switching and may need their slug confirmed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from openai import OpenAI

_OPENROUTER = "https://openrouter.ai/api/v1"
_OLLAMA = "http://localhost:11434/v1"


@dataclass(frozen=True)
class ModelPreset:
    name: str
    base_url: str
    model: str
    api_key_env: str
    max_tokens: int = 4096
    effort: str = "medium"          # low | medium | high (reasoning effort)
    provider_order: tuple[str, ...] = ()   # pin OpenRouter provider(s) for reproducibility
    extra_body: dict = field(default_factory=dict)   # local knobs (num_ctx, keep_alive, ...)

    def api_key(self) -> str:
        candidates = [self.api_key_env]
        if "openrouter" in self.base_url:
            candidates += ["OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY"]
        for name in candidates:
            key = os.environ.get(name, "")
            if key:
                return key
        raise RuntimeError(f"none of {candidates} is set; export the key (do not commit it).")

    def client(self) -> OpenAI:
        return OpenAI(
            base_url=self.base_url,
            api_key=self.api_key(),
            default_headers={"X-Title": "sentinel-sre"},
            max_retries=0,   # the loop owns retry/backoff + rate limiting
            # cap each request so a slow provider fails-fast into the loop's retry
            # rather than blocking on the SDK's 600s default (seen hanging on live NR runs)
            timeout=float(os.environ.get("SENTINEL_LLM_TIMEOUT_S", "90")),
        )

    def body(self, *, max_tokens: int | None = None, effort: str | None = None) -> dict:
        """Per-call extra kwargs for chat.completions.create."""
        body: dict = {"reasoning": {"effort": effort or self.effort}}
        if self.provider_order:
            body["provider"] = {"order": list(self.provider_order), "allow_fallbacks": False}
        body.update(self.extra_body)
        return {"model": self.model, "max_tokens": max_tokens or self.max_tokens, "extra_body": body}


# Provider pin for gpt-oss reproducibility: set SENTINEL_OR_PROVIDER=<slug[,slug]>
# (from the model's OpenRouter providers page) to lock routing; empty = let :exacto route.
_PIN = tuple(p for p in os.environ.get("SENTINEL_OR_PROVIDER", "").split(",") if p)
_EFFORT = os.environ.get("SENTINEL_EFFORT", "medium")   # low | medium | high

PRESETS: dict[str, ModelPreset] = {
    "gpt-oss-120b": ModelPreset(
        name="gpt-oss-120b",
        base_url=_OPENROUTER,
        model="openai/gpt-oss-120b:exacto",
        api_key_env="OPEN_ROUTER_API_KEY",
        max_tokens=4096,
        effort=_EFFORT,
        provider_order=_PIN,
    ),
    "deepseek": ModelPreset(   # confirm the exact slug on OpenRouter before a scored run
        name="deepseek",
        base_url=_OPENROUTER,
        model=os.environ.get("SENTINEL_DEEPSEEK_SLUG", "deepseek/deepseek-chat"),
        api_key_env="OPEN_ROUTER_API_KEY",
        max_tokens=4096,
        effort=_EFFORT,
        provider_order=_PIN,
    ),
    "qwen3-14b": ModelPreset(   # local via Ollama's OpenAI-compatible endpoint
        name="qwen3-14b",
        base_url=_OLLAMA,
        model="qwen3:14b",
        api_key_env="OLLAMA_API_KEY",   # any non-empty value; set OLLAMA_API_KEY=ollama
        max_tokens=4096,
        effort=_EFFORT,
        extra_body={"num_ctx": 32768, "keep_alive": "30m"},
    ),
}

DEFAULT_PRESET = "gpt-oss-120b"


def resolve(name: str | None = None) -> ModelPreset:
    key = name or os.environ.get("SENTINEL_MODEL", DEFAULT_PRESET)
    if key not in PRESETS:
        raise KeyError(f"unknown model preset {key!r}; choose from {sorted(PRESETS)}")
    return PRESETS[key]


@lru_cache(maxsize=8)
def client_for(name: str | None = None) -> tuple[OpenAI, ModelPreset]:
    preset = resolve(name)
    return preset.client(), preset


def smoke_test(name: str | None = None) -> dict:
    """One tool-use round-trip. Run once per preset after the key is set."""
    client, preset = client_for(name)
    tools = [{
        "type": "function",
        "function": {
            "name": "add", "description": "Add two integers.",
            "parameters": {"type": "object",
                           "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                           "required": ["a", "b"]},
        },
    }]
    resp = client.chat.completions.create(
        messages=[{"role": "user", "content": "Call add with a=2 and b=3. Use the tool."}],
        tools=tools, tool_choice="auto", **preset.body(max_tokens=512),
    )
    msg = resp.choices[0].message
    calls = msg.tool_calls or []
    return {
        "preset": preset.name,
        "model": preset.model,
        "tool_called": bool(calls),
        "tool_name": calls[0].function.name if calls else None,
        "arguments": calls[0].function.arguments if calls else None,
        "finish_reason": resp.choices[0].finish_reason,
    }
