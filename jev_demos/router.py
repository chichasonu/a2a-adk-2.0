import re
from typing import Any

from typesafe_sdk import Choice, Noul, Score

from .config import PROVIDERS
from .jev import jev_call

TIERS = ["light", "standard", "frontier"]

# Below this Jev confidence we escalate one tier: a wrong downgrade costs more than a wasted upgrade.
MIN_CONFIDENCE = 0.6

# The routing "policy" lives here, as typed questions. Control flow stays in ordinary code.
ROUTING_QUESTIONS = {
    "tier": Choice(
        instructions="What is the minimum model tier that can answer this request well?",
        criteria={
            "light": "Simple: greeting, factual lookup, short rewrite, formatting, extraction or classification of short text",
            "standard": "Moderate: typical coding task, summarising long text, multi-step analysis, constrained writing",
            "frontier": "Hard: novel algorithm or system design, deep multi-step reasoning, proofs, subtle debugging, high-stakes analysis",
        },
    ),
    "complexity": Score(
        instructions="How complex is the request?",
        criteria=[
            "Trivial, one obvious step",
            "Simple, a few obvious steps",
            "Moderate, needs planning or domain knowledge",
            "Hard, needs deep reasoning or expert judgement",
        ],
    ),
    "needsReasoning": Noul(instructions="Does answering correctly require extended multi-step reasoning?"),
}


def _heuristic(prompt: str) -> dict[str, Any]:
    """Offline stand-in for Jev so the demo runs without a key. Deliberately crude, always flagged
    simulated."""
    p = prompt.lower()
    hard = 0
    if re.search(r"prove|proof|distributed|consensus|architect|race condition|deadlock|optimi[sz]e|trade-?offs?|migration plan|threat model", p):
        hard += 2
    if re.search(r"step by step|derive|why does|root cause|design a|from scratch", p):
        hard += 1
    if len(prompt) > 900:
        hard += 1
    std = 0
    if re.search(r"refactor|function|code|bug|sql|summari[sz]e|analy[sz]e|compare|write an? (email|essay|report)", p):
        std += 1
    if len(prompt) > 300:
        std += 1
    tier = "frontier" if hard >= 2 else ("standard" if hard + std >= 1 else "light")
    top = {"light": 0.9, "standard": 0.78, "frontier": 0.86}[tier]
    rest = (1 - top) / 2
    return {
        "jevTier": tier,
        "probabilities": {t: (top if t == tier else rest) for t in TIERS},
        "confidence": top,
        "complexity": {"light": 0.4, "standard": 1.7, "frontier": 2.7}[tier],
        "needsReasoning": {"light": 0.05, "standard": 0.4, "frontier": 0.9}[tier],
    }


async def decide(prompt: str, provider: str) -> dict[str, Any]:
    r = await jev_call(
        prompt[:60_000],
        ROUTING_QUESTIONS,
        lambda a: {
            "jevTier": a["tier"].choice,
            "probabilities": a["tier"].probabilities,
            "confidence": a["tier"].confidence,
            "complexity": a["complexity"].score,
            "needsReasoning": a["needsReasoning"].noul,
        },
        lambda: _heuristic(prompt),
    )
    idx = TIERS.index(r["value"]["jevTier"])
    escalated = r["value"]["confidence"] < MIN_CONFIDENCE and idx < len(TIERS) - 1
    tier = TIERS[idx + 1] if escalated else r["value"]["jevTier"]
    return {
        **r["value"],
        "tier": tier,
        "escalated": escalated,
        "provider": provider,
        "model": PROVIDERS[provider]["tiers"][tier],
        "latencyMs": r["latencyMs"],
        "inputTokens": r["inputTokens"],
        "costUsd": r["costUsd"],
        "simulated": r["simulated"],
    }
