"""Redis-backed SessionService for Google ADK 2.0."""

from __future__ import annotations

import json
import logging
from typing import Any

from google.adk.events.event import Event
from google.adk.platform import time as platform_time
from google.adk.platform import uuid as platform_uuid
from google.adk.sessions import BaseSessionService
from google.adk.sessions import Session
from google.adk.sessions import _session_util
from google.adk.sessions.base_session_service import GetSessionConfig
from google.adk.sessions.base_session_service import ListSessionsResponse
from google.adk.sessions.state import State
from pydantic import ValidationError
from redis import asyncio as aioredis
from typing_extensions import override

logger = logging.getLogger(__name__)


class RedisSessionService(BaseSessionService):
    """ADK 2.0 SessionService implementation using Redis.

    Stores session events, state, and user/app-scoped state in Redis so that
    agent conversations survive process restarts and can be shared across
    multiple worker instances.
    """

    APP_STATE_PREFIX = "adk:app_state"
    USER_STATE_PREFIX = "adk:user_state"
    SESSION_PREFIX = "adk:session"

    def __init__(self, redis_url: str):
        super().__init__()
        self.redis_url = redis_url
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                self.redis_url, decode_responses=True, encoding="utf-8"
            )
        return self._redis

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
            self._redis = None

    @property
    def client(self) -> aioredis.Redis:
        """Return the underlying async Redis client (initializing if needed)."""
        if self._redis is None:
            self._redis = aioredis.from_url(
                self.redis_url, decode_responses=True, encoding="utf-8"
            )
        return self._redis

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _app_state_key(self, app_name: str) -> str:
        return f"{self.APP_STATE_PREFIX}:{app_name}"

    def _user_state_key(self, app_name: str, user_id: str) -> str:
        return f"{self.USER_STATE_PREFIX}:{app_name}:{user_id}"

    def _session_key(self, app_name: str, user_id: str, session_id: str) -> str:
        return f"{self.SESSION_PREFIX}:{app_name}:{user_id}:{session_id}"

    def _session_pattern(self, app_name: str, user_id: str | None = None) -> str:
        if user_id:
            return f"{self.SESSION_PREFIX}:{app_name}:{user_id}:*"
        return f"{self.SESSION_PREFIX}:{app_name}:*"

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def _serialize_session(self, session: Session) -> str:
        # Events are stored as plain dicts so they can be rehydrated by Pydantic.
        return session.model_dump_json()

    def _deserialize_session(self, raw: str | None) -> Session | None:
        if not raw:
            return None
        try:
            return Session.model_validate_json(raw)
        except ValidationError as e:
            logger.error("Failed to deserialize session: %s", e)
            return None

    async def _load_app_state(self, app_name: str) -> dict[str, Any]:
        redis = await self._get_redis()
        raw = await redis.get(self._app_state_key(app_name))
        return json.loads(raw) if raw else {}

    async def _save_app_state(self, app_name: str, state: dict[str, Any]) -> None:
        redis = await self._get_redis()
        await redis.set(self._app_state_key(app_name), json.dumps(state))

    async def _load_user_state(self, app_name: str, user_id: str) -> dict[str, Any]:
        redis = await self._get_redis()
        raw = await redis.get(self._user_state_key(app_name, user_id))
        return json.loads(raw) if raw else {}

    async def _save_user_state(
        self, app_name: str, user_id: str, state: dict[str, Any]
    ) -> None:
        redis = await self._get_redis()
        await redis.set(
            self._user_state_key(app_name, user_id), json.dumps(state)
        )

    def _merge_state(
        self,
        session: Session,
        app_state: dict[str, Any],
        user_state: dict[str, Any],
    ) -> Session:
        """Merges app/user-scoped state into the session state with prefixes."""
        # Copy session so mutations do not leak into storage.
        merged = session.model_copy(deep=False)
        merged.events = list(session.events)
        merged.state = dict(session.state)

        for key, value in app_state.items():
            merged.state[State.APP_PREFIX + key] = value
        for key, value in user_state.items():
            merged.state[State.USER_PREFIX + key] = value

        return merged

    # ------------------------------------------------------------------
    # BaseSessionService overrides
    # ------------------------------------------------------------------

    @override
    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Session:
        redis = await self._get_redis()

        if session_id:
            existing = await redis.get(
                self._session_key(app_name, user_id, session_id)
            )
            if existing:
                raise ValueError(
                    f"Session with id {session_id} already exists."
                )

        state_deltas = _session_util.extract_state_delta(state)
        app_state_delta = state_deltas["app"]
        user_state_delta = state_deltas["user"]
        session_state = state_deltas["session"]

        if app_state_delta:
            app_state = await self._load_app_state(app_name)
            app_state.update(app_state_delta)
            await self._save_app_state(app_name, app_state)

        if user_state_delta:
            user_state = await self._load_user_state(app_name, user_id)
            user_state.update(user_state_delta)
            await self._save_user_state(app_name, user_id, user_state)

        session_id = (
            session_id.strip()
            if session_id and session_id.strip()
            else platform_uuid.new_uuid()
        )

        session = Session(
            app_name=app_name,
            user_id=user_id,
            id=session_id,
            state=session_state or {},
            last_update_time=platform_time.get_time(),
        )

        await redis.set(
            self._session_key(app_name, user_id, session_id),
            self._serialize_session(session),
        )

        return self._merge_state(
            session,
            await self._load_app_state(app_name),
            await self._load_user_state(app_name, user_id),
        )

    @override
    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: GetSessionConfig | None = None,
    ) -> Session | None:
        redis = await self._get_redis()
        raw = await redis.get(self._session_key(app_name, user_id, session_id))
        session = self._deserialize_session(raw)
        if session is None:
            return None

        if config:
            if config.num_recent_events is not None:
                if config.num_recent_events == 0:
                    session.events = []
                else:
                    session.events = session.events[-config.num_recent_events :]
            if config.after_timestamp:
                idx = len(session.events) - 1
                while idx >= 0 and session.events[idx].timestamp >= config.after_timestamp:
                    idx -= 1
                if idx >= 0:
                    session.events = session.events[idx + 1 :]

        return self._merge_state(
            session,
            await self._load_app_state(app_name),
            await self._load_user_state(app_name, user_id),
        )

    @override
    async def list_sessions(
        self, *, app_name: str, user_id: str | None = None
    ) -> ListSessionsResponse:
        redis = await self._get_redis()
        pattern = self._session_pattern(app_name, user_id)

        sessions = []
        async for key in redis.scan_iter(match=pattern):
            raw = await redis.get(key)
            session = self._deserialize_session(raw)
            if session is None:
                continue
            # Strip events for list response (matches in-memory behavior).
            session.events = []
            session = self._merge_state(
                session,
                await self._load_app_state(app_name),
                await self._load_user_state(app_name, session.user_id),
            )
            sessions.append(session)

        return ListSessionsResponse(sessions=sessions)

    @override
    async def delete_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        redis = await self._get_redis()
        await redis.delete(self._session_key(app_name, user_id, session_id))

    @override
    async def get_user_state(
        self, *, app_name: str, user_id: str
    ) -> dict[str, Any]:
        return await self._load_user_state(app_name, user_id)

    @override
    async def append_event(self, session: Session, event: Event) -> Event:
        if event.partial:
            return event

        # Apply state changes to the in-memory session object first.
        await super().append_event(session=session, event=event)
        session.last_update_time = event.timestamp

        # Persist any app/user/session state deltas.
        if event.actions and event.actions.state_delta:
            state_deltas = _session_util.extract_state_delta(
                event.actions.state_delta
            )
            app_state_delta = state_deltas["app"]
            user_state_delta = state_deltas["user"]
            session_state_delta = state_deltas["session"]

            if app_state_delta:
                app_state = await self._load_app_state(session.app_name)
                app_state.update(app_state_delta)
                await self._save_app_state(session.app_name, app_state)

            if user_state_delta:
                user_state = await self._load_user_state(
                    session.app_name, session.user_id
                )
                user_state.update(user_state_delta)
                await self._save_user_state(
                    session.app_name, session.user_id, user_state
                )

            if session_state_delta:
                session.state.update(session_state_delta)

        redis = await self._get_redis()
        await redis.set(
            self._session_key(session.app_name, session.user_id, session.id),
            self._serialize_session(session),
        )

        return event

    @override
    async def flush(self) -> None:
        pass
