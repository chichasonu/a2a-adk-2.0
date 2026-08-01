"""FastAPI application exposing ADK 2.0 agents via A2A and direct HTTP endpoints."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Path
from fastapi import Query
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from google.adk.a2a import _compat
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from google.adk.events.event import Event
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel
from pydantic import Field
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import APIKeyMiddleware
from .callbacks import RedisCallbackPlugin
from .config import settings
from .mcp_tools import mcp_tool_cache
from .runner import build_runner
from .runner import build_session_service
from .runner import create_user_session
from .runner import run_agent
from .tool_cache import tool_cache

logger = logging.getLogger(__name__)


# Every agent that gets its own runner, HTTP endpoint and A2A endpoint.
_AGENT_TYPES = ["team", "graph", "greeting", "weather", "math", "mcp", "orchestrator"]
_AGENT_TYPE_PATTERN = "^(" + "|".join(_AGENT_TYPES) + ")$"


def _a2a_prefix(agent_type: str) -> str:
    """A2A route prefix for an agent."""
    return f"/a2a/{agent_type}-agent"


class RunRequest(BaseModel):
    """Direct run request payload."""

    user_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    session_id: str | None = None


class RunResponse(BaseModel):
    """Direct run response payload."""

    response: str
    session_id: str


class ErrorResponse(BaseModel):
    """Structured error response payload."""

    error: str
    detail: str
    session_id: str | None = None


def _to_json(obj: Any) -> str:
    """Serialize a value to a compact JSON string."""
    return json.dumps(obj, default=str, ensure_ascii=False)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request with timing and attaches an X-Request-ID header."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        logger.info(
            "Request started method=%s path=%s request_id=%s",
            request.method,
            request.url.path,
            request_id,
        )
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "Request failed method=%s path=%s request_id=%s",
                request.method,
                request.url.path,
                request_id,
            )
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Request completed method=%s path=%s status=%s duration_ms=%.2f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        response.headers["X-Request-ID"] = request_id
        return response


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


async def _run_agent(
    runner,
    user_id: str,
    session_id: str,
    message: str,
) -> AsyncGenerator[Event, None]:
    """Run a runner's root agent and yield ADK events."""
    new_message = types.Content(role="user", parts=[types.Part(text=message)])
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=new_message,
    ):
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
    request: Request | None = None,
) -> dict[str, Any]:
    """Create/resume a session and return the final agent response."""
    resolved_session_id = await _ensure_session(runner, user_id, session_id)
    if request is not None:
        request.state.session_id = resolved_session_id

    events = await _collect_events(
        _run_agent(runner, user_id, resolved_session_id, message)
    )
    text = _last_text_from_events(events)
    return {"response": text, "session_id": resolved_session_id}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: build all runners, attach A2A routes and close on shutdown."""
    # Warm the tool caches so declarations are persisted and reused across agents.
    await tool_cache.initialize()
    await mcp_tool_cache.discover()

    # Separate session service for the HTTP listing/query endpoints.
    session_service = build_session_service()
    app.state.session_service = session_service

    base_url = settings.A2A_BASE_URL.rstrip("/")

    for agent_type in _AGENT_TYPES:
        # Each runner gets its own plugin so metrics/events are tagged per agent.
        plugin = RedisCallbackPlugin(settings.REDIS_URL)
        setattr(app.state, f"{agent_type}_plugin", plugin)

        runner = await build_runner(agent_type=agent_type, plugins=[plugin])
        setattr(app.state, f"{agent_type}_runner", runner)

        # Expose the agent over A2A under its own prefix.
        prefix = _a2a_prefix(agent_type)
        task_store = InMemoryTaskStore()
        push_config_store = InMemoryPushNotificationConfigStore()
        setattr(app.state, f"{agent_type}_task_store", task_store)
        setattr(app.state, f"{agent_type}_push_config_store", push_config_store)

        agent_executor = A2aAgentExecutor(
            runner=lambda attr=f"{agent_type}_runner": getattr(app.state, attr),
        )
        agent_card = _compat.build_agent_card(
            name=f"adk-{agent_type}-agent",
            description=f"A2A-enabled ADK 2.0 {agent_type} agent.",
            version="0.1.0",
            url=f"{base_url}{prefix}",
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
            prefix=prefix,
        )

    yield

    for agent_type in _AGENT_TYPES:
        runner = getattr(app.state, f"{agent_type}_runner", None)
        if runner:
            await runner.close()
    await session_service.close()


app = FastAPI(
    title="A2A ADK 2.0 Agent",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(APIKeyMiddleware)


def _request_session_id(request: Request) -> str | None:
    return getattr(request.state, "session_id", None)


@app.exception_handler(RequestValidationError)
async def _handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return a structured 422 for invalid request payloads."""
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error="ValidationError",
            detail=json.dumps(exc.errors()),
            session_id=_request_session_id(request),
        ).model_dump(),
    )


