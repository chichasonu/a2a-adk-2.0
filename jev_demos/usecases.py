import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from typesafe_sdk import Choice, Noul, Score

from .config import PROVIDERS, usable
from .jev import jev_call
from .llm import parse_with


def _has(text: str, pat: str) -> bool:
    return bool(re.search(pat, text.lower()))


# ───────────── Use case 3: inbox triage at scale ─────────────

EMAIL_CATEGORIES = ["brand_deal", "invoice", "newsletter", "cold_pitch", "customer", "scam", "other"]
EmailCategory = Literal["brand_deal", "invoice", "newsletter", "cold_pitch", "customer", "scam", "other"]

INBOX_QUESTIONS = {
    "category": Choice(
        instructions="What kind of email is this?",
        criteria={
            "brand_deal": "A brand or sponsor offering a paid collaboration",
            "invoice": "Receipt, invoice, payment confirmation or billing notice",
            "newsletter": "Bulk newsletter or product update",
            "cold_pitch": "Unsolicited sales or service pitch",
            "customer": "A customer or member asking for help or giving feedback",
            "scam": "Phishing, fraud or something untrustworthy",
            "other": "Anything else",
        },
    ),
    "urgency": Score(
        instructions="How important is it that the owner personally sees this email?",
        criteria=["Ignore", "Low", "High, reply today", "Critical, needs a reply within 30 minutes"],
    ),
    "scam": Noul(instructions="Does this email look like a scam or something untrustworthy?"),
    "needsReply": Noul(instructions="Does this email need a personal reply?"),
}


def classify_email_with_jev(email: str):
    return jev_call(
        email,
        INBOX_QUESTIONS,
        lambda a: {
            "category": a["category"].choice,
            "categoryConfidence": a["category"].confidence,
            "urgency": a["urgency"].score,
            "scam": a["scam"].noul,
            "needsReply": a["needsReply"].noul,
        },
        lambda: _inbox_heuristic(email),
    )


def _inbox_heuristic(email: str) -> dict[str, Any]:
    category: EmailCategory = (
        "scam" if _has(email, r"verify your account|password will expire|wire transfer|gift card|prince|lottery")
        else "brand_deal" if _has(email, r"sponsor|brand deal|collab|paid partnership|ambassador")
        else "invoice" if _has(email, r"invoice|receipt|payment (received|failed)|payout|billing")
        else "newsletter" if _has(email, r"newsletter|this week in|unsubscribe|digest")
        else "cold_pitch" if _has(email, r"quick call|book a demo|grow your|10x|our agency|leads")
        else "customer" if _has(email, r"help|not working|broken|refund|can't|question|thank")
        else "other"
    )
    scam = 0.93 if category == "scam" else 0.03
    return {
        "category": category,
        "categoryConfidence": 0.88,
        "urgency": (0.4 if category == "scam" else 2.6) if _has(email, r"urgent|asap|immediately|today|down") else 1.8 if category == "customer" else 1.9 if category == "brand_deal" else 0.5,
        "scam": scam,
        "needsReply": 0.9 if category in ("customer", "brand_deal") else 0.06,
    }


class InboxLlmSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: EmailCategory
    urgency: float = 0  # 0 ignore, 1 low, 2 reply today, 3 critical within 30 minutes
    scam: float = 0  # probability 0-1 the email is a scam
    needsReply: float = 0  # probability 0-1 the email needs a personal reply


async def classify_email_with_llm(email: str, provider: str) -> dict[str, Any] | None:
    """Baseline: the provider's small LLM answering the same questions. None when it can't run."""
    if not usable(provider):
        return None
    try:
        r = await parse_with(
            PROVIDERS[provider]["small"],
            InboxLlmSchema,
            email,
            "You triage the inbox of a busy content creator. Return the requested fields for the email.",
        )
    except Exception:
        return None
    return {"value": {**r["value"].model_dump(), "categoryConfidence": None}, "latencyMs": r["latencyMs"], "inputTokens": r["inputTokens"], "costUsd": r["costUsd"], "simulated": False}


# ───────────── Use case 4: real-time feed filter ─────────────

POST_KINDS = ["breaking", "golden_nugget", "hot_take", "promo", "ai_slop", "noise"]
PostKind = Literal["breaking", "golden_nugget", "hot_take", "promo", "ai_slop", "noise"]

FEED_QUESTIONS = {
    "kind": Choice(
        instructions="What kind of post is this?",
        criteria={
            "breaking": "Genuine breaking news, or an official product launch or release announcement with concrete details",
            "golden_nugget": "A specific, useful insight, technique or resource worth saving",
            "hot_take": "An opinion without new information",
            "promo": "Advertising: giveaways, discount codes, follow-for-a-prize, or vague self-promotion with no concrete details",
            "ai_slop": "Generic, formulaic engagement bait that reads as machine-written",
            "noise": "Chatter with no value",
        },
    ),
    "aiWritten": Noul(instructions="Does this read as machine-written filler?"),
    "worthReading": Score(
        instructions="How worth reading is this for someone focused on AI and building products?",
        criteria=["Skip", "Skim", "Read", "Must read"],
    ),
}


