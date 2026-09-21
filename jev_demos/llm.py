import asyncio
import json
import os
import random
import time
from typing import Any, Literal, TypeVar

import openai
from pydantic import BaseModel

from .config import (
    MODELS,
    PROVIDERS,
    account_error,
    budget,
    cost_usd,
    mark_down,
    usable,
)
from .keys import key_for

BASE_URL = os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
Effort = Literal["low", "medium", "high"]
T = TypeVar("T", bound=BaseModel)

_clients: dict[str, openai.AsyncOpenAI] = {}


def _client() -> openai.AsyncOpenAI:
    """One client per key (never shared across visitors); bounded so a public host can't grow it
    forever. Both providers ride on the single OpenRouter key."""
    api_key = key_for("openrouter")
    if not api_key:
        raise RuntimeError("No OpenRouter key")
    c = _clients.get(api_key)
    if c is None:
        if len(_clients) >= 50:
            _clients.clear()
        c = openai.AsyncOpenAI(api_key=api_key, base_url=BASE_URL, timeout=90)
        _clients[api_key] = c
    return c


def _reasoning(model: str, effort: Effort) -> dict[str, Any] | None:
    """OpenRouter `reasoning` body field per model: K2.6 needs thinking off, K3 takes an effort
    level, Haiku takes none, Sonnet/Opus take the requested effort."""
    if model == "kimiK26":
        return {"enabled": False}
    if model == "kimiK3":
        return {"effort": "high" if effort == "high" else "low"}
    if model == "haiku":
        return None
    return {"effort": effort}


Completion = dict[str, Any]


def _jitter(base: float) -> float:
    return base * (0.85 + random.random() * 0.3) / 1000


# Rough offline stand-ins used only when a provider is unavailable. Always flagged `simulated`.
SIM: dict[str, dict[str, float]] = {
    "haiku": {"latency": 800, "outTok": 140},
    "sonnet": {"latency": 2200, "outTok": 380},
    "opus": {"latency": 3800, "outTok": 520},
    "kimiK26": {"latency": 900, "outTok": 160},
    "kimiK3": {"latency": 4500, "outTok": 420},
}


async def _chat(model: str, system: str | None, prompt: str, max_tokens: int, effort: Effort = "low") -> dict[str, Any]:
    info = MODELS[model]
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    reasoning = _reasoning(model, effort)
    r = await _client().chat.completions.create(
        model=info["id"],
        max_tokens=max_tokens,
        messages=messages,
        extra_body={"reasoning": reasoning} if reasoning is not None else {},
    )
    return {
        "text": (r.choices[0].message.content or "").strip() if r.choices else "",
        "inputTokens": r.usage.prompt_tokens if r.usage else 0,
        "outputTokens": r.usage.completion_tokens if r.usage else 0,
    }


async def complete(model: str, prompt: str, system: str | None = None, max_tokens: int | None = None, effort: Effort = "low") -> Completion:
    info = MODELS[model]
    started = time.perf_counter()
    if max_tokens is None:
        max_tokens = 1000 if (info["provider"] == "kimi" and model == "kimiK3") else 500

    async def simulate() -> Completion:
        sim = SIM[model]
        input_tokens = -(-len(prompt) // 4) + 20
        await asyncio.sleep(_jitter(sim["latency"]))
        return {
            "model": model,
            "text": f"[Simulated {info['label']} response: this provider is unavailable, so this is a placeholder.]",
            "inputTokens": input_tokens,
            "outputTokens": int(sim["outTok"]),
            "latencyMs": round((time.perf_counter() - started) * 1000),
            "costUsd": cost_usd(info, input_tokens, sim["outTok"]),
            "simulated": True,
        }

    if not usable(info["provider"]):
        return await simulate()
    try:
        r = await _chat(model, system, prompt, max_tokens, effort)
        text = r["text"]
        if info["provider"] == "kimi" and not text:
            text = "[The model spent its token budget on reasoning before answering. Raise max tokens or lower the effort.]"
        input_tokens, output_tokens = r["inputTokens"], r["outputTokens"]
    except Exception as err:
        reason = account_error(err)
        if not reason:
            raise
        mark_down(info["provider"], reason)
        return await simulate()
    cost = cost_usd(info, input_tokens, output_tokens)
    budget.charge(cost, "openrouter")
    return {
        "model": model,
        "text": text,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "latencyMs": round((time.perf_counter() - started) * 1000),
        "costUsd": cost,
        "simulated": False,
    }


async def parse_with(model: str, schema: type[T], prompt: str, system: str) -> dict[str, Any]:
    """Structured-output call: the "LLM does the classification" baseline. Raises if the provider
    can't answer."""
    info = MODELS[model]
    if not usable(info["provider"]):
        raise RuntimeError(f"{info['label']} is unavailable")
    started = time.perf_counter()
    schema_json = json.dumps(schema.model_json_schema())
    sys_prompt = f"{system}\nRespond with a single JSON object matching this JSON Schema, and nothing else:\n{schema_json}"
    try:
        r = await _client().chat.completions.create(
            model=info["id"],
            max_tokens=400 if info["provider"] == "kimi" else 500,
            messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": prompt}],
            response_format={"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": True}},
        )
        value = schema.model_validate_json(r.choices[0].message.content or "")
        input_tokens = r.usage.prompt_tokens if r.usage else 0
        output_tokens = r.usage.completion_tokens if r.usage else 0
    except Exception as err:
        reason = account_error(err)
        if reason:
            mark_down(info["provider"], reason)
        raise
    cost = cost_usd(info, input_tokens, output_tokens)
    budget.charge(cost, "openrouter")
    return {"value": value, "inputTokens": input_tokens, "outputTokens": output_tokens, "latencyMs": round((time.perf_counter() - started) * 1000), "costUsd": cost}


async def probe_llm(p: str) -> None:
    """Cheapest possible real call, used to validate a key. Raises the provider's error on failure."""
    await _chat(PROVIDERS[p]["small"], None, "hi", 1)
