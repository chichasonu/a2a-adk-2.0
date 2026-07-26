"""ADK 2.0 plugin that persists callbacks and events to Redis streams."""

from __future__ import annotations

import json
import logging
from typing import Any

from google.adk.events.event import Event
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from pydantic import BaseModel
from redis import asyncio as aioredis

from .redis_client import create_redis_client

logger = logging.getLogger(__name__)


def _to_serializable(obj: Any) -> Any:
    """Convert a Pydantic model or generic object to a JSON-serializable value."""
    try:
        if isinstance(obj, BaseModel):
            return obj.model_dump(mode="json", exclude_none=True, by_alias=False)
        if isinstance(obj, dict):
            return {k: _to_serializable(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_to_serializable(v) for v in obj]
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        return str(obj)
    except Exception:
        return str(obj)


def _to_json(obj: Any) -> str:
    """Serialize an object to a JSON string."""
    return json.dumps(_to_serializable(obj), default=str, ensure_ascii=False)


def _node_name(callback_context) -> str | None:
    """Extract the current node/agent name from a context."""
    node = getattr(callback_context, "node", None)
    if node:
        return getattr(node, "name", None)
    return None


class RedisCallbackPlugin(BasePlugin):
    """Plugin that logs before/after tool and model callbacks and events to Redis.

    It writes every significant callback and ADK event to a per-session Redis
    stream (``adk:events:{app}:{user}:{session}``) and keeps the latest context
    snapshot in a Redis hash (``adk:context:{app}:{user}:{session}``).
    """

    def __init__(self, redis_url: str):
        super().__init__(name="redis_callback_plugin")
        self.redis_url = redis_url
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = create_redis_client(self.redis_url)
        return self._redis

    def _event_stream_key(self, invocation_context) -> str:
        return (
            f"adk:events:{invocation_context.app_name}:"
            f"{invocation_context.user_id}:{invocation_context.session.id}"
        )

    def _context_hash_key(self, invocation_context) -> str:
        return (
            f"adk:context:{invocation_context.app_name}:"
            f"{invocation_context.user_id}:{invocation_context.session.id}"
        )

    def _metric_key(self, invocation_context, metric: str) -> str:
        return f"adk:metrics:{invocation_context.app_name}:{metric}"

    async def _incr_metric(self, invocation_context, metric: str) -> None:
        """Increment an observability counter in Redis."""
        try:
            redis = await self._get_redis()
            await redis.incr(self._metric_key(invocation_context, metric))
        except Exception:
            logger.exception("Failed to increment metric %s", metric)

    async def _xadd(
        self,
        invocation_context,
        event_type: str,
        payload: Any,
    ) -> None:
        """Append a callback/execution record to the per-session Redis stream."""
        try:
            redis = await self._get_redis()
            key = self._event_stream_key(invocation_context)
            await redis.xadd(
                key,
                {
                    "type": event_type,
                    "payload": _to_json(payload),
                },
            )
        except Exception:
            logger.exception("Failed to write %s event to Redis stream", event_type)

    async def _snapshot_context(self, callback_context) -> None:
        """Persist the latest context state to a Redis hash."""
        try:
            invocation_context = callback_context.get_invocation_context()
            redis = await self._get_redis()
            state = callback_context.state.to_dict() if callback_context.state else {}
            snapshot = {
                "app_name": invocation_context.app_name,
                "user_id": invocation_context.user_id,
                "session_id": invocation_context.session.id,
                "state": _to_json(state),
                "agent_name": _to_json(_node_name(callback_context)),
            }
            if invocation_context.user_content:
                snapshot["user_content"] = _to_json(invocation_context.user_content)
            await redis.hset(self._context_hash_key(invocation_context), mapping=snapshot)
        except Exception:
            logger.exception("Failed to snapshot context to Redis")

    async def before_run_callback(self, *, invocation_context) -> None:
        await self._incr_metric(invocation_context, "runs")
        await self._xadd(
            invocation_context,
            "run_start",
            {
                "app_name": invocation_context.app_name,
                "user_id": invocation_context.user_id,
                "session_id": invocation_context.session.id,
                "user_content": invocation_context.user_content,
            },
        )

    async def after_run_callback(self, *, invocation_context) -> None:
        await self._xadd(
            invocation_context,
            "run_end",
            {
                "app_name": invocation_context.app_name,
                "user_id": invocation_context.user_id,
                "session_id": invocation_context.session.id,
            },
        )

    async def before_model_callback(
        self, *, callback_context, llm_request: LlmRequest
    ) -> None:
        await self._snapshot_context(callback_context)
        await self._xadd(
            callback_context.get_invocation_context(),
            "before_model",
            {
                "agent_name": _node_name(callback_context),
                "llm_request": llm_request,
            },
        )

    async def after_model_callback(
        self, *, callback_context, llm_response: LlmResponse
    ) -> None:
        await self._snapshot_context(callback_context)
        await self._xadd(
            callback_context.get_invocation_context(),
            "after_model",
            {
                "agent_name": _node_name(callback_context),
                "llm_response": llm_response,
            },
        )

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> None:
        invocation_context = tool_context.get_invocation_context()
        await self._incr_metric(invocation_context, "tool_calls")
        await self._xadd(
            invocation_context,
            "before_tool",
            {
                "agent_name": _node_name(tool_context),
                "tool_name": getattr(tool, "name", str(tool)),
                "tool_args": tool_args,
            },
        )

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> None:
        invocation_context = tool_context.get_invocation_context()
        await self._xadd(
            invocation_context,
            "after_tool",
            {
                "agent_name": _node_name(tool_context),
                "tool_name": getattr(tool, "name", str(tool)),
                "tool_args": tool_args,
                "result": result,
            },
        )

    async def on_event_callback(
        self, *, invocation_context, event: Event
    ) -> None:
        await self._incr_metric(invocation_context, "events")
        await self._xadd(invocation_context, "event", event)

    async def on_run_error_callback(
        self, *, invocation_context, error: Exception
    ) -> None:
        await self._incr_metric(invocation_context, "errors")
        await self._xadd(
            invocation_context,
            "run_error",
            {"error": str(error), "error_type": type(error).__name__},
        )

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.close()
            self._redis = None
