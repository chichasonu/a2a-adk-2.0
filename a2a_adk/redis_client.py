"""Factory for the Redis (or embedded FakeRedis) async client."""

from __future__ import annotations

import logging
from typing import Any

from redis import asyncio as aioredis

from .config import settings

logger = logging.getLogger(__name__)

# Shared in-memory server so that all clients see the same embedded Redis data.
_fakeredis_server: Any | None = None


def _get_fakeredis_server() -> Any:
    """Return (and create if needed) the shared fakeredis server."""
    global _fakeredis_server
    if _fakeredis_server is None:
        import fakeredis

        _fakeredis_server = fakeredis.FakeServer()
    return _fakeredis_server


def create_redis_client(redis_url: str | None = None) -> aioredis.Redis:
    """Return an async Redis client.

    If ``USE_FAKEREDIS`` is true or ``redis_url`` starts with ``fake://`` or
    ``embedded://``, an in-memory ``fakeredis`` client is used instead of a
    real Redis server. All embedded clients in the same process share a single
    ``FakeServer`` so session, event, and context data is consistent.
    """
    redis_url = redis_url or settings.REDIS_URL
    if settings.USE_FAKEREDIS or redis_url.startswith(("fake://", "embedded://")):
        try:
            import fakeredis
        except ImportError as exc:
            raise RuntimeError(
                "fakeredis is required for embedded mode. Install it with: "
                "pip install 'fakeredis>=2.36.2,<2.37.0'"
            ) from exc
        logger.info("Using embedded fakeredis")
        return fakeredis.FakeAsyncRedis(
            server=_get_fakeredis_server(), decode_responses=True, encoding="utf-8"
        )

    return aioredis.from_url(
        redis_url,
        decode_responses=True,
        encoding="utf-8",
    )
