"""FastAPI application exposing ADK 2.0 agents via A2A and direct HTTP endpoints."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
from google.adk.a2a import _compat
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from google.adk.events.event import Event
from google.genai import types
from pydantic import BaseModel

from .config import settings
from .runner import build_runner
from .runner import build_session_service
from .runner import create_user_session
from .runner import run_graph_agent
from .runner import run_team_agent

logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    """Direct run request payload."""

    user_id: str
    message: str
    session_id: str | None = None


class RunResponse(BaseModel):
    """Direct run response payload."""

    response: str
    session_id: str


def _last_text_from_events(events: list[Event]) -> str:
    """Extract the final assistant text from a list of ADK events."""
    for event in reversed(events):
        if not event.content or not event.content.parts:
            continue
        if event.content.role != "model":
            continue
        text = "".join(
            part.text or "" for part in event.content.parts if part.text
        )
        if text:
            return text
    return ""


async def _collect_events(agen: AsyncGenerator[Event, None]) -> list[Event]:
    """Drain an async event generator into a list."""
    events = []
    async for event in agen:
        events.append(event)
    return events


async def _run_and_respond(
    runner,
    user_id: str,
    session_id: str | None,
    message: str,
) -> dict[str, Any]:
    """Create/resume a session and return the final agent response."""
    if not session_id:
        session = await create_user_session(runner, user_id=user_id)
        session_id = session.id
    else:
        existing = await runner.session_service.get_session(
            app_name=runner.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if existing is None:
            session = await create_user_session(
                runner, user_id=user_id, session_id=session_id
            )
            session_id = session.id

    if runner.agent.name in ("team_coordinator",):
        agen = run_team_agent(runner, user_id, session_id, message)
    else:
        agen = run_graph_agent(runner, user_id, session_id, message)

    events = await _collect_events(agen)
    text = _last_text_from_events(events)
    return {"response": text, "session_id": session_id}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: build runners once and close Redis on shutdown."""
    session_service = build_session_service()
    app.state.session_service = session_service
    app.state.team_runner = build_runner(
        agent_type="team", session_service=session_service
    )
    app.state.graph_runner = build_runner(
        agent_type="graph", session_service=session_service
    )

    # Attach A2A routes for the team agent.
    task_store = InMemoryTaskStore()
    push_config_store = InMemoryPushNotificationConfigStore()
    agent_executor = A2aAgentExecutor(
        runner=lambda: app.state.team_runner,
    )
    agent_card = _compat.build_agent_card(
        name="adk-team-agent",
        description="A2A-enabled ADK 2.0 team agent with Redis session memory.",
        version="0.1.0",
        url=settings.A2A_AGENT_URL,
        protocol_binding="jsonrpc",
        default_input_modes=("text/plain",),
        default_output_modes=("text/plain",),
        streaming=True,
    )
    _compat.attach_a2a_routes_to_app(
        app,
        agent_card=agent_card,
        agent_executor=agent_executor,
        task_store=task_store,
        push_config_store=push_config_store,
        prefix="/a2a/team-agent",
    )
    app.state.task_store = task_store
    app.state.push_config_store = push_config_store

    yield

    await session_service.close()


app = FastAPI(
    title="A2A ADK 2.0 Agent",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.APP_NAME}


@app.post("/run/team", response_model=RunResponse)
async def run_team(request: Request, body: RunRequest) -> RunResponse:
    """Run the team coordinator agent directly via HTTP."""
    result = await _run_and_respond(
        request.app.state.team_runner,
        body.user_id,
        body.session_id,
        body.message,
    )
    return RunResponse(**result)


@app.post("/run/graph", response_model=RunResponse)
async def run_graph(request: Request, body: RunRequest) -> RunResponse:
    """Run the route-graph Workflow agent directly via HTTP."""
    result = await _run_and_respond(
        request.app.state.graph_runner,
        body.user_id,
        body.session_id,
        body.message,
    )
    return RunResponse(**result)


@app.get("/sessions/{user_id}")
async def list_sessions(request: Request, user_id: str) -> JSONResponse:
    """List sessions for a user from Redis."""
    response = await request.app.state.session_service.list_sessions(
        app_name=settings.APP_NAME, user_id=user_id
    )
    return JSONResponse(
        content={
            "sessions": [
                {"id": s.id, "user_id": s.user_id, "state": s.state}
                for s in response.sessions
            ]
        }
    )


def build_app() -> FastAPI:
    """Returns the configured FastAPI application."""
    return app
