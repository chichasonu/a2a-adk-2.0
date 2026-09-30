"""FastAPI app exposing the ADK supervisor as a Server-Sent Events stream."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.events.event import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field

from setfit_router.agents import ROUTING_METADATA_KEY, Supervisor, build_supervisor
from setfit_router.config import Settings
from setfit_router.metrics import RoutingMetrics
from setfit_router.router import RouteDecision, Router, SetFitRouter
from setfit_router.schemas import ClassifyRequest, ClassifyResponse

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    user_id: str = "demo-user"
    session_id: str | None = None


class RouteRequest(BaseModel):
    message: str = Field(min_length=1)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _decision_from_metadata(meta: dict) -> RouteDecision:
    return RouteDecision(
        agent=meta["agent"],
        confidence=meta["confidence"],
        predicted_label=meta["predicted_label"],
        intent=meta.get("intent"),
        granularity=meta["granularity"],
        low_confidence=meta["low_confidence"],
        latency_ms=meta["latency_ms"],
    )


def _event_payloads(event: Event) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    meta = event.custom_metadata or {}
    if ROUTING_METADATA_KEY in meta:
        out.append(("route", meta[ROUTING_METADATA_KEY]))
    for call in [] if event.partial else event.get_function_calls():
        out.append(("tool_call", {"author": event.author, "name": call.name, "args": call.args}))
    for resp in event.get_function_responses():
        out.append(("tool_result", {"author": event.author, "name": resp.name, "response": resp.response}))
    text = "".join(
        p.text or "" for p in (event.content.parts if event.content else None) or [] if not p.thought
    )
    if text and event.author != "user":
        kind = "delta" if event.partial else "message"
        out.append((kind, {"author": event.author, "text": text}))
    return out


def create_app(
    settings: Settings | None = None,
    router: Router | None = None,
    model: str | BaseLlm | None = None,
    metrics: RoutingMetrics | None = None,
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active_router = router
        if active_router is None:
            if not settings.router_model_dir.exists():
                raise RuntimeError(
                    f"router model not found at {settings.router_model_dir}; "
                    "run `python training/train.py` or set ROUTER_MODEL_DIR"
                )
            loaded = SetFitRouter(settings.router_model_dir, settings.router_confidence_threshold)
            loaded.warmup()
            active_router = loaded
        supervisor: Supervisor = build_supervisor(
            active_router, model or settings.agent_model, settings.mcp_urls, settings.mcp_timeout_s
        )
        session_service = InMemorySessionService()
        app.state.router = active_router
        app.state.session_service = session_service
        app.state.runner = Runner(
            app_name=settings.app_name, agent=supervisor.workflow, session_service=session_service
        )
        app.state.metrics = metrics or RoutingMetrics(settings.routing_metrics_log, settings.routing_log_text)
        try:
            yield
        finally:
            await supervisor.close()

    app = FastAPI(title="ADK SetFit banking router", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/route")
    async def route(req: RouteRequest, request: Request) -> dict:
        return request.app.state.router.route(req.message).to_dict()

    @app.post("/classify", response_model=ClassifyResponse)
    async def classify(req: ClassifyRequest, request: Request) -> ClassifyResponse:
        """Classify one message: routed agent, BANKING77 intent, confidence and top-k candidates."""
        active: Router = request.app.state.router
        return ClassifyResponse.from_classification(active.classify(req.text, req.top_k))

    @app.get("/metrics")
    async def get_metrics(request: Request) -> dict:
        return request.app.state.metrics.summary()

    @app.post("/chat/stream")
    async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
        state = request.app.state
        session_service: InMemorySessionService = state.session_service
        session = None
        if req.session_id:
            session = await session_service.get_session(
                app_name=settings.app_name, user_id=req.user_id, session_id=req.session_id
            )
            if session is None:
                raise HTTPException(status_code=404, detail="unknown session_id")
        else:
            session = await session_service.create_session(app_name=settings.app_name, user_id=req.user_id)

        runner: Runner = state.runner
        routing_metrics: RoutingMetrics = state.metrics
        request_id = uuid.uuid4().hex

        async def stream() -> AsyncIterator[str]:
            start = time.perf_counter()
            decision: RouteDecision | None = None
            status = "ok"
            yield sse("session", {"session_id": session.id, "user_id": req.user_id, "request_id": request_id})
            try:
                async for event in runner.run_async(
                    user_id=req.user_id,
                    session_id=session.id,
                    new_message=types.Content(role="user", parts=[types.Part(text=req.message)]),
                    run_config=RunConfig(streaming_mode=StreamingMode.SSE),
                ):
                    for kind, payload in _event_payloads(event):
                        if kind == "route":
                            decision = _decision_from_metadata(payload)
                        yield sse(kind, payload)
            except Exception as exc:  # surfaced to the client as an SSE error event
                status = "error"
                logger.exception("supervisor run failed")
                yield sse("error", {"error": type(exc).__name__, "detail": str(exc)})
            total_ms = (time.perf_counter() - start) * 1000
            if decision is not None:
                routing_metrics.record(
                    decision,
                    request_id=request_id,
                    session_id=session.id,
                    user_id=req.user_id,
                    text=req.message,
                    status=status,
                    total_latency_ms=total_ms,
                )
            yield sse(
                "done", {"request_id": request_id, "status": status, "total_latency_ms": round(total_ms, 1)}
            )

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
