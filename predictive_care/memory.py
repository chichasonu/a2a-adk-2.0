"""Redis-backed long-term memory for the predictive care agent.

Implements the ADK 2.0 ``BaseMemoryService`` contract so the agent can recall
facts across sessions (``load_memory`` / ``preload_memory`` tools), and adds a
small profile layer with the durable, structured facts the predictor needs
(recurring issue types, preferred resolutions, resolution outcomes).

Retrieval is lexical (token overlap with an inverse-frequency weight plus a
recency boost) so the POC has no embedding-model or vector-index dependency.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

from google.adk.events.event import Event
from google.adk.memory import BaseMemoryService
from google.adk.memory.base_memory_service import SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.sessions import Session
from google.genai import types
from redis import asyncio as aioredis
from typing_extensions import override

from .config import settings
from .redis_client import create_redis_client

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    """a an and are as at be but by for from has have i if in is it its me my not
    of on or that the their there they this to was were what when where which who
    will with you your""".split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens with stopwords removed."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def _content_text(content: types.Content | None) -> str:
    if content is None:
        return ""
    return " ".join(part.text or "" for part in content.parts or [] if part.text).strip()


class RedisMemoryService(BaseMemoryService):
    """Long-term, cross-session memory persisted in Redis.

    Layout (per app/user):
      * ``care:memory:<app>:<user>``  — sorted set of memory entries (score = ts)
      * ``care:profile:<app>:<user>`` — hash of durable structured facts
    """

    MEMORY_PREFIX = "care:memory"
    PROFILE_PREFIX = "care:profile"
    MAX_ENTRIES = 500

    def __init__(
        self,
        redis_url: str | None = None,
        app_name: str | None = None,
        redis: aioredis.Redis | None = None,
    ):
        super().__init__()
        self.redis_url = redis_url or settings.REDIS_URL
        self.app_name = app_name or settings.APP_NAME
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

    # ------------------------------------------------------------------
    # Keys
    # ------------------------------------------------------------------

    def _memory_key(self, app_name: str, user_id: str) -> str:
        return f"{self.MEMORY_PREFIX}:{app_name}:{user_id}"

    def _profile_key(self, app_name: str, user_id: str) -> str:
        return f"{self.PROFILE_PREFIX}:{app_name}:{user_id}"

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def remember(
        self,
        *,
        user_id: str,
        text: str,
        author: str = "system",
        kind: str = "fact",
        app_name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store a single long-term memory fact and return the stored record."""
        app_name = app_name or self.app_name
        record = {
            "text": text.strip(),
            "author": author,
            "kind": kind,
            "timestamp": time.time(),
            "metadata": dict(metadata or {}),
        }
        key = self._memory_key(app_name, user_id)
        await self.client.zadd(key, {json.dumps(record): record["timestamp"]})
        # Trim oldest entries so a long-lived demo user cannot grow unbounded.
        await self.client.zremrangebyrank(key, 0, -(self.MAX_ENTRIES + 1))
        if settings.MEMORY_TTL_SECONDS > 0:
            await self.client.expire(key, settings.MEMORY_TTL_SECONDS)
        logger.info("Remembered [%s] for user=%s: %s", kind, user_id, record["text"])
        return record

    @override
    async def add_session_to_memory(self, session: Session) -> None:
        """Ingest a whole conversation into long-term memory."""
        await self.add_events_to_memory(
            app_name=session.app_name,
            user_id=session.user_id,
            events=session.events,
            session_id=session.id,
        )

    @override
    async def add_events_to_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        events: Sequence[Event],
        session_id: str | None = None,
        custom_metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Ingest a delta of conversation events as memory entries."""
        for event in events:
            text = _content_text(event.content)
            if not text:
                continue
            metadata = dict(custom_metadata or {})
            if session_id:
                metadata["session_id"] = session_id
            await self.remember(
                app_name=app_name,
                user_id=user_id,
                text=text,
                author=event.author or (event.content.role if event.content else "user"),
                kind="conversation",
                metadata=metadata,
            )

    @override
    async def add_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        memories: Sequence[MemoryEntry],
        custom_metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Direct write of explicit memory items."""
        for memory in memories:
            text = _content_text(memory.content)
            if not text:
                continue
            metadata = {**dict(custom_metadata or {}), **memory.custom_metadata}
            await self.remember(
                app_name=app_name,
                user_id=user_id,
                text=text,
                author=memory.author or "system",
                kind=str(metadata.get("kind", "fact")),
                metadata=metadata,
            )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def list_records(
        self, *, user_id: str, app_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return the most recent memory records, newest first."""
        app_name = app_name or self.app_name
        raw = await self.client.zrevrange(
            self._memory_key(app_name, user_id), 0, max(limit - 1, 0)
        )
        records = []
        for item in raw:
            try:
                records.append(json.loads(item))
            except json.JSONDecodeError:
                logger.warning("Skipping corrupt memory record for user=%s", user_id)
        return records

    def _score(
        self, record: dict[str, Any], query_tokens: list[str], idf: dict[str, float], now: float
    ) -> float:
        tokens = set(tokenize(record.get("text", "")))
        if not tokens:
            return 0.0
        overlap = sum(idf.get(token, 0.0) for token in set(query_tokens) & tokens)
        if overlap <= 0:
            return 0.0
        age_days = max(now - float(record.get("timestamp", now)), 0.0) / 86400.0
        recency = 1.0 / (1.0 + age_days)
        return overlap * (0.7 + 0.3 * recency)

    async def search_records(
        self,
        *,
        user_id: str,
        query: str,
        app_name: str | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Lexically rank stored memory records against ``query``."""
        records = await self.list_records(
            user_id=user_id, app_name=app_name, limit=self.MAX_ENTRIES
        )
        query_tokens = tokenize(query)
        if not records or not query_tokens:
            return []

        # Inverse document frequency keeps common demo words from dominating.
        total = len(records)
        doc_freq: dict[str, int] = {}
        for record in records:
            for token in set(tokenize(record.get("text", ""))):
                doc_freq[token] = doc_freq.get(token, 0) + 1
        idf = {
            token: math.log(1 + total / (1 + doc_freq.get(token, 0)))
            for token in set(query_tokens)
        }

        now = time.time()
        scored = [
            (self._score(record, query_tokens, idf, now), record) for record in records
        ]
        ranked = sorted(
            (item for item in scored if item[0] > 0), key=lambda item: item[0], reverse=True
        )
        for score, record in ranked:
            record["score"] = round(score, 4)
        return [record for _, record in ranked[:top_k]]

    @override
    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> SearchMemoryResponse:
        """ADK entry point used by the ``load_memory`` / ``preload_memory`` tools."""
        records = await self.search_records(
            user_id=user_id, query=query, app_name=app_name
        )
        memories = [
            MemoryEntry(
                content=types.Content(
                    role="user", parts=[types.Part(text=record["text"])]
                ),
                author=record.get("author"),
                timestamp=str(record.get("timestamp")),
                custom_metadata={
                    "kind": record.get("kind", "fact"),
                    **record.get("metadata", {}),
                },
            )
            for record in records
        ]
        return SearchMemoryResponse(memories=memories)

    # ------------------------------------------------------------------
    # Structured profile
    # ------------------------------------------------------------------

    async def get_profile(
        self, *, user_id: str, app_name: str | None = None
    ) -> dict[str, Any]:
        """Return the durable structured profile for a user."""
        app_name = app_name or self.app_name
        raw = await self.client.hgetall(self._profile_key(app_name, user_id))
        profile: dict[str, Any] = {}
        for key, value in raw.items():
            try:
                profile[key] = json.loads(value)
            except json.JSONDecodeError:
                profile[key] = value
        return profile

    async def update_profile(
        self, *, user_id: str, updates: Mapping[str, Any], app_name: str | None = None
    ) -> dict[str, Any]:
        """Merge ``updates`` into the user's profile and return the new profile."""
        app_name = app_name or self.app_name
        if updates:
            await self.client.hset(
                self._profile_key(app_name, user_id),
                mapping={key: json.dumps(value) for key, value in updates.items()},
            )
        return await self.get_profile(user_id=user_id, app_name=app_name)

    async def increment_profile_counter(
        self, *, user_id: str, field: str, amount: int = 1, app_name: str | None = None
    ) -> int:
        """Increment a JSON integer counter stored in the profile hash."""
        profile = await self.get_profile(user_id=user_id, app_name=app_name)
        current = profile.get(field)
        value = int(current) + amount if isinstance(current, int) else amount
        await self.update_profile(
            user_id=user_id, updates={field: value}, app_name=app_name
        )
        return value

    async def clear(self, *, user_id: str, app_name: str | None = None) -> None:
        """Forget everything about a user (demo reset)."""
        app_name = app_name or self.app_name
        await self.client.delete(
            self._memory_key(app_name, user_id), self._profile_key(app_name, user_id)
        )
