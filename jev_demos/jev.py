import asyncio
import json
import random
import time
from collections.abc import Callable, Mapping
from typing import Any

from typesafe_sdk import AsyncTypeSafeClient, Noul, Question

from .config import account_error, budget, jev_cost_usd
from .keys import bucket, key_for

Timed = dict[str, Any]

_clients: dict[str, AsyncTypeSafeClient] = {}


def _client_for(api_key: str) -> AsyncTypeSafeClient:
    """One client per key (never shared across visitors); bounded so a public host can't grow it
    forever."""
    c = _clients.get(api_key)
    if c is None:
        if len(_clients) >= 50:
            _clients.clear()
        c = AsyncTypeSafeClient(api_key=api_key)
        _clients[api_key] = c
    return c


async def probe_jev() -> None:
    """Cheapest possible real call, used to validate a key. Raises the provider's error on failure."""
    key = key_for("jev")
    if not key:
        raise RuntimeError("No Jev key")
    await _client_for(key).system_one("ping", {"ok": Noul(instructions="Is this a greeting?")})


# Availability: a rejected key marks Jev down for a cool-down (per key), and the offline heuristic
# answers instead.
_jev_down: dict[str, dict[str, Any]] = {}
COOL_DOWN_S = 10 * 60


def jev_state() -> dict[str, str]:
    if not key_for("jev"):
        return {"state": "simulated", "note": "No API key set"}
    d = _jev_down.get(bucket("jev"))
    if d and time.time() < d["until"]:
        return {"state": "unavailable", "note": str(d["reason"])}
    return {"state": "live", "note": ""}


def reset_jev_down() -> None:
    _jev_down.pop(bucket("jev"), None)


def mark_jev_down(reason: str) -> None:
    _jev_down[bucket("jev")] = {"until": time.time() + COOL_DOWN_S, "reason": reason}


async def jev_call(
    state: Any,
    questions: Mapping[str, Question],
    normalize: Callable[[dict[str, Any]], Any],
    mock: Callable[[], Any],
) -> Timed:
    """One Jev call: typed questions in, a normalised plain value out, with latency and cost
    attached. `mock` is the offline stand-in used when there is no key, or the key is rejected."""
    started = time.perf_counter()

    async def simulate() -> Timed:
        text = state if isinstance(state, str) else json.dumps(state)
        input_tokens = -(-len(text) // 4) + 60 * len(questions)
        await asyncio.sleep(0.07 + random.random() * 0.13)
        return {
            "value": mock(),
            "latencyMs": round((time.perf_counter() - started) * 1000),
            "inputTokens": input_tokens,
            "costUsd": jev_cost_usd(input_tokens),
            "simulated": True,
        }

    api_key = key_for("jev")
    if not api_key or jev_state()["state"] != "live":
        return await simulate()
    try:
        result = await _client_for(api_key).system_one(state, questions)
    except Exception as err:
        reason = account_error(err)
        if not reason:
            raise
        mark_jev_down(reason)
        return await simulate()
    input_tokens = result.usage.input_tokens or 0 if result.usage else 0
    budget.charge(jev_cost_usd(input_tokens), "jev")
    return {
        "value": normalize(result.answers),
        "latencyMs": round((time.perf_counter() - started) * 1000),
        "inputTokens": input_tokens,
        "costUsd": jev_cost_usd(input_tokens),
        "simulated": False,
    }
