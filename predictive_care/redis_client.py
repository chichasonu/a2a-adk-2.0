"""Redis (or embedded fakeredis) client factory for the predictive care app."""

from __future__ import annotations

import logging
from typing import Any

from redis import asyncio as aioredis

from .config import settings

logger = logging.getLogger(__name__)

_fakeredis_server: Any | None = None


def _get_fakeredis_server() -> Any:
    """Return (creating if needed) the process-wide shared fakeredis server."""
    global _fakeredis_server
    if _fakeredis_server is None:
        import fakeredis

        _fakeredis_server = fakeredis.FakeServer()
    return _fakeredis_server


def create_redis_client(redis_url: str | None = None) -> aioredis.Redis:
    """Return an async Redis client, embedded when fakeredis is requested."""
    redis_url = redis_url or settings.REDIS_URL
    if settings.USE_FAKEREDIS or redis_url.startswith(("fake://", "embedded://")):
        import fakeredis

        logger.info("Predictive care using embedded fakeredis")
        return fakeredis.FakeAsyncRedis(
            server=_get_fakeredis_server(), decode_responses=True, encoding="utf-8"
        )

    return aioredis.from_url(redis_url, decode_responses=True, encoding="utf-8")
