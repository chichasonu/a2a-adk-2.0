"""Behavioural signal capture for predictive service issue resolution.

The web/mobile app emits low-level signals (searches, page views, declined
transactions). They are stored in a per-user Redis sorted set scored by
timestamp and aggregated into the features the predictor consumes.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import Field
from redis import asyncio as aioredis

from .config import settings
from .memory import tokenize
from .redis_client import create_redis_client

logger = logging.getLogger(__name__)

SignalType = Literal[
    "search",
    "page_view",
    "transaction_declined",
    "call_ivr",
    "chat_open",
    "action_taken",
]

# Keywords that map free-text search or page context to a card issue type.
ISSUE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "lost_or_stolen": ("lost", "stolen", "missing", "misplaced", "replace", "replacement"),
    "blocked": ("blocked", "block", "locked", "lock", "frozen", "freeze", "unlock", "declined", "decline"),
    "not_activated": ("activate", "activation", "new card", "not working", "inactive"),
    "disputed_charge": ("dispute", "fraud", "unauthorized", "unauthorised", "charge", "chargeback", "refund"),
}

CARD_PAGES = ("card_management", "card_details", "card_settings", "card_replace")


class Signal(BaseModel):
    """A single behavioural event emitted by the customer-facing app."""

    user_id: str = Field(..., min_length=1)
    type: SignalType
    # Free-text query for searches, page name for views, amount/merchant for
    # declines - kept as a loose payload so the app can evolve independently.
    value: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)


class SignalFeatures(BaseModel):
    """Aggregated features over the recent signal window."""

    user_id: str
    window_seconds: int
    signal_count: int = 0
    search_count: int = 0
    card_issue_searches: list[str] = Field(default_factory=list)
    card_page_views: int = 0
    repeated_card_page: bool = False
    distinct_card_pages: list[str] = Field(default_factory=list)
    declined_transactions: int = 0
    ivr_calls: int = 0
    dwell_seconds: float = 0.0
    keyword_hits: dict[str, int] = Field(default_factory=dict)
    last_signal_at: float | None = None


def classify_issue_keywords(text: str) -> dict[str, int]:
    """Count issue-keyword hits in free text (multi-word keywords included)."""
    lowered = text.lower()
    tokens = set(tokenize(text))
    hits: dict[str, int] = {}
    for issue, keywords in ISSUE_KEYWORDS.items():
        count = 0
        for keyword in keywords:
            if " " in keyword:
                count += 1 if keyword in lowered else 0
            else:
                count += 1 if keyword in tokens else 0
        if count:
            hits[issue] = count
    return hits


class SignalStore:
    """Persists and aggregates behavioural signals in Redis."""

    SIGNAL_PREFIX = "care:signals"

    def __init__(self, redis_url: str | None = None, redis: aioredis.Redis | None = None):
        self.redis_url = redis_url or settings.REDIS_URL
        self._redis = redis

    @property
    def client(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = create_redis_client(self.redis_url)
        return self._redis

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    def _key(self, user_id: str) -> str:
        return f"{self.SIGNAL_PREFIX}:{settings.APP_NAME}:{user_id}"

    async def record(self, signal: Signal) -> Signal:
        """Append a signal and drop entries older than the retention TTL."""
        key = self._key(signal.user_id)
        await self.client.zadd(key, {signal.model_dump_json(): signal.timestamp})
        await self.client.zremrangebyscore(
            key, "-inf", signal.timestamp - settings.SIGNAL_TTL_SECONDS
        )
        await self.client.expire(key, settings.SIGNAL_TTL_SECONDS)
        logger.info(
            "Signal user=%s type=%s value=%s", signal.user_id, signal.type, signal.value
        )
        return signal

    async def recent(
        self, user_id: str, window_seconds: int | None = None
    ) -> list[Signal]:
        """Return signals inside the rolling window, oldest first."""
        window = window_seconds or settings.SIGNAL_WINDOW_SECONDS
        cutoff = time.time() - window
        raw = await self.client.zrangebyscore(self._key(user_id), cutoff, "+inf")
        signals = []
        for item in raw:
            try:
                signals.append(Signal.model_validate_json(item))
            except ValueError:
                logger.warning("Skipping corrupt signal for user=%s", user_id)
        return signals

    async def clear(self, user_id: str) -> None:
        await self.client.delete(self._key(user_id))

    async def features(
        self, user_id: str, window_seconds: int | None = None
    ) -> SignalFeatures:
        """Aggregate the recent signal window into predictor features."""
        window = window_seconds or settings.SIGNAL_WINDOW_SECONDS
        signals = await self.recent(user_id, window)

        features = SignalFeatures(
            user_id=user_id, window_seconds=window, signal_count=len(signals)
        )
        if not signals:
            return features

        keyword_hits: Counter[str] = Counter()
        card_pages: Counter[str] = Counter()

        for signal in signals:
            text = f"{signal.value} {signal.metadata.get('context', '')}"
            if signal.type == "search":
                features.search_count += 1
                hits = classify_issue_keywords(text)
                if hits:
                    features.card_issue_searches.append(signal.value)
                keyword_hits.update(hits)
            elif signal.type == "page_view":
                page = signal.value
                if page in CARD_PAGES or "card" in page.lower():
                    features.card_page_views += 1
                    card_pages[page] += 1
                    features.dwell_seconds += float(
                        signal.metadata.get("dwell_seconds", 0) or 0
                    )
                    keyword_hits.update(classify_issue_keywords(text))
            elif signal.type == "transaction_declined":
                features.declined_transactions += 1
                keyword_hits["blocked"] += 1
            elif signal.type == "call_ivr":
                features.ivr_calls += 1
                keyword_hits.update(classify_issue_keywords(text))

        features.keyword_hits = dict(keyword_hits)
        features.distinct_card_pages = sorted(card_pages)
        features.repeated_card_page = any(
            count >= settings.REPEAT_VIEW_THRESHOLD for count in card_pages.values()
        )
        features.last_signal_at = signals[-1].timestamp
        return features

    async def timeline(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return the newest signals as plain dicts for the UI inspector."""
        raw = await self.client.zrevrange(self._key(user_id), 0, max(limit - 1, 0))
        timeline = []
        for item in raw:
            try:
                timeline.append(json.loads(item))
            except json.JSONDecodeError:
                continue
        return timeline