@app.exception_handler(genai_errors.APIError)
async def _handle_google_api_error(request: Request, exc: genai_errors.APIError) -> JSONResponse:
    """Return a 502 when the underlying LLM API call fails."""
    logger.exception("Google API error")
    return JSONResponse(
        status_code=502,
        content=ErrorResponse(
            error="GoogleAPIError",
            detail=str(exc),
            session_id=_request_session_id(request),
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def _handle_generic_error(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler returning a structured 500 error."""
    logger.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error=type(exc).__name__,
            detail=str(exc),
            session_id=_request_session_id(request),
        ).model_dump(),
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.APP_NAME}


@app.get("/health/ready")
async def health_ready(request: Request) -> dict[str, Any]:
    """Readiness probe that verifies the Redis session service is reachable."""
    try:
        redis = request.app.state.session_service.client
        await redis.ping()
        return {"status": "ready", "redis": "ok"}
    except Exception as exc:
        logger.exception("Readiness check failed")
        raise HTTPException(status_code=503, detail=f"not ready: {exc}")


@app.get("/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    """Return basic agent run metrics stored in Redis."""
    redis = request.app.state.session_service.client
    prefix = f"adk:metrics:{settings.APP_NAME}"
    keys = [f"{prefix}:{m}" for m in ("runs", "errors", "events", "tool_calls")]
    values = await redis.mget(keys)
    return {
        "runs_total": int(values[0] or 0),
        "errors_total": int(values[1] or 0),
        "events_total": int(values[2] or 0),
        "tool_calls_total": int(values[3] or 0),
    }


@app.get("/agents")
async def list_agents() -> dict[str, Any]:
    """List the available agents and their A2A endpoints."""
    base_url = settings.A2A_BASE_URL.rstrip("/")
    return {
        "agents": [
            {
                "agent_type": agent_type,
                "run_url": f"/run/{agent_type}",
                "invoke_url": f"/invoke/{agent_type}",
                "a2a_card_url": f"{base_url}{_a2a_prefix(agent_type)}/.well-known/agent-card.json",
            }
            for agent_type in _AGENT_TYPES
        ]
    }


@app.post("/run/{agent_type}", response_model=RunResponse)
async def run_agent_endpoint(
    request: Request,
    agent_type: str = Path(..., pattern=_AGENT_TYPE_PATTERN),
    body: RunRequest = ...,  # noqa: B008
) -> RunResponse:
    """Run any configured root agent directly via HTTP."""
    runner = getattr(request.app.state, f"{agent_type}_runner")
    request.state.session_id = body.session_id
    result = await _run_and_respond(
        runner,
        body.user_id,
        body.session_id,
        body.message,
        request=request,
    )
    return RunResponse(**result)


@app.post("/invoke/{agent_type}", response_model=None)
async def invoke_agent(
    request: Request,
    agent_type: str = Path(..., pattern=_AGENT_TYPE_PATTERN),
    body: RunRequest = ...,  # noqa: B008
    stream: bool = Query(False, description="Stream events via Server-Sent Events"),
) -> RunResponse | StreamingResponse:
    """Generic HTTP endpoint to invoke any agent.

    Set ``stream=true`` to receive ADK events as a ``text/event-stream``
    (SSE) response.
    """
    runner = getattr(request.app.state, f"{agent_type}_runner")

    if stream:

        async def event_stream() -> AsyncGenerator[str, None]:
            session_id = await _ensure_session(
                runner, body.user_id, body.session_id
            )
            request.state.session_id = session_id
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

    request.state.session_id = body.session_id
    result = await _run_and_respond(
        runner,
        body.user_id,
        body.session_id,
        body.message,
        request=request,
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


@app.get("/tools")
async def list_tools() -> dict[str, Any]:
    """List local and MCP tool declarations currently cached in Redis."""
    local = await tool_cache.list_cached()
    mcp = await mcp_tool_cache.list_cached()
    return {"local": local, "mcp": mcp, "count": len(local) + len(mcp)}


@app.post("/refresh-tools")
async def refresh_tools(
    request: Request,
    name: str | None = Query(None, description="Optional tool name to refresh"),
) -> dict[str, Any]:
    """Invalidate and rebuild cached tool declarations and all agent runners.

    Call this endpoint after adding or changing a tool in ``a2a_adk/tools.py``
    or the remote Spring MCP server. The endpoint re-fetches MCP tools,
    rebuilds the cached declarations, and reconstructs every ADK runner so the
    agents pick up new, changed or removed tools without a server restart.
    """
    refreshed_local = await tool_cache.refresh(name)
    refreshed_mcp = await mcp_tool_cache.refresh()

    # Rebuild every runner so all LlmAgents receive the refreshed tool lists.
    for agent_type in _AGENT_TYPES:
        old_runner = getattr(request.app.state, f"{agent_type}_runner")
        await old_runner.close()
        plugin = getattr(request.app.state, f"{agent_type}_plugin")
        request.app.state[f"{agent_type}_runner"] = await build_runner(
            agent_type=agent_type,
            plugins=[plugin],
        )

    return {
        "local": {"refreshed": refreshed_local, "count": len(refreshed_local)},
        "mcp": {"refreshed": refreshed_mcp, "count": len(refreshed_mcp)},
    }


def build_app() -> FastAPI:
    """Returns the configured FastAPI application."""
    return app
