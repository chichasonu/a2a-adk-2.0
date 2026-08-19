"""Redis-backed ADK SessionService for the predictive care app.

Short-term (conversation) memory. It is deliberately separate from
``RedisMemoryService``, which holds the long-term, cross-session memory.
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.events.event import Event
from google.adk.platform import time as platform_time
from google.adk.platform import uuid as platform_uuid
from google.adk.sessions import BaseSessionService
from google.adk.sessions import Session
from google.adk.sessions.base_session_service import GetSessionConfig
from google.adk.sessions.base_session_service import ListSessionsResponse
from pydantic import ValidationError
from redis import asyncio as aioredis
from typing_extensions import override

from .config import settings
from .redis_client import create_redis_client

logger = logging.getLogger(__name__)


class RedisCareSessionService(BaseSessionService):
    """Stores ADK sessions (events + state) as JSON documents in Redis."""

    SESSION_PREFIX = "care:session"

    def __init__(self, redis_url: str | None = None, redis: aioredis.Redis | None = None):
        super().__init__()
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

    def _key(self, app_name: str, user_id: str, session_id: str) -> str:
        return f"{self.SESSION_PREFIX}:{app_name}:{user_id}:{session_id}"

    async def _load(
        self, app_name: str, user_id: str, session_id: str
    ) -> Session | None:
        raw = await self.client.get(self._key(app_name, user_id, session_id))
        if not raw:
            return None
        try:
            return Session.model_validate_json(raw)
        except ValidationError as exc:
            logger.error("Failed to deserialize session %s: %s", session_id, exc)
            return None

    async def _save(self, session: Session) -> None:
        await self.client.set(
            self._key(session.app_name, session.user_id, session.id),
            session.model_dump_json(),
        )

    @override
    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Session:
        session_id = (session_id or "").strip() or platform_uuid.new_uuid()
        existing = await self._load(app_name, user_id, session_id)
        if existing is not None:
            return existing

        session = Session(
            app_name=app_name,
            user_id=user_id,
            id=session_id,
            state=state or {},
            last_update_time=platform_time.get_time(),
        )
        await self._save(session)
        return session

    @override
    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: GetSessionConfig | None = None,
    ) -> Session | None:
        session = await self._load(app_name, user_id, session_id)
        if session is None:
            return None
        if config and config.num_recent_events is not None:
            session.events = (
                session.events[-config.num_recent_events :]
                if config.num_recent_events
                else []
            )
        return session

    @override
    async def list_sessions(
        self, *, app_name: str, user_id: str | None = None
    ) -> ListSessionsResponse:
        pattern = self._key(app_name, user_id or "*", "*")
        sessions = []
        async for key in self.client.scan_iter(match=pattern):
            raw = await self.client.get(key)
            if not raw:
                continue
            try:
                session = Session.model_validate_json(raw)
            except ValidationError:
                continue
            session.events = []
            sessions.append(session)
        return ListSessionsResponse(sessions=sessions)

    @override
    async def delete_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        await self.client.delete(self._key(app_name, user_id, session_id))

    @override
    async def append_event(self, session: Session, event: Event) -> Event:
        if event.partial:
            return event
        await super().append_event(session=session, event=event)
        session.last_update_time = event.timestamp
        if event.actions and event.actions.state_delta:
            session.state.update(event.actions.state_delta)
        await self._save(session)
        return event

    @override
    async def flush(self) -> None:
        pass
