"""FastAPI application exposing ADK 2.0 agents via A2A and direct HTTP endpoints."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI
from fastapi import Path
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from google.adk.a2a import _compat
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from google.adk.events.event import Event
from google.genai import types
from pydantic import BaseModel

from .callbacks import RedisCallbackPlugin
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


def _to_json(obj: Any) -> str:
    """Serialize a value to a compact JSON string."""
    return json.dumps(obj, default=str, ensure_ascii=False)


def _event_to_json(event: Event) -> str:
    """Serialize an ADK event to a compact JSON string."""
    try:
        data = event.model_dump(mode="json", exclude_none=True, by_alias=False)
    except Exception:
        data = {"output": str(event.output), "content": str(event.content)}
    return _to_json(data)


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


async def _ensure_session(
    runner,
    user_id: str,
    session_id: str | None,
) -> str:
    """Return an existing session id or create a new one."""
    if not session_id:
        session = await create_user_session(runner, user_id=user_id)
        return session.id

    existing = await runner.session_service.get_session(
        app_name=runner.app_name,
        user_id=user_id,
        session_id=session_id,
    )
    if existing is None:
        session = await create_user_session(
            runner, user_id=user_id, session_id=session_id
        )
        return session.id
    return session_id


async def _run_agent(runner, user_id: str, session_id: str, message: str):
    """Run an agent and yield ADK events for an already-resolved session."""
    if runner.agent.name == "team_coordinator":
        agen = run_team_agent(runner, user_id, session_id, message)
    else:
        agen = run_graph_agent(runner, user_id, session_id, message)

    async for event in agen:
        yield event


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
    session_id = await _ensure_session(runner, user_id, session_id)

    if runner.agent.name == "team_coordinator":
        agen = run_team_agent(runner, user_id, session_id, message)
    else:
        agen = run_graph_agent(runner, user_id, session_id, message)

    events = await _collect_events(agen)
    text = _last_text_from_events(events)
    return {"response": text, "session_id": session_id}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: build runners once and close Redis on shutdown."""
    # Separate session service for the HTTP listing/query endpoints.
    session_service = build_session_service()
    app.state.session_service = session_service

    # Each runner owns its own session service so Runner.close() is safe.
    app.state.team_plugin = RedisCallbackPlugin(settings.REDIS_URL)
    app.state.graph_plugin = RedisCallbackPlugin(settings.REDIS_URL)
    app.state.team_runner = build_runner(
        agent_type="team",
        plugins=[app.state.team_plugin],
    )
    app.state.graph_runner = build_runner(
        agent_type="graph",
        plugins=[app.state.graph_plugin],
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

    await app.state.team_runner.close()
    await app.state.graph_runner.close()
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


@app.post("/invoke/{agent_type}", response_model=None)
async def invoke_agent(
    request: Request,
    agent_type: str = Path(..., pattern="^(team|graph)$"),
    body: RunRequest = ...,  # noqa: B008
    stream: bool = Query(False, description="Stream events via Server-Sent Events"),
) -> RunResponse | StreamingResponse:
    """Generic HTTP endpoint to invoke the team or graph agent.

    Set ``stream=true`` to receive ADK events as a ``text/event-stream``
    (SSE) response.
    """
    runner = (
        request.app.state.team_runner
        if agent_type == "team"
        else request.app.state.graph_runner
    )

    if stream:

        async def event_stream() -> AsyncGenerator[str, None]:
            session_id = await _ensure_session(
                runner, body.user_id, body.session_id
            )
            yield f"data: {_to_json({'type': 'session', 'session_id': session_id})}\n\n"
            try:
                async for event in _run_agent(
                    runner, body.user_id, session_id, body.message
                ):
                    data = _event_to_json(event)
                    yield f"data: {data}\n\n"
            except Exception as exc:  # noqa: BLE001
                logger.exception("Streaming agent run failed")
                yield f"data: {_to_json({'type': 'error', 'error': str(exc)})}\n\n"
            yield f"data: {_to_json({'type': 'done'})}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
        )

    result = await _run_and_respond(
        runner,
        body.user_id,
        body.session_id,
        body.message,
    )
    return RunResponse(**result)


@app.get("/events/{user_id}/{session_id}")
async def read_event_stream(
    request: Request,
    user_id: str,
    session_id: str,
    count: int = Query(100, ge=1, le=1000),
) -> JSONResponse:
    """Read the Redis stream of callback/model/tool events for a session."""
    redis = request.app.state.session_service.client
    key = f"adk:events:{settings.APP_NAME}:{user_id}:{session_id}"
    entries = await redis.xrevrange(key, count=count)
    results = []
    for entry_id, fields in entries:
        payload = fields.get("payload")
        try:
            payload = json.loads(payload)
        except Exception:
            pass
        results.append(
            {
                "id": entry_id,
                "type": fields.get("type"),
                "payload": payload,
            }
        )
    return JSONResponse(content={"stream": key, "entries": results})


@app.get("/context/{user_id}/{session_id}")
async def read_context(
    request: Request,
    user_id: str,
    session_id: str,
) -> JSONResponse:
    """Read the latest persisted context snapshot from Redis."""
    redis = request.app.state.session_service.client
    key = f"adk:context:{settings.APP_NAME}:{user_id}:{session_id}"
    raw = await redis.hgetall(key)
    context = {}
    for k, v in raw.items():
        try:
            context[k] = json.loads(v)
        except Exception:
            context[k] = v
    return JSONResponse(content={"key": key, "context": context})


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
