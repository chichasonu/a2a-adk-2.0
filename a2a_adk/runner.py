"""Runner integration for ADK 2.0 agents backed by Redis session memory."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.apps.app import App
from google.adk.events.event import Event
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Runner
from google.adk.sessions import Session
from google.genai import types

from .agents import build_graph_agent
from .agents import build_greeting_agent
from .agents import build_math_agent
from .agents import build_mcp_agent
from .agents import build_orchestrator_agent
from .agents import build_team_agent
from .agents import build_weather_agent
from .config import settings
from .session_service import RedisSessionService

logger = logging.getLogger(__name__)


_AGENT_BUILDERS = {
    "team": build_team_agent,
    "graph": build_graph_agent,
    "greeting": build_greeting_agent,
    "weather": build_weather_agent,
    "math": build_math_agent,
    "mcp": build_mcp_agent,
    "orchestrator": build_orchestrator_agent,
}


def build_session_service() -> RedisSessionService:
    """Factory for the Redis-backed ADK SessionService."""
    return RedisSessionService(redis_url=settings.REDIS_URL)


async def build_runner(
    *,
    agent_type: str = "team",
    app_name: str | None = None,
    session_service: RedisSessionService | None = None,
    plugins: list[BasePlugin] | None = None,
) -> Runner:
    """Builds an ADK Runner wired to Redis session memory and optional plugins.

    Args:
        agent_type: One of ``team``, ``graph``, ``greeting``, ``weather``,
            ``math``, ``mcp`` or ``orchestrator``.
        app_name: Optional application name override.
        session_service: Optional RedisSessionService instance.
        plugins: Optional list of ADK plugins to attach to the app.
    """
    builder = _AGENT_BUILDERS.get(agent_type)
    if builder is None:
        raise ValueError(
            f"Unknown agent_type: {agent_type}. "
            f"Choose from {list(_AGENT_BUILDERS)}."
        )

    app_name = app_name or settings.APP_NAME
    session_service = session_service or build_session_service()
    agent = await builder()

    app = App(
        name=app_name,
        root_agent=agent,
        plugins=plugins or [],
    )

    return Runner(
        app=app,
        session_service=session_service,
    )


async def create_user_session(
    runner: Runner,
    user_id: str,
    session_id: str | None = None,
    state: dict[str, Any] | None = None,
) -> Session:
    """Creates a new session via the Runner's session service."""
    session = await runner.session_service.create_session(
        app_name=runner.app_name,
        user_id=user_id,
        session_id=session_id,
        state=state or {},
    )
    return session


async def run_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    message: str,
) -> AsyncGenerator[Event, None]:
    """Runs the runner's root agent with a user message."""
    new_message = types.Content(role="user", parts=[types.Part(text=message)])
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=new_message,
    ):
        yield event


async def run_team_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    message: str,
) -> AsyncGenerator[Event, None]:
    """Runs the team coordinator agent with a user message."""
    async for event in run_agent(runner, user_id, session_id, message):
        yield event


async def run_graph_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    message: str,
) -> AsyncGenerator[Event, None]:
    """Runs the route-graph Workflow agent with a user message."""
    async for event in run_agent(runner, user_id, session_id, message):
        yield event
