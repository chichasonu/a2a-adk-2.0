import os
import re
import time
from typing import Literal, TypedDict

import openai
from typesafe_sdk import TypeSafeAPIError

from .keys import bucket, key_for

ProviderKey = Literal["claude", "kimi"]
ModelKey = Literal["haiku", "sonnet", "opus", "kimiK26", "kimiK3"]
Tier = Literal["light", "standard", "frontier"]


class ModelInfo(TypedDict):
    id: str
    label: str
    provider: ProviderKey
    inPerM: float  # USD per 1M tokens
    outPerM: float


# Prices are OpenRouter list prices as of Sep 2026. Re-check before quoting numbers externally.
MODELS: dict[str, ModelInfo] = {
    "haiku": {"id": "anthropic/claude-haiku-4.5", "label": "Haiku 4.5", "provider": "claude", "inPerM": 1, "outPerM": 5},
    "sonnet": {"id": "anthropic/claude-sonnet-5", "label": "Sonnet 5", "provider": "claude", "inPerM": 2, "outPerM": 10},
    "opus": {"id": "anthropic/claude-opus-5", "label": "Opus 5", "provider": "claude", "inPerM": 5, "outPerM": 25},
    "kimiK26": {"id": "moonshotai/kimi-k2.6", "label": "Kimi K2.6", "provider": "kimi", "inPerM": 0.95, "outPerM": 4},
    "kimiK3": {"id": "moonshotai/kimi-k3", "label": "Kimi K3", "provider": "kimi", "inPerM": 3, "outPerM": 15},
}


class Provider(TypedDict):
    label: str
    tiers: dict[Tier, ModelKey]  # which model answers each router tier
    small: ModelKey  # cheap model: classifier baseline, title writer, auto-replies
    mid: ModelKey  # bigger model for escalated replies
    big: ModelKey  # top model: the "always use the best" baseline


PROVIDERS: dict[str, Provider] = {
    "claude": {"label": "Claude", "tiers": {"light": "haiku", "standard": "sonnet", "frontier": "opus"}, "small": "haiku", "mid": "sonnet", "big": "opus"},
    "kimi": {"label": "Kimi", "tiers": {"light": "kimiK26", "standard": "kimiK3", "frontier": "kimiK3"}, "small": "kimiK26", "mid": "kimiK3", "big": "kimiK3"},
}

# Jev: $0.042 per 1M input tokens, output free.
JEV_IN_PER_M = 0.042

# Both providers are served by one OpenRouter key, so they share the "openrouter" key/budget bucket.
LLM_KEY_NAME = "openrouter"


def llm_key() -> str | None:
    return key_for(LLM_KEY_NAME)


def has_jev_key() -> bool:
    return key_for("jev") is not None


def has_key(p: ProviderKey) -> bool:
    return llm_key() is not None


def cost_usd(m: ModelInfo, in_tok: float, out_tok: float) -> float:
    return (in_tok * m["inPerM"] + out_tok * m["outPerM"]) / 1e6


def jev_cost_usd(in_tok: float) -> float:
    return (in_tok * JEV_IN_PER_M) / 1e6


# ───────────── Spend budget ─────────────
# A hard cap on real API spend. Once reached, paid LLM calls fall back to clearly badged simulated
# output until the cap is raised. Every key gets its own bucket: all traffic on the server's keys
# shares one, and each visitor-supplied key has its own, so nobody can spend anybody else's budget.
# Jev is ~free, so it is counted but never blocked.
def _default_cap() -> float:
    return float(os.environ.get("DEMO_BUDGET_USD", "0.5"))


_budgets: dict[str, dict[str, float]] = {}


def _slot(name: str) -> dict[str, float]:
    b = _budgets.get(bucket(name))
    if b is None:
        if len(_budgets) >= 2000:
            _budgets.pop(next(iter(_budgets)))  # bound memory on a public host
        b = {"spent": 0.0, "cap": _default_cap()}
        _budgets[bucket(name)] = b
    return b


class _Budget:
    def spent(self, name: str) -> float:
        return _slot(name)["spent"]

    def cap(self, name: str) -> float:
        return _slot(name)["cap"]

    def charge(self, usd: float, name: str) -> None:
        _slot(name)["spent"] += usd

    def set_cap(self, usd: float, name: str) -> None:
        _slot(name)["cap"] = usd

    def exhausted(self, name: str) -> bool:
        s = _slot(name)
        return s["spent"] >= s["cap"]


budget = _Budget()

# ───────────── Provider availability ─────────────
# Account-level failures (spend cap, bad key, no credit) mark that provider down for a cool-down
# window. The bucket is the shared openrouter key; the provider prefix keeps a rejected Kimi model
# from sinking Claude.
_down: dict[str, dict[str, object]] = {}
COOL_DOWN_S = 10 * 60  # account-level problems (spend cap, credit) do not clear in seconds


def _down_id(p: ProviderKey) -> str:
    return f"{p}:{bucket(LLM_KEY_NAME)}"


def provider_state(p: ProviderKey) -> dict[str, str]:
    if not has_key(p):
        return {"state": "simulated", "note": "No API key set"}
    if budget.exhausted(LLM_KEY_NAME):
        return {"state": "budget", "note": f"Demo budget of ${budget.cap(LLM_KEY_NAME):.2f} reached"}
    d = _down.get(_down_id(p))
    if d and time.time() < d["until"]:
        return {"state": "unavailable", "note": str(d["reason"])}
    return {"state": "live", "note": ""}


def usable(p: ProviderKey) -> bool:
    return provider_state(p)["state"] == "live"


def _status_and_message(err: BaseException) -> tuple[int | None, str]:
    if isinstance(err, openai.APIStatusError):
        return err.status_code, err.message
    if isinstance(err, TypeSafeAPIError):
        return err.status, str(err)
    return None, str(err)


def account_error(err: BaseException) -> str | None:
    """A human reason when `err` is an account-level failure (not a transient one), else None."""
    status, message = _status_and_message(err)
    if status in (401, 402, 403):
        return message or "The provider rejected the key"
    if status in (400, 429) and re.search(
        r"usage limit|credit balance|billing|spend|insufficient|quota|balance|suspended", message, re.IGNORECASE
    ):
        return message
    return None


def mark_down(p: ProviderKey, reason: str) -> None:
    if len(_down) >= 2000:
        _down.pop(next(iter(_down)))
    _down[_down_id(p)] = {"until": time.time() + COOL_DOWN_S, "reason": reason}


def reset_down(p: ProviderKey) -> None:
    _down.pop(_down_id(p), None)
