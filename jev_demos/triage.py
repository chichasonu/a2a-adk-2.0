import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from typesafe_sdk import Choice, Noul, Score

from .config import PROVIDERS, usable
from .jev import jev_call
from .llm import complete, parse_with

DEPARTMENTS = {
    "billing": "Charges, refunds, invoices, payment failures",
    "technical": "Bugs, outages, errors, integration problems",
    "account": "Login, access, profile, plan or seat changes",
    "sales": "Pricing, upgrades, demos, procurement",
    "other": "Anything else",
}
Department = Literal["billing", "technical", "account", "sales", "other"]
Action = Literal["discard", "escalate", "auto-reply"]

# One Jev call answers all six questions in a single parallel pass.
TRIAGE_QUESTIONS = {
    "department": Choice(instructions="Which team should handle this ticket?", criteria=DEPARTMENTS),
    "urgency": Score(
        instructions="How urgent is this ticket?",
        criteria=[
            "No time pressure",
            "Would like a reply this week",
            "Blocking their work, needs a reply today",
            "Production is down or money is at risk right now",
        ],
    ),
    "frustration": Score(
        instructions="How frustrated is the customer?",
        criteria=[
            "Calm, factual",
            "Mildly annoyed but civil",
            "Clearly angry",
            "Furious, threatening or abusive",
        ],
    ),
    "refundRequested": Noul(instructions="Does the customer explicitly ask for money back?"),
    "churnRisk": Noul(instructions="Is the customer signalling they may cancel or leave?"),
    "spam": Noul(instructions="Is this spam, a solicitation, or not a real support request?"),
}


def _pct(n: float) -> str:
    return f"{math.floor(n * 100 + 0.5)}%"


def decide_action(t: dict[str, Any]) -> dict[str, Any]:
    """Business rules stay in plain code; Jev only supplies the calibrated signals they branch on."""
    if t["spam"] > 0.7:
        return {"action": "discard", "reasons": [f"spam {_pct(t['spam'])}"]}
    reasons = []
    if t["churnRisk"] > 0.6:
        reasons.append(f"churn risk {_pct(t['churnRisk'])}")
    if t["urgency"] >= 2.2:
        reasons.append(f"urgency {t['urgency']:.1f}/3")
    if t["frustration"] >= 2.2:
        reasons.append(f"frustration {t['frustration']:.1f}/3")
    if t["refundRequested"] > 0.7 and t["department"] == "billing":
        reasons.append("refund request")
    if reasons:
        return {"action": "escalate", "reasons": reasons}
    return {"action": "auto-reply", "reasons": ["low urgency, low churn risk"]}


async def triage_with_jev(ticket: str) -> dict[str, Any]:
    r = await jev_call(
        ticket,
        TRIAGE_QUESTIONS,
        lambda a: {
            "department": a["department"].choice,
            "departmentConfidence": a["department"].confidence,
            "urgency": a["urgency"].score,
            "frustration": a["frustration"].score,
            "refundRequested": a["refundRequested"].noul,
            "churnRisk": a["churnRisk"].noul,
            "spam": a["spam"].noul,
        },
        lambda: _heuristic(ticket),
    )
    return {"triage": r["value"], "latencyMs": r["latencyMs"], "costUsd": r["costUsd"], "inputTokens": r["inputTokens"], "simulated": r["simulated"]}


class LlmTriageSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department: Department
    urgency: float = 0  # 0 no pressure, 1 this week, 2 needs reply today, 3 production down or money at risk
    frustration: float = 0  # 0 calm, 1 mildly annoyed, 2 angry, 3 furious
    refundRequested: float = 0  # probability 0-1 that the customer explicitly asks for a refund
    churnRisk: float = 0  # probability 0-1 that the customer may cancel
    spam: float = 0  # probability 0-1 that this is spam or not a support request


async def triage_with_llm(ticket: str, provider: str) -> dict[str, Any] | None:
    """Baseline: the same six judgements made by the provider's small LLM (the cheap, fast option)."""
    if not usable(provider):
        return None
    try:
        r = await parse_with(
            PROVIDERS[provider]["small"],
            LlmTriageSchema,
            ticket,
            "You triage customer-support tickets. Return the requested fields for the ticket.",
        )
    except Exception:
        return None  # baseline unavailable (spend cap or rate limit): the UI shows the lane as n/a
    return {
        "triage": {**r["value"].model_dump(), "departmentConfidence": None},
        "latencyMs": r["latencyMs"],
        "costUsd": r["costUsd"],
        "inputTokens": r["inputTokens"],
        "simulated": False,
    }


def _heuristic(ticket: str) -> dict[str, Any]:
    """Offline stand-in for Jev; flagged simulated everywhere it surfaces."""
    t = ticket.lower()

    def has(pat: str) -> bool:
        return bool(re.search(pat, t))

    department: Department = (
        "billing" if has(r"charged|refund|invoice|payment|billing|card")
        else "technical" if has(r"error|bug|crash|down|outage|api|500|not working|broken")
        else "account" if has(r"password|login|log in|access|seat|account")
        else "sales" if has(r"pricing|quote|demo|enterprise|upgrade")
        else "other"
    )
    spam = 0.95 if has(r"seo|backlinks|crypto|winner|click here|guaranteed") else 0.03
    relaxed = has(r"nothing urgent|not urgent|no rush|whenever you")
    urgency = 0.5 if relaxed else 2.8 if has(r"production|down|outage|asap|urgent|immediately|losing") else 2.1 if has(r"today|blocked|can't") else 0.7
    frustration = 2.7 if has(r"unacceptable|furious|worst|scam|ridiculous|!!") else 1.8 if has(r"again|still|third time|frustrat") else 0.4
    return {
        "department": department,
        "departmentConfidence": 0.9,
        "urgency": urgency,
        "frustration": frustration,
        "refundRequested": 0.94 if has(r"refund|money back") else 0.04,
        "churnRisk": 0.88 if has(r"cancel|switch to|leaving|competitor") else 0.05,
        "spam": spam,
    }


async def draft_reply(ticket: str, department: str, action: Action, provider: str) -> dict[str, Any] | None:
    """Drafts the first reply; escalated tickets get the bigger model."""
    if action == "discard":
        return None
    p = PROVIDERS[provider]
    return await complete(
        p["mid"] if action == "escalate" else p["small"],
        ticket,
        system=(
            f"You are a support agent for a SaaS company. The ticket was routed to {department}. "
            + (
                "It is high priority: acknowledge the impact, apologise once, and state the next concrete step. "
                if action == "escalate"
                else "Write a short, friendly first reply. "
            )
            + "Under 90 words. Never promise refunds or timelines you cannot verify."
        ),
        max_tokens=250,
    )