def label_post(post: str):
    return jev_call(
        post,
        FEED_QUESTIONS,
        lambda a: {"kind": a["kind"].choice, "kindConfidence": a["kind"].confidence, "aiWritten": a["aiWritten"].noul, "worthReading": a["worthReading"].score},
        lambda: _feed_heuristic(post),
    )


def _feed_heuristic(post: str) -> dict[str, Any]:
    slop = _has(post, "game[- ]?changer|unlock|here'?s the thing|let that sink in|thread 🧵|10 ways|nobody is talking about|🚀|🔥")
    kind: PostKind = (
        "ai_slop" if slop
        else "breaking" if _has(post, r"just released|announcing|now available|launch(ed|es)|breaking")
        else "promo" if _has(post, r"giveaway|use code|link in bio|discount|dm me")
        else "golden_nugget" if _has(post, r"benchmark|tip:|how to|repo|paper|latency|we measured|open.?source")
        else "hot_take" if _has(post, r"unpopular opinion|overrated|hot take|dead")
        else "noise"
    )
    return {
        "kind": kind,
        "kindConfidence": 0.85,
        "aiWritten": 0.94 if slop else 0.08,
        "worthReading": {"breaking": 2.6, "golden_nugget": 2.8, "hot_take": 1.2, "promo": 0.4, "ai_slop": 0.2, "noise": 0.3}[kind],
    }


# ───────────── Use case 5: LLM writes, Jev ranks ─────────────

TITLE_QUESTIONS = {
    "clickAppeal": Score(
        instructions="How likely is a curious viewer to click this video title?",
        criteria=["Very unlikely", "Unlikely", "Maybe", "Likely", "Very likely"],
    ),
    "clarity": Score(
        instructions="How clear is what the viewer will get from this title?",
        criteria=["Vague", "Somewhat clear", "Clear", "Crystal clear"],
    ),
    "clickbait": Noul(instructions="Is this title misleading or hype without substance?"),
    "specific": Noul(instructions="Does the title name a concrete outcome, number or tool?"),
}


def _composite(t: dict[str, float]) -> int:
    return math.floor(max(0, min(100, (t["clickAppeal"] / 4) * 45 + (t["clarity"] / 3) * 25 + t["specific"] * 15 + (1 - t["clickbait"]) * 15)) + 0.5)


def score_title(title: str, topic: str):
    def mock() -> dict[str, Any]:
        specific = 0.8 if re.search(r"\d|build|step|vs|tutorial|how to", title, re.IGNORECASE) else 0.25
        clickbait = 0.85 if re.search(r"insane|shocking|you won't believe|secret|!!", title, re.IGNORECASE) else 0.1
        t = {"clickAppeal": 1.4 + (len(title) % 7) * 0.32, "clarity": 2.4 if specific > 0.5 else 1.3, "clickbait": clickbait, "specific": specific}
        return {**t, "composite": _composite(t)}

    return jev_call(
        {"topic": topic, "title": title},
        TITLE_QUESTIONS,
        lambda a: _with_composite(a),
        mock,
    )


def _with_composite(a: dict[str, Any]) -> dict[str, Any]:
    t = {"clickAppeal": a["clickAppeal"].score, "clarity": a["clarity"].score, "clickbait": a["clickbait"].noul, "specific": a["specific"].noul}
    return {**t, "composite": _composite(t)}


class TitlesSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    titles: list[str]


async def generate_titles(topic: str, n: int, provider: str) -> dict[str, Any]:
    async def simulated() -> dict[str, Any]:
        seeds = [
            f"{topic}: the complete beginner tutorial", f"I built an AI agent with {topic} in 20 minutes", f"{topic} is INSANE (you won't believe this)",
            f"{topic} vs the old way: real cost and speed numbers", f"The secret {topic} trick nobody talks about", f"{topic} explained in 5 minutes",
            f"Build a support triage bot with {topic} step by step", f"Why {topic} changes everything", f"{topic} for beginners: 3 projects you can copy",
            f"Stop overpaying for AI: {topic} cuts costs 90%", f"{topic}: what I learned after 7 days", f"Is {topic} overhyped? I tested it",
        ]
        return {"titles": seeds[:n], "simulated": True, "latencyMs": 300, "costUsd": 0}

    if not usable(provider):
        return await simulated()
    try:
        r = await parse_with(
            PROVIDERS[provider]["small"],
            TitlesSchema,
            f"Topic: {topic}\nWrite {n} distinct YouTube video titles, varied in style (tutorial, curiosity, comparison, hype, specific outcome). Titles only.",
            "You write YouTube titles for a developer audience.",
        )
    except Exception:
        return await simulated()
    return {"titles": r["value"].titles[:n], "simulated": False, "latencyMs": r["latencyMs"], "costUsd": r["costUsd"]}
